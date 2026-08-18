from datetime import datetime
from io import BytesIO
import json
import re
from time import perf_counter
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func
from sqlmodel import Session, select
from .ai import AIServiceError, build_evidence_pack, generate_draft, generate_selection_criteria_bundle, match_evidence_batch, repair_cover_letter, repair_selection_criteria_bundle, repair_tailored_resume
from .application_requirements import confirm_application_requirements, correct_application_requirements, load_application_requirements, parse_application_requirements, requirements_source_changed
from .applicant_profile import applicant_profile_prompt
from .generation_trace import build_generation_trace, build_trace_bundle
from .auth import get_current_user
from .backup import create_backup, list_backups, read_backup, restore_backup
from .ckb import build_career_knowledge_base, validate_career_knowledge_base
from .config import settings
from .cover_letter_plan import build_cover_letter_plan, selected_cover_letter_evidence_ids
from .database import create_db_and_tables, get_session
from .exporter import create_docx, create_pdf, safe_filename
from .feature_flags import GENERATION_FEATURES, generation_feature_status
from .ingest import expand_abbreviated_company, extract_resume_experiences, extract_resume_text, import_job_url, parse_job_ad_text
from .job_model import build_job_model, validate_job_model
from .models import ApplicantProfile, ApplicantProfilePayload, ApplicantProfileResponse, ApplicationRequirementsResponse, ApplicationRequirementsUpdate, CreditLedger, GeneratedDocument, GeneratedDocumentUpdate, GenerationUsage, GenerateRequest, JobAdParseRequest, JobAdParseResponse, JobApplication, JobApplicationCreate, JobApplicationStatusUpdate, JobApplicationSubmissionUpdate, JobApplicationUpdate, JobUrlImportRequest, JobUrlImportResponse, QualityCheckIssue, QualityCheckResponse, Referee, Referral, ReferralClaimRequest, RestoreBackupRequest, Resume, ResumeContentCheckItem, ResumeContentCheckResponse, ResumeCreate, ResumeUpdate, SelectionCriteriaAccessResponse, SelectionCriteriaConfirmationRequest
from .quality import find_writing_quality_issues
from .resume_plan import build_resume_curation_plan, selected_resume_evidence_ids, validate_resume_content
from .selection_logic import build_selection_plan, criteria_requiring_confirmation

app = FastAPI(title="Job Application Assistant API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()


@app.get("/health")
def health():
    provider = settings.ai_provider.strip().lower()
    provider_configured = {
        "openai": bool(settings.openai_api_key),
        "deepseek": bool(settings.deepseek_api_key),
    }.get(provider, False)
    return {
        "status": "ok",
        "deployment_mode": settings.deployment_mode,
        "ai_provider": provider,
        "ai_configured": provider_configured,
        "deepseek_fallback_ready": bool(
            provider == "openai"
            and settings.ai_fallback_to_deepseek
            and settings.deepseek_api_key
        ),
        "generation_features": {
            document_type: bool(getattr(settings, setting))
            for document_type, setting in GENERATION_FEATURES.items()
        },
    }


def select_for_user(model, user_id: UUID | None):
    statement = select(model)
    return statement.where(model.user_id == user_id) if user_id is not None else statement


def serialise_ckb(source_text: str, experiences_json: str) -> str:
    ckb = build_career_knowledge_base(source_text, experiences_json)
    errors = validate_career_knowledge_base(ckb)
    if errors:
        raise HTTPException(400, errors[0])
    return json.dumps(ckb, ensure_ascii=False)


def serialise_job_model(job_description: str, selection_criteria: str | None, position_title: str, company: str) -> str:
    model = build_job_model(job_description, selection_criteria, position_title, company)
    errors = validate_job_model(model)
    if errors:
        raise HTTPException(400, errors[0])
    return json.dumps(model, ensure_ascii=False)


def invalidate_evidence_matches(session: Session, user_id: UUID | None) -> None:
    for application in session.exec(select_for_user(JobApplication, user_id)).all():
        application.evidence_matches_json = "{}"
        application.selection_plan_json = "{}"
        application.selection_confirmations_json = "[]"
        session.add(application)
    session.commit()


def _content_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def _resume_value_is_supported(value: str, source_text: str) -> bool:
    value_words = _content_words(value)
    source_words = _content_words(source_text)
    if not value_words:
        return False
    compact_value = "".join(value_words)
    compact_source = "".join(source_words)
    if compact_value in compact_source:
        return True
    source_set = set(source_words)
    meaningful = [word for word in value_words if len(word) > 2]
    return bool(meaningful) and sum(word in source_set for word in meaningful) / len(meaningful) >= 0.8


def build_resume_content_check(
    resume: Resume,
    profile: ApplicantProfile | None,
) -> ResumeContentCheckResponse:
    items: list[ResumeContentCheckItem] = []

    def add(field: str, label: str, value: str) -> None:
        cleaned = (value or "").strip()
        if not cleaned:
            status, message = "missing", "No information was extracted. Add it if it appears in your CV."
        elif _resume_value_is_supported(cleaned, resume.source_text):
            status, message = "matched", "Found in the uploaded CV."
        else:
            status, message = "review", "Not found as written in the uploaded CV. Confirm or correct it."
        items.append(ResumeContentCheckItem(field=field, label=label, value=cleaned, status=status, message=message))

    add("profile.full_name", "Full name", f"{profile.first_name} {profile.last_name}" if profile else "")
    add("profile.phone", "Phone", profile.phone if profile else "")
    add("profile.email", "Email", profile.email if profile else "")
    try:
        experiences = json.loads(resume.experiences_json or "[]")
    except (TypeError, json.JSONDecodeError):
        experiences = []
    if not experiences:
        items.append(ResumeContentCheckItem(
            field="experiences", label="Structured experience", value="", status="missing",
            message="No work experience was extracted. Review the CV text and add experience manually if needed.",
        ))
    for index, experience in enumerate(experiences, start=1):
        prefix = f"experiences.{index}"
        add(f"{prefix}.role_title", f"Experience {index} — role title", str(experience.get("role_title") or ""))
        add(f"{prefix}.organization", f"Experience {index} — organisation", str(experience.get("organization") or ""))
        add(f"{prefix}.responsibility", f"Experience {index} — responsibilities", str(experience.get("responsibility") or ""))
        result = str(experience.get("result") or "")
        if result:
            add(f"{prefix}.result", f"Experience {index} — result", result)
    matched_count = sum(item.status == "matched" for item in items)
    review_count = sum(item.status == "review" for item in items)
    missing_count = sum(item.status == "missing" for item in items)
    return ResumeContentCheckResponse(
        ready=review_count == 0 and missing_count == 0,
        matched_count=matched_count,
        review_count=review_count,
        missing_count=missing_count,
        items=items,
    )


def get_for_user(session: Session, model, record_id: int, user_id: UUID | None):
    record = session.get(model, record_id)
    if not record or (user_id is not None and record.user_id != user_id):
        return None
    return record


def require_local_mode() -> None:
    if settings.deployment_mode.strip().lower() == "online":
        raise HTTPException(404, "Local backup tools are not available in the online beta.")


def check_generation_quota(session: Session, user_id: UUID | None, pack_id: UUID | None) -> bool:
    """Return True when a new online pack usage record must be created."""
    if settings.deployment_mode.strip().lower() != "online":
        return False
    if user_id is None or pack_id is None:
        raise HTTPException(400, "A generation pack identifier is required in online mode.")
    existing = session.exec(
        select(GenerationUsage).where(
            GenerationUsage.user_id == user_id,
            GenerationUsage.pack_id == pack_id,
        )
    ).first()
    if existing:
        return False

    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    start_of_month = datetime(now.year, now.month, 1)
    daily_count = session.exec(
        select(func.count(GenerationUsage.id)).where(
            GenerationUsage.user_id == user_id,
            GenerationUsage.completed_at >= start_of_day,
        )
    ).one()
    global_monthly_count = session.exec(
        select(func.count(GenerationUsage.id)).where(
            GenerationUsage.completed_at >= start_of_month,
        )
    ).one()
    if daily_count >= settings.daily_pack_limit_per_user:
        raise HTTPException(429, "Today's beta limit has been reached. Please try again tomorrow.")
    if global_monthly_count >= settings.monthly_pack_limit_global:
        raise HTTPException(429, "The beta's monthly AI limit has been reached. Generation is paused.")
    return True


def selection_criteria_access(session: Session, user_id: UUID | None) -> SelectionCriteriaAccessResponse:
    if settings.deployment_mode.strip().lower() != "online":
        return SelectionCriteriaAccessResponse(unlimited=True)
    if user_id is None:
        raise HTTPException(401, "Sign in to use Selection Criteria.")
    ledger_total = session.exec(
        select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(CreditLedger.user_id == user_id)
    ).one()
    referral_credits = session.exec(
        select(func.count(Referral.id)).where(
            Referral.inviter_user_id == user_id,
            Referral.status == "earned",
        )
    ).one()
    used_credits = session.exec(
        select(func.count(CreditLedger.id)).where(
            CreditLedger.user_id == user_id,
            CreditLedger.reason == "generation",
        )
    ).one()
    referral_claimed = session.exec(
        select(Referral.id).where(Referral.invited_user_id == user_id)
    ).first() is not None
    return SelectionCriteriaAccessResponse(
        included_credits=2,
        referral_credits=referral_credits,
        used_credits=used_credits,
        remaining_credits=max(0, 2 + int(ledger_total)),
        referral_code=str(user_id),
        referral_claimed=referral_claimed,
    )


def check_selection_criteria_credit(
    session: Session,
    user_id: UUID | None,
    pack_id: UUID | None,
) -> str | None:
    if settings.deployment_mode.strip().lower() != "online":
        return None
    if user_id is None or pack_id is None:
        raise HTTPException(400, "A generation pack identifier is required in online mode.")
    idempotency_key = f"selection-criteria:{user_id}:{pack_id}"
    if session.exec(select(CreditLedger.id).where(CreditLedger.idempotency_key == idempotency_key)).first():
        return None
    access = selection_criteria_access(session, user_id)
    if not access.remaining_credits:
        raise HTTPException(429, "No Selection Criteria credits remain. Invite a new user to earn one more use.")
    return idempotency_key


@app.get("/selection-criteria/access", response_model=SelectionCriteriaAccessResponse)
def get_selection_criteria_access(
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    return selection_criteria_access(session, user_id)


@app.post("/selection-criteria/referral", response_model=SelectionCriteriaAccessResponse)
def claim_selection_criteria_referral(
    payload: ReferralClaimRequest,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    if settings.deployment_mode.strip().lower() != "online" or user_id is None:
        raise HTTPException(400, "Referral credits are available in the online service only.")
    try:
        inviter_id = UUID(payload.referral_code.strip())
    except ValueError as error:
        raise HTTPException(400, "This referral code is not valid.") from error
    if inviter_id == user_id:
        raise HTTPException(400, "You cannot use your own referral code.")
    if session.exec(select(Referral.id).where(Referral.invited_user_id == user_id)).first():
        raise HTTPException(409, "A referral has already been claimed for this account.")
    session.add(Referral(
        inviter_user_id=inviter_id,
        invited_user_id=user_id,
        status="earned",
        reward_credits=1,
        earned_at=datetime.utcnow(),
    ))
    session.add(CreditLedger(
        user_id=inviter_id,
        delta=1,
        reason="referral",
        reference_id=str(user_id),
        idempotency_key=f"referral:{user_id}",
    ))
    session.commit()
    return selection_criteria_access(session, user_id)


@app.get("/backups")
def get_backups():
    require_local_mode()
    return list_backups()


@app.post("/backups")
def make_backup(session: Session = Depends(get_session)):
    require_local_mode()
    return create_backup(session)


@app.get("/backups/{filename}/download")
def download_backup(filename: str):
    require_local_mode()
    try:
        path, _ = read_backup(filename)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found.")
    except ValueError as error:
        raise HTTPException(400, str(error))
    return Response(
        path.read_bytes(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@app.post("/backups/{filename}/restore")
def restore_saved_backup(filename: str, payload: RestoreBackupRequest, session: Session = Depends(get_session)):
    require_local_mode()
    if not payload.confirm:
        raise HTTPException(400, "Explicit confirmation is required before restoring a backup.")
    create_backup(session)
    try:
        return restore_backup(session, filename)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found.")
    except ValueError as error:
        raise HTTPException(400, str(error))


def profile_response(profile: ApplicantProfile, referees: list[Referee]) -> ApplicantProfileResponse:
    values = profile.model_dump(exclude={"created_at", "user_id"})
    values["referees"] = [
        referee.model_dump(exclude={"id", "profile_id", "display_order"})
        for referee in referees
    ]
    return ApplicantProfileResponse.model_validate(values)


def organisation_is_named(company: str, content: str) -> bool:
    stop_words = {"pty", "ltd", "limited", "inc", "incorporated", "the", "of", "and", "at"}
    company_words = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", company)]
    core_words = [word for word in company_words if word not in stop_words]
    content_words = " ".join(re.findall(r"[A-Za-z0-9]+", content.lower()))
    core_name = " ".join(core_words)
    acronym = "".join(word[0] for word in core_words if word)
    return bool(
        core_name
        and (
            core_name in content_words
            or (len(acronym) >= 3 and acronym.upper() in content.upper())
        )
    )


def _identity_tokens(value: str) -> list[str]:
    replacements = {"wa": ("western", "australia"), "dept": ("department",)}
    stop_words = {"pty", "ltd", "limited", "inc", "incorporated", "the", "of", "and", "at"}
    tokens: list[str] = []
    for word in re.findall(r"[A-Za-z0-9]+", value.lower()):
        if word in stop_words:
            continue
        tokens.extend(replacements.get(word, (word,)))
    return tokens


def organisations_are_equivalent(left: str, right: str) -> bool:
    left_tokens, right_tokens = set(_identity_tokens(left)), set(_identity_tokens(right))
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    smaller, larger = sorted((left_tokens, right_tokens), key=len)
    return len(smaller) >= 2 and smaller <= larger


def _strong_organisation_identity(company: str) -> bool:
    tokens = _identity_tokens(company)
    generic_single_names = {
        "agency", "department", "government", "health", "planning", "services", "transport",
    }
    return len(tokens) >= 2 or (
        len(tokens) == 1 and len(tokens[0]) >= 6 and tokens[0] not in generic_single_names
    )


def _identity_phrase_is_named(value: str, content: str) -> bool:
    phrase = " ".join(_identity_tokens(value))
    normalised_content = " ".join(_identity_tokens(content))
    return bool(phrase and f" {phrase} " in f" {normalised_content} ")


def _organisation_identity_is_named(company: str, content: str) -> bool:
    return organisation_is_named(company, content) or _identity_phrase_is_named(company, content)


def enforce_profile_contact(content: str, profile: ApplicantProfile, document_type: str) -> str:
    mobile_pattern = re.compile(r"(?<!\d)(?:\+?61[ \t().-]*4|04)(?:[ \t().-]*\d){8}(?!\d)")
    email_pattern = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
    corrected, phone_replacements = mobile_pattern.subn(profile.phone.strip(), content)
    corrected, email_replacements = email_pattern.subn(profile.email.strip(), corrected)
    missing_lines: list[str] = []
    if phone_replacements == 0:
        missing_lines.append(f"Phone: {profile.phone.strip()}")
    if email_replacements == 0:
        missing_lines.append(f"Email: {profile.email.strip()}")
    if missing_lines:
        contact_block = "\n".join(missing_lines)
        corrected = (
            f"{contact_block}\n\n{corrected}"
            if document_type == "tailored_resume"
            else f"{corrected.rstrip()}\n\n{contact_block}"
        )
    return corrected


def confirmed_availability_wording(value: str) -> str:
    return {
        "two_weeks": "Available following two weeks' notice",
        "one_month": "Available following one month's notice",
        "negotiable": "Start date negotiable",
        "not_specified": "Do not state availability",
    }.get(value, "Do not state availability")


def auto_polish_cover_letter(
    content: str,
    profile: ApplicantProfile | None,
    job_description: str = "",
) -> str:
    def application_heading(match: re.Match) -> str:
        heading = match.group(1).strip()
        return heading if heading.lower().startswith("application for ") else f"Application for {heading}"

    polished = re.sub(
        r"(?i)\bI am writing to apply\b",
        "Please accept my application",
        content,
    )
    polished = re.sub(r"(?im)^\s*(?:RE|Subject)\s*:\s*(.+?)\s*$", application_heading, polished)
    generic_salutation = re.search(
        r"(?im)^Dear (?:Hiring Manager|Recruitment Team|Sir or Madam)\s*,?\s*$",
        polished,
    )
    if generic_salutation:
        polished = re.sub(r"(?im)^Yours sincerely\s*,?\s*$", "Yours faithfully", polished)

    # Remove common speculative recruiter/client sentences. The prompt only permits
    # these relationships when the advertisement states them explicitly.
    relationship_evidence = ("on behalf of", "our client", "recruitment agency", "recruitment company")
    if not any(phrase in job_description.lower() for phrase in relationship_evidence):
        polished = re.sub(
            r"(?im)^.*\bmay be (?:coordinating this recruitment|recruiting|acting) on behalf of.*(?:\r?\n|$)",
            "",
            polished,
        )
        polished = re.sub(
            r"(?im)^.*\bwhichever entity manages the process.*(?:\r?\n|$)",
            "",
            polished,
        )

    if profile:
        availability = confirmed_availability_wording(profile.availability_notice)
        availability_sentence = {
            "two_weeks": "I am available following two weeks' notice.",
            "one_month": "I am available following one month's notice.",
            "negotiable": "My start date is negotiable.",
        }.get(profile.availability_notice)
        if availability_sentence:
            polished = re.sub(
                r"(?i)I am available to commence[^.]*\.",
                availability_sentence,
                polished,
            )
        elif availability == "Do not state availability":
            polished = re.sub(r"(?i)I am available to commence[^.]*\.\s*", "", polished)

    return re.sub(r"\n{3,}", "\n\n", polished).strip()


def auto_polish_tailored_resume(content: str) -> str:
    polished = content
    heading_variants = (
        (r"professional summary|professional profile|career profile|profile|summary", "Professional Summary"),
        (r"key skills|core skills|relevant skills|skills|core capabilities|relevant capabilities", "Key Skills"),
        (r"work experience|professional experience|employment history|career history", "Work Experience"),
        (r"references|referees", "References"),
    )
    for variants, standard in heading_variants:
        polished = re.sub(
            rf"(?im)^\s*(?:#{{1,3}}\s*)?(?:\*\*)?(?:{variants})(?:\*\*)?\s*:?[ \t]*$",
            f"## {standard}",
            polished,
        )
    polished = re.sub(
        r"(?im)^\s*(?:references?|referees?)\s+(?:are\s+)?available (?:on|upon) request\.?\s*$",
        "Available upon request",
        polished,
    )
    if not re.search(r"(?im)^## References\s*$", polished):
        polished = f"{polished.rstrip()}\n\n## References\nAvailable upon request"
    return re.sub(r"\n{3,}", "\n\n", polished).strip()


@app.get("/profile", response_model=ApplicantProfileResponse | None)
def get_profile(
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    profile = session.exec(select_for_user(ApplicantProfile, user_id).order_by(ApplicantProfile.id)).first()
    if not profile:
        return None
    referees = session.exec(
        select_for_user(Referee, user_id)
        .where(Referee.profile_id == profile.id)
        .order_by(Referee.display_order)
    ).all()
    return profile_response(profile, referees)


@app.put("/profile", response_model=ApplicantProfileResponse)
def save_profile(
    payload: ApplicantProfilePayload,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    if len(payload.referees) > 2:
        raise HTTPException(400, "A maximum of two referees can be saved.")
    profile = session.exec(select_for_user(ApplicantProfile, user_id).order_by(ApplicantProfile.id)).first()
    profile_values = payload.model_dump(exclude={"referees"})
    if profile:
        for key, value in profile_values.items():
            setattr(profile, key, value)
        profile.updated_at = datetime.utcnow()
    else:
        profile = ApplicantProfile.model_validate(profile_values)
        profile.user_id = user_id
    session.add(profile)
    session.commit()
    session.refresh(profile)

    existing_referees = session.exec(
        select_for_user(Referee, user_id).where(Referee.profile_id == profile.id)
    ).all()
    for referee in existing_referees:
        session.delete(referee)
    for index, referee_payload in enumerate(payload.referees, start=1):
        referee = Referee(
            user_id=user_id,
            profile_id=profile.id,
            display_order=index,
            **referee_payload.model_dump(),
        )
        session.add(referee)
    session.commit()
    referees = session.exec(
        select_for_user(Referee, user_id)
        .where(Referee.profile_id == profile.id)
        .order_by(Referee.display_order)
    ).all()
    return profile_response(profile, referees)


@app.post("/resumes", response_model=Resume)
def create_resume(
    payload: ResumeCreate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    values = payload.model_dump()
    values["ckb_json"] = serialise_ckb(values["source_text"], values.get("experiences_json") or "[]")
    resume = Resume.model_validate(values)
    resume.user_id = user_id
    session.add(resume); session.commit(); session.refresh(resume)
    invalidate_evidence_matches(session, user_id)
    return resume


@app.post("/resumes/upload", response_model=Resume)
async def upload_resume(
    file: UploadFile = File(...),
    title: str = Form("Master Resume"),
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    try:
        source_text = extract_resume_text(file.filename or "resume", await file.read())
    except ValueError as error:
        raise HTTPException(400, str(error))
    experiences_json = json.dumps(extract_resume_experiences(source_text), ensure_ascii=False)
    ckb_json = serialise_ckb(source_text, experiences_json)
    current = session.exec(select_for_user(Resume, user_id).order_by(Resume.updated_at.desc())).first()
    if current:
        current.title = title.strip() or "Master Resume"
        current.source_text = source_text
        current.experiences_json = experiences_json
        current.ckb_json = ckb_json
        current.updated_at = datetime.utcnow()
        resume = current
    else:
        resume = Resume(
            user_id=user_id,
            title=title.strip() or "Master Resume",
            source_text=source_text,
            experiences_json=experiences_json,
            ckb_json=ckb_json,
        )
    session.add(resume); session.commit(); session.refresh(resume)
    invalidate_evidence_matches(session, user_id)
    return resume


@app.get("/resumes", response_model=list[Resume])
def list_resumes(
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    return session.exec(select_for_user(Resume, user_id).order_by(Resume.updated_at.desc())).all()


@app.get("/resumes/{resume_id}/content-check", response_model=ResumeContentCheckResponse)
def check_resume_content(
    resume_id: int,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    resume = get_for_user(session, Resume, resume_id, user_id)
    if not resume:
        raise HTTPException(404, "Resume not found.")
    profile = session.exec(select_for_user(ApplicantProfile, user_id)).first()
    return build_resume_content_check(resume, profile)


@app.patch("/resumes/{resume_id}", response_model=Resume)
def update_resume(
    resume_id: int,
    payload: ResumeUpdate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    resume = get_for_user(session, Resume, resume_id, user_id)
    if not resume:
        raise HTTPException(404, "Resume not found.")
    values = payload.model_dump(exclude_unset=True)
    next_source_text = values.get("source_text", resume.source_text)
    next_experiences_json = values.get("experiences_json", resume.experiences_json)
    if "source_text" in values or "experiences_json" in values:
        values["ckb_json"] = serialise_ckb(next_source_text, next_experiences_json)
    for key, value in values.items():
        setattr(resume, key, value)
    resume.updated_at = datetime.utcnow()
    session.add(resume); session.commit(); session.refresh(resume)
    invalidate_evidence_matches(session, user_id)
    return resume


@app.post("/applications", response_model=JobApplication)
def create_application(
    payload: JobApplicationCreate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    values = payload.model_dump()
    values["job_model_json"] = serialise_job_model(
        values["job_description"], values.get("selection_criteria"), values["position_title"], values["company"]
    )
    values["application_requirements_json"] = json.dumps(parse_application_requirements(
        "\n".join(filter(None, (values["job_description"], values.get("selection_criteria"))))
    ), ensure_ascii=False)
    application = JobApplication.model_validate(values)
    application.user_id = user_id
    session.add(application); session.commit(); session.refresh(application)
    return application


@app.post("/applications/import-url", response_model=JobUrlImportResponse)
def import_application_url(
    payload: JobUrlImportRequest,
    _user_id: UUID | None = Depends(get_current_user),
):
    try:
        return JobUrlImportResponse.model_validate(import_job_url(payload.job_url))
    except ValueError as error:
        raise HTTPException(400, str(error))
    except Exception as error:
        raise HTTPException(
            400,
            "This website did not allow automatic reading. Your link is still saved in the form; paste the job details manually instead.",
        ) from error


@app.post("/applications/parse-ad", response_model=JobAdParseResponse)
def parse_application_ad(
    payload: JobAdParseRequest,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    previous_companies = [item.company for item in session.exec(select_for_user(JobApplication, user_id)).all()]
    try:
        parsed = parse_job_ad_text(payload.raw_text, previous_companies)
        parsed["application_requirements"] = parse_application_requirements(payload.raw_text)
        return JobAdParseResponse.model_validate(parsed)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get("/applications", response_model=list[JobApplication])
def list_applications(
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    return session.exec(select_for_user(JobApplication, user_id).order_by(JobApplication.updated_at.desc())).all()


@app.get("/applications/{application_id}/application-requirements", response_model=ApplicationRequirementsResponse)
def get_application_requirements(
    application_id: int,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    return ApplicationRequirementsResponse(
        application_id=application.id,
        requirements=load_application_requirements(
            application.application_requirements_json, application.selection_criteria
        ),
    )


@app.patch("/applications/{application_id}/application-requirements", response_model=ApplicationRequirementsResponse)
def update_application_requirements(
    application_id: int,
    payload: ApplicationRequirementsUpdate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    current = load_application_requirements(
        application.application_requirements_json, application.selection_criteria
    )
    try:
        if payload.action == "confirm":
            if payload.documents is not None or payload.additional_documents is not None:
                raise ValueError("Confirm does not accept requirement corrections.")
            updated = confirm_application_requirements(current)
        else:
            if payload.documents is None:
                raise ValueError("Correct requires a complete documents object.")
            updated = correct_application_requirements(
                current,
                payload.documents,
                payload.additional_documents if payload.additional_documents is not None else current["additional_documents"],
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    application.application_requirements_json = json.dumps(updated, ensure_ascii=False)
    application.updated_at = datetime.utcnow()
    session.add(application)
    session.commit()
    return ApplicationRequirementsResponse(application_id=application.id, requirements=updated)


@app.patch("/applications/{application_id}", response_model=JobApplication)
def update_application(
    application_id: int,
    payload: JobApplicationUpdate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    values = payload.model_dump(exclude_unset=True)
    old_job_description = application.job_description
    old_selection_criteria = application.selection_criteria
    for required_field in ("company", "position_title", "job_description"):
        if required_field in values and not str(values[required_field] or "").strip():
            raise HTTPException(400, f"{required_field.replace('_', ' ').title()} cannot be blank.")
    for key, value in values.items():
        if key in {"job_url", "selection_criteria", "submission_reference"} and isinstance(value, str) and not value.strip():
            value = None
        setattr(application, key, value.strip() if isinstance(value, str) else value)
    if any(key in values for key in {"company", "position_title", "job_description", "selection_criteria"}):
        application.job_model_json = serialise_job_model(
            application.job_description, application.selection_criteria, application.position_title, application.company
        )
        application.evidence_matches_json = "{}"
        application.selection_plan_json = "{}"
        application.selection_confirmations_json = "[]"
    if requirements_source_changed(
        old_job_description,
        old_selection_criteria,
        application.job_description,
        application.selection_criteria,
    ):
        application.application_requirements_json = json.dumps(parse_application_requirements(
            "\n".join(filter(None, (application.job_description, application.selection_criteria)))
        ), ensure_ascii=False)
    application.updated_at = datetime.utcnow()
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


@app.patch("/applications/{application_id}/submission", response_model=JobApplication)
def record_application_submission(
    application_id: int,
    payload: JobApplicationSubmissionUpdate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    reference = (payload.submission_reference or "").strip() or None
    application.status = "applied"
    application.submission_reference = reference
    application.submitted_at = payload.submitted_at or datetime.utcnow()
    application.updated_at = datetime.utcnow()
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


@app.patch("/applications/{application_id}/status", response_model=JobApplication)
def update_application_status(
    application_id: int,
    payload: JobApplicationStatusUpdate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    application.status = payload.status
    if payload.status == "applied" and not application.submitted_at:
        application.submitted_at = datetime.utcnow()
    application.updated_at = datetime.utcnow()
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


@app.get("/applications/{application_id}/documents", response_model=list[GeneratedDocument])
def list_generated_documents(
    application_id: int,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    if not get_for_user(session, JobApplication, application_id, user_id):
        raise HTTPException(404, "Application not found.")
    return session.exec(
        select_for_user(GeneratedDocument, user_id)
        .where(GeneratedDocument.application_id == application_id)
        .order_by(GeneratedDocument.created_at.desc())
    ).all()


@app.get("/applications/{application_id}/quality-check", response_model=QualityCheckResponse)
def quality_check(
    application_id: int,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    documents = session.exec(
        select_for_user(GeneratedDocument, user_id)
        .where(GeneratedDocument.application_id == application_id)
        .order_by(GeneratedDocument.created_at.desc())
    ).all()
    latest: dict[str, GeneratedDocument] = {}
    for document in documents:
        latest.setdefault(document.document_type, document)

    issues: list[QualityCheckIssue] = []
    required = ("tailored_resume", "cover_letter") + (
        ("selection_criteria",) if (application.selection_criteria or "").strip() else ()
    )
    for document_type in required:
        if document_type not in latest:
            issues.append(QualityCheckIssue(
                severity="error",
                code="missing_document",
                message=f"Generate the {document_type.replace('_', ' ')} before applying.",
                document_type=document_type,
            ))

    profile = session.exec(select_for_user(ApplicantProfile, user_id).order_by(ApplicantProfile.id)).first()
    content_to_check = {key: value.content for key, value in latest.items() if key in required}
    for reviewed_type in ("selection_criteria", "cover_letter", "tailored_resume"):
        reviewed_document = latest.get(reviewed_type)
        if not reviewed_document or (reviewed_document.reviewer_json or "{}").strip() in {"", "{}"}:
            continue
        try:
            reviewer = json.loads(reviewed_document.reviewer_json)
        except json.JSONDecodeError:
            reviewer = {"status": "fail", "results": []}
        if reviewer.get("status") == "fail":
            if reviewed_type in {"tailored_resume", "cover_letter", "selection_criteria"} and reviewer.get("generation_status") == "needs_ckb_update":
                remaining = reviewer.get("remaining_issues") or []
                claims = [str(item.get("claim") or "").strip() for item in remaining if item.get("claim")]
                summary = ", ".join(claims[:5])
                document_label = reviewed_type.replace("_", " ").title()
                message = f"The {document_label} needs additional source information before it can be finalised."
                if summary:
                    message = f"The {document_label} could not support a few requested details. Add or confirm the source information for: {summary}."
                issue_code = "resume_needs_ckb_update" if reviewed_type == "tailored_resume" else f"{reviewed_type}_needs_ckb_update"
                issues.append(QualityCheckIssue(
                    severity="error", code=issue_code, message=message,
                    document_type=reviewed_type,
                ))
                continue
            reviewer_issues = [
                issue
                for result in reviewer.get("results") or []
                for issue in result.get("issues") or []
            ]
            if reviewer_issues:
                for issue in reviewer_issues:
                    issues.append(QualityCheckIssue(
                        severity="error",
                        code=f"reviewer_{issue.get('type', 'finding')}",
                        message=str(issue.get("description") or f"The {reviewed_type.replace('_', ' ').title()} Reviewer found a material issue."),
                        document_type=reviewed_type,
                    ))
            else:
                issues.append(QualityCheckIssue(
                    severity="error", code="reviewer_failed",
                    message=f"The {reviewed_type.replace('_', ' ').title()} Reviewer did not pass this document. Regenerate or review it manually.",
                    document_type=reviewed_type,
                ))
    cover = content_to_check.get("cover_letter", "")
    tailored_resume = content_to_check.get("tailored_resume", "")
    role_title = application.position_title.split(" - ", 1)[0].strip()
    if cover and role_title.lower() not in cover.lower():
        issues.append(QualityCheckIssue(
            severity="error",
            code="position_title_mismatch",
            message="The cover letter does not contain the current position title. Check for an old or incorrect job title.",
            document_type="cover_letter",
        ))
    advertised_company = application.company.strip()
    if cover and advertised_company and not organisation_is_named(advertised_company, cover):
        issues.append(QualityCheckIssue(
            severity="warning",
            code="advertised_company_missing",
            message="The advertised organisation is not named in the cover letter. Confirm whether it is a recruiter and whether another organisation is the actual employer or client.",
            document_type="cover_letter",
        ))

    current_job_context = "\n".join((application.job_description, application.job_model_json or "{}"))
    master_resume = session.exec(select_for_user(Resume, user_id).order_by(Resume.updated_at.desc())).first()
    applicant_evidence_context = (
        "\n".join((master_resume.source_text, master_resume.ckb_json or "[]"))
        if master_resume else ""
    )
    other_applications = session.exec(
        select_for_user(JobApplication, user_id).where(JobApplication.id != application_id)
    ).all()
    for document_type in ("cover_letter", "selection_criteria"):
        content = content_to_check.get(document_type, "")
        if not content:
            continue
        for other_application in other_applications:
            other_company = (other_application.company or "").strip()
            other_title = (other_application.position_title or "").split(" - ", 1)[0].strip()
            if (
                not other_company
                or not other_title
                or not _strong_organisation_identity(other_company)
                or organisations_are_equivalent(other_company, advertised_company)
            ):
                continue
            pair_is_named = (
                _organisation_identity_is_named(other_company, content)
                and _identity_phrase_is_named(other_title, content)
            )
            if not pair_is_named:
                continue
            pair_is_current_job_context = (
                _organisation_identity_is_named(other_company, current_job_context)
                and _identity_phrase_is_named(other_title, current_job_context)
            )
            pair_is_applicant_evidence = (
                _organisation_identity_is_named(other_company, applicant_evidence_context)
                and _identity_phrase_is_named(other_title, applicant_evidence_context)
            )
            if pair_is_current_job_context or pair_is_applicant_evidence:
                continue
            issues.append(QualityCheckIssue(
                severity="error",
                code="cross_application_content_leak",
                message=(
                    f"This {document_type.replace('_', ' ')} contains the organisation and position identity "
                    f"from another saved application ({other_company} — {other_title}). Check that content was "
                    "not carried over from that application."
                ),
                document_type=document_type,
            ))
            break

    placeholders = ("[your name]", "[current date]", "[date]", "[company name]", "[hiring manager]")
    cliches = (
        "i am confident", "i am excited", "i am writing to express my interest", "i am writing to apply",
        "i am comfortable learning", "adapt quickly", "learn quickly",
        "proven track record", "dynamic professional", "passionate about",
        "leverage my skills", "well placed",
    )
    self_deprecating_phrases = (
        "although i have not", "while i have not", "even though i have not",
        "despite not having", "despite lacking", "i lack", "i currently lack",
        "i do not have direct experience", "i do not have experience in",
        "i have limited experience in", "i have no direct experience",
    )
    american_spellings = ("organized", "organization", "prioritize", "behavior", "labor")
    unsupported_comparisons = (
        "comparable in value", "comparable in scale", "comparable in complexity",
        "value and complexity comparable", "equivalent in scale", "similar in scale",
    )
    for document_type, content in content_to_check.items():
        lowered = content.lower()
        if any(placeholder in lowered for placeholder in placeholders):
            issues.append(QualityCheckIssue(
                severity="error",
                code="placeholder_text",
                message="Unresolved placeholder text remains in this document.",
                document_type=document_type,
            ))
        found_cliches = [phrase for phrase in cliches if phrase in lowered]
        if found_cliches:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="generic_ai_wording",
                message=f"Generic wording detected ({', '.join(found_cliches[:3])}). Replace it with direct evidence where practical.",
                document_type=document_type,
            ))
        found_self_deprecating = (
            [phrase for phrase in self_deprecating_phrases if phrase in lowered]
            if document_type in {"cover_letter", "selection_criteria"} else []
        )
        if found_self_deprecating:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="self_deprecating_wording",
                message=(
                    "The document foregrounds an evidence gap in the applicant's own voice "
                    f"({', '.join(found_self_deprecating[:2])}). State the supported transferable evidence "
                    "positively without implying direct experience."
                ),
                document_type=document_type,
            ))
        found_us_spellings = [word for word in american_spellings if re.search(rf"\b{word}\b", lowered)]
        if found_us_spellings:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="american_spelling",
                message=f"Check Australian spelling for: {', '.join(found_us_spellings)}.",
                document_type=document_type,
            ))
        found_comparisons = [phrase for phrase in unsupported_comparisons if phrase in lowered]
        if found_comparisons:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="unsupported_project_comparison",
                message="The document compares project value, scale or complexity. Keep this only when both the resume and job advertisement provide facts supporting the comparison.",
                document_type=document_type,
            ))
        if "translate directly to managing" in lowered or "directly transferable to managing" in lowered:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="jd_duty_as_experience",
                message="A job requirement may be presented as direct experience. Rephrase it as transferable capability unless the Master Resume confirms the duty.",
                document_type=document_type,
            ))
        if "available to commence immediately" in lowered or "available to commence promptly" in lowered:
            availability = profile.availability_notice if profile else "not_specified"
            expected = {
                "two_weeks": "two weeks' notice",
                "one_month": "one month's notice",
                "negotiable": "a negotiable start date",
                "not_specified": "no confirmed start date",
            }.get(availability, "the saved availability preference")
            issues.append(QualityCheckIssue(
                severity="warning",
                code="unconfirmed_availability",
                message=f"The document states immediate or prompt availability, but the profile indicates {expected}. Confirm or revise this statement.",
                document_type=document_type,
            ))
        for code, message in find_writing_quality_issues(content):
            issues.append(QualityCheckIssue(
                severity="warning",
                code=code,
                message=message,
                document_type=document_type,
            ))

    if profile:
        for document_type in ("tailored_resume", "cover_letter"):
            content = content_to_check.get(document_type, "")
            profile_phone = "".join(character for character in profile.phone if character.isdigit())
            document_phone_text = "".join(character for character in content if character.isdigit())
            phone_variants = {profile_phone}
            if profile_phone.startswith("61"):
                phone_variants.add("0" + profile_phone[2:])
            elif profile_phone.startswith("0"):
                phone_variants.add("61" + profile_phone[1:])
            phone_found = bool(profile_phone) and any(value in document_phone_text for value in phone_variants)
            if content and not phone_found:
                issues.append(QualityCheckIssue(
                    severity="error",
                    code="phone_mismatch",
                    message="The saved profile phone number is missing from this document.",
                    document_type=document_type,
                ))
            if content and profile.email.lower() not in content.lower():
                issues.append(QualityCheckIssue(
                    severity="error",
                    code="email_mismatch",
                    message="The saved profile email is missing from this document.",
                    document_type=document_type,
                ))
    else:
        issues.append(QualityCheckIssue(
            severity="warning",
            code="profile_missing",
            message="Save an Applicant Profile to check contact details automatically.",
        ))

    if cover:
        lowered_cover = cover.lower()
        speculative_relationships = (
            "may be coordinating this recruitment on behalf of",
            "may be recruiting on behalf of",
            "may be acting on behalf of",
            "whichever entity manages the process",
        )
        relationship_evidence = ("on behalf of", "our client", "recruitment agency", "recruitment company")
        relationship_is_explicit = any(phrase in application.job_description.lower() for phrase in relationship_evidence)
        if not relationship_is_explicit and any(phrase in lowered_cover for phrase in speculative_relationships):
            issues.append(QualityCheckIssue(
                severity="warning",
                code="speculative_employer_relationship",
                message="The cover letter speculates about a recruiter, client or employer relationship. Remove it unless the job advertisement states that relationship explicitly.",
                document_type="cover_letter",
            ))
        generic_salutations = ("dear hiring manager", "dear recruitment team", "dear sir or madam")
        if any(salutation in lowered_cover for salutation in generic_salutations) and "yours sincerely" in lowered_cover:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="salutation_signoff_mismatch",
                message="A generic salutation should normally close with 'Yours faithfully' rather than 'Yours sincerely'.",
                document_type="cover_letter",
            ))
        word_count = len(cover.split())
        if word_count < 220:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="cover_letter_short",
                message=f"The cover letter has about {word_count} words and may not provide enough role-specific evidence.",
                document_type="cover_letter",
            ))
        elif word_count > 600:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="cover_letter_length",
                message=f"The cover letter has about {word_count} words and may be longer than a concise one-page letter.",
                document_type="cover_letter",
            ))

    if tailored_resume:
        required_resume_headings = ("professional summary", "key skills", "work experience")
        missing_resume_headings = [
            heading for heading in required_resume_headings
            if not re.search(rf"(?im)^\s*#*\s*{re.escape(heading)}\s*$", tailored_resume)
        ]
        if missing_resume_headings:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="resume_structure",
                message=f"The CV is missing standard sections: {', '.join(missing_resume_headings)}.",
                document_type="tailored_resume",
            ))
        resume_word_count = len(tailored_resume.split())
        if resume_word_count > 900:
            issues.append(QualityCheckIssue(
                severity="warning",
                code="resume_length",
                message=f"The CV has about {resume_word_count} words and may run beyond two pages.",
                document_type="tailored_resume",
            ))

    return QualityCheckResponse(
        ready=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        checked_documents=sorted(content_to_check),
    )


@app.patch("/documents/{document_id}", response_model=GeneratedDocument)
def update_generated_document(
    document_id: int,
    payload: GeneratedDocumentUpdate,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    document = get_for_user(session, GeneratedDocument, document_id, user_id)
    if not document:
        raise HTTPException(404, "Generated document not found.")
    document.content = payload.content
    # Reviewer findings describe the generated content. Once the applicant edits
    # that content, those findings are stale and must not block Final Check.
    document.reviewer_json = "{}"
    session.add(document)
    if document.document_type == "selection_criteria":
        application = get_for_user(session, JobApplication, document.application_id, user_id)
        if not application:
            raise HTTPException(404, "Application not found.")
        application.selection_confirmations_json = "[]"
        application.updated_at = datetime.utcnow()
        session.add(application)
    session.commit(); session.refresh(document)
    return document


@app.get("/documents/{document_id}/export")
def export_generated_document(
    document_id: int,
    format: str = Query(pattern="^(docx|pdf)$"),
    template: str = Query(default="classic", pattern="^(classic|modern|traditional)$"),
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    document = get_for_user(session, GeneratedDocument, document_id, user_id)
    if not document:
        raise HTTPException(404, "Generated document not found.")
    application = get_for_user(session, JobApplication, document.application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    label = {
        "tailored_resume": "Tailored_Resume",
        "cover_letter": "Cover_Letter",
        "selection_criteria": "Selection_Criteria",
    }.get(document.document_type, document.document_type)
    filename = safe_filename(f"{application.position_title}_{label}")
    if format == "docx":
        payload = create_docx(document.content, label.replace("_", " "), template)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        payload = create_pdf(document.content, label.replace("_", " "), template)
        media_type = "application/pdf"
    return Response(payload, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}.{format}"'})


@app.get("/documents/{document_id}/trace")
def export_document_trace(
    document_id: int,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    document = get_for_user(session, GeneratedDocument, document_id, user_id)
    if not document:
        raise HTTPException(404, "Generated document not found.")
    bundle = build_trace_bundle(document)
    filename = safe_filename(f"{document.document_type}_{document.run_id or document.id}_Trace")
    return Response(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
    )


@app.get("/applications/{application_id}/export-pack")
def export_application_pack(
    application_id: int,
    format: str = Query(pattern="^(docx|pdf)$"),
    template: str = Query(default="classic", pattern="^(classic|modern|traditional)$"),
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    documents = session.exec(
        select_for_user(GeneratedDocument, user_id)
        .where(GeneratedDocument.application_id == application_id)
        .order_by(GeneratedDocument.created_at.desc())
    ).all()
    latest = {}
    for document in documents:
        if document.document_type in {"tailored_resume", "cover_letter", "selection_criteria"}:
            latest.setdefault(document.document_type, document)
    required_types = ["tailored_resume", "cover_letter"]
    if (application.selection_criteria or "").strip():
        required_types.append("selection_criteria")
    if any(document_type not in latest for document_type in required_types):
        raise HTTPException(400, "Generate all required application documents before downloading the pack.")

    archive_stream = BytesIO()
    labels = {
        "tailored_resume": "Tailored_Resume",
        "cover_letter": "Cover_Letter",
        "selection_criteria": "Selection_Criteria",
    }
    with ZipFile(archive_stream, "w", ZIP_DEFLATED) as archive:
        for document_type in required_types:
            document = latest[document_type]
            label = labels[document_type]
            payload = create_docx(document.content, label.replace("_", " "), template) if format == "docx" else create_pdf(document.content, label.replace("_", " "), template)
            archive.writestr(f"{safe_filename(application.position_title)}_{label}.{format}", payload)
    filename = safe_filename(f"{application.position_title}_Application_Pack_{format.upper()}")
    return Response(archive_stream.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}.zip"'})


@app.post("/generate", response_model=GeneratedDocument)
def generate(
    payload: GenerateRequest,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    feature = generation_feature_status(payload.document_type, {
        setting: bool(getattr(settings, setting)) for setting in GENERATION_FEATURES.values()
    })
    if not feature["supported"]:
        raise HTTPException(400, "Unsupported document type.")
    if not feature["enabled"]:
        raise HTTPException(503, f"{payload.document_type.replace('_', ' ').title()} generation is temporarily unavailable. Existing documents remain available.")
    generation_started = perf_counter()
    application = get_for_user(session, JobApplication, payload.application_id, user_id)
    master_resume = session.exec(select_for_user(Resume, user_id).order_by(Resume.updated_at.desc())).first()
    if not application or not master_resume:
        raise HTTPException(400, "Create a Master Resume and job application first.")
    application.company = expand_abbreviated_company(application.company, application.job_description)
    new_pack_usage = check_generation_quota(session, user_id, payload.pack_id)
    selection_credit_key = (
        check_selection_criteria_credit(session, user_id, payload.pack_id)
        if payload.document_type == "selection_criteria"
        else None
    )
    profile = session.exec(select_for_user(ApplicantProfile, user_id).order_by(ApplicantProfile.id)).first()
    used_experiences = "[]"
    used_closing_styles = "[]"
    if payload.document_type == "cover_letter":
        prior_selection = session.exec(
            select_for_user(GeneratedDocument, user_id)
            .where(GeneratedDocument.application_id == application.id)
            .where(GeneratedDocument.document_type == "selection_criteria")
            .order_by(GeneratedDocument.created_at.desc())
        ).first()
        if prior_selection:
            used_experiences = prior_selection.used_experiences_json or "[]"
            used_closing_styles = prior_selection.closing_styles_json or "[]"
    profile_text = applicant_profile_prompt(profile) if profile else None
    ckb_source_json = master_resume.ckb_json or "[]"
    if ckb_source_json.strip() in {"", "[]"}:
        ckb_source_json = serialise_ckb(master_resume.source_text, master_resume.experiences_json or "[]")
        master_resume.ckb_json = ckb_source_json
        session.add(master_resume)
        session.commit()
    job_model_json = serialise_job_model(
        application.job_description, application.selection_criteria, application.position_title, application.company
    )
    if job_model_json != (application.job_model_json or "{}"):
        application.job_model_json = job_model_json
        application.evidence_matches_json = "{}"
        application.selection_plan_json = "{}"
        application.selection_confirmations_json = "[]"
        session.add(application)
        session.commit()
    evidence_matches_json = application.evidence_matches_json or "{}"
    try:
        if evidence_matches_json.strip() in {"", "{}"}:
            evidence_matches_json = json.dumps(match_evidence_batch(ckb_source_json, job_model_json), ensure_ascii=False)
            application.evidence_matches_json = evidence_matches_json
            session.add(application)
            session.commit()
        selection_plan_json = application.selection_plan_json or "{}"
        if selection_plan_json.strip() in {"", "{}"}:
            selection_plan_json = json.dumps(build_selection_plan(
                json.loads(job_model_json), json.loads(evidence_matches_json), json.loads(ckb_source_json),
                settings.default_sc_word_target,
            ), ensure_ascii=False)
            application.selection_plan_json = selection_plan_json
            session.add(application)
            session.commit()
        selection_bundle = None
        selection_review = None
        cover_letter_plan = None
        cover_letter_review = None
        resume_plan = None
        resume_review = None
        if payload.document_type == "selection_criteria":
            selection_bundle = generate_selection_criteria_bundle(ckb_source_json, selection_plan_json)
            selection_bundle, selection_review = repair_selection_criteria_bundle(
                ckb_source_json, selection_plan_json, selection_bundle
            )
            content = selection_bundle["content"]
        else:
            if payload.document_type == "cover_letter":
                try:
                    prior_ids = json.loads(used_experiences or "[]")
                except json.JSONDecodeError:
                    prior_ids = []
                cover_letter_plan = build_cover_letter_plan(
                    json.loads(job_model_json), json.loads(evidence_matches_json), json.loads(ckb_source_json), profile, prior_ids
                )
            if payload.document_type == "tailored_resume":
                resume_plan = build_resume_curation_plan(
                    json.loads(job_model_json), json.loads(evidence_matches_json), json.loads(ckb_source_json)
                )
            content = generate_draft(
                master_resume.source_text,
                application.job_description,
                payload.document_type,
                application.selection_criteria,
                profile_text,
                application.position_title,
                application.company,
                ckb_source_json,
                used_experiences,
                used_closing_styles,
                job_model_json,
                evidence_matches_json,
                selection_plan_json,
                json.dumps(cover_letter_plan or {}, ensure_ascii=False),
                json.dumps(resume_plan or {}, ensure_ascii=False),
            )
        if profile and payload.document_type in {"tailored_resume", "cover_letter"}:
            content = enforce_profile_contact(content, profile, payload.document_type)
        if payload.document_type == "tailored_resume":
            content = auto_polish_tailored_resume(content)
        if payload.document_type == "cover_letter":
            content = auto_polish_cover_letter(content, profile, application.job_description)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(400, str(error))
    except AIServiceError as error:
        raise HTTPException(502, str(error))
    metadata = {
        "used_experiences": selection_bundle["used_experiences"] if selection_bundle else [],
        "closing_styles": [],
    }
    if payload.document_type in {"cover_letter", "tailored_resume"}:
        match = re.search(r"<!--\s*GENERATION_META\s+(\{.*?\})\s*-->", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                metadata["used_experiences"] = parsed.get("used_experiences", [])
                metadata["closing_styles"] = parsed.get("closing_styles", [])
            except (json.JSONDecodeError, AttributeError):
                pass
            content = content[:match.start()].rstrip()
    if payload.document_type in {"selection_criteria", "cover_letter", "tailored_resume"}:
        parsed_matches = json.loads(evidence_matches_json or "{}")
        allowed_evidence_ids = {
            str(evidence_id)
            for item in parsed_matches.get("matches") or []
            for evidence_id in item.get("matched_evidence") or []
        }
        if payload.document_type == "cover_letter" and cover_letter_plan:
            allowed_evidence_ids = selected_cover_letter_evidence_ids(cover_letter_plan)
        if payload.document_type == "tailored_resume" and resume_plan:
            allowed_evidence_ids = selected_resume_evidence_ids(resume_plan)
        invalid_evidence_ids = set(metadata["used_experiences"]) - allowed_evidence_ids
        if invalid_evidence_ids:
            raise HTTPException(502, "The draft cited resume evidence that was not supplied. Please regenerate it.")
    if payload.document_type == "tailored_resume":
        validation = validate_resume_content(content, resume_plan or {}, metadata["used_experiences"])
        if not validation["valid"]:
            raise HTTPException(502, validation["issues"][0]["message"] + " Please regenerate it.")
        try:
            content, resume_review = repair_tailored_resume(
                content, ckb_source_json, job_model_json,
                json.dumps(resume_plan or {}, ensure_ascii=False),
                profile_text,
            )
            repaired_validation = validate_resume_content(content, resume_plan or {}, metadata["used_experiences"])
            if not repaired_validation["valid"]:
                raise HTTPException(502, repaired_validation["issues"][0]["message"] + " Please regenerate it.")
        except ValueError as error:
            raise HTTPException(400, str(error))
        except AIServiceError as error:
            raise HTTPException(502, str(error))
    if payload.document_type == "cover_letter":
        try:
            content, cover_letter_review = repair_cover_letter(
                content, ckb_source_json, job_model_json,
                json.dumps(cover_letter_plan or {}, ensure_ascii=False), profile_text,
            )
        except ValueError as error:
            raise HTTPException(400, str(error))
        except AIServiceError as error:
            raise HTTPException(502, str(error))
    run_id = str(uuid4())
    provider = settings.ai_provider.strip().lower()
    model_name = settings.deepseek_model if provider == "deepseek" else settings.openai_model
    review_result = selection_review or cover_letter_review or resume_review or {}
    retry_count = int((selection_bundle or {}).get("telemetry", {}).get("generator_retries", 0))
    retry_count += int(review_result.get("telemetry", {}).get("reviewer_retries", 0))
    trace = build_generation_trace(
        run_id=run_id,
        document_type=payload.document_type,
        application_id=application.id,
        resume_id=master_resume.id,
        provider=provider,
        model=model_name,
        evidence_ids=[str(value) for value in metadata["used_experiences"]],
        reviewer=review_result,
        latency_ms=round((perf_counter() - generation_started) * 1000),
        retry_count=retry_count,
    )
    document = GeneratedDocument(
        user_id=user_id,
        application_id=application.id,
        document_type=payload.document_type,
        content=content,
        structured_content_json=json.dumps(selection_bundle or cover_letter_plan or resume_plan or {}, ensure_ascii=False),
        reviewer_json=json.dumps(selection_review or cover_letter_review or resume_review or {}, ensure_ascii=False),
        run_id=run_id,
        trace_json=json.dumps(trace, ensure_ascii=False),
        used_experiences_json=json.dumps(metadata["used_experiences"]),
        closing_styles_json=json.dumps(metadata["closing_styles"]),
    )
    session.add(document)
    if payload.document_type == "selection_criteria":
        application.selection_confirmations_json = "[]"
        session.add(application)
    if selection_credit_key and user_id is not None:
        session.add(CreditLedger(
            user_id=user_id,
            delta=-1,
            reason="generation",
            reference_id=str(application.id),
            idempotency_key=selection_credit_key,
        ))
    pack_is_complete = payload.document_type == (
        "selection_criteria" if (application.selection_criteria or "").strip() else "cover_letter"
    )
    usage = None
    if user_id is not None and payload.pack_id is not None:
        usage = session.exec(
            select(GenerationUsage).where(
                GenerationUsage.user_id == user_id,
                GenerationUsage.pack_id == payload.pack_id,
            )
        ).first()
    if new_pack_usage and user_id is not None and payload.pack_id is not None:
        usage = GenerationUsage(
            user_id=user_id,
            application_id=application.id,
            pack_id=payload.pack_id,
        )
        session.add(usage)
    if usage is not None and pack_is_complete and usage.completed_at is None:
        usage.completed_at = datetime.utcnow()
        session.add(usage)
    session.commit(); session.refresh(document)
    return document


@app.post("/applications/{application_id}/selection-confirmations", response_model=JobApplication)
def save_selection_confirmations(
    application_id: int,
    payload: SelectionCriteriaConfirmationRequest,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    try:
        plan = json.loads(application.selection_plan_json or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(400, "Selection Criteria plan is invalid. Regenerate the document.") from error
    review_required = criteria_requiring_confirmation(plan)
    confirmed = sorted(set(payload.criteria_ids) & review_required)
    application.selection_confirmations_json = json.dumps(confirmed)
    application.updated_at = datetime.utcnow()
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


@app.post("/applications/{application_id}/prepare-submission")
def prepare_submission(
    application_id: int,
    session: Session = Depends(get_session),
    user_id: UUID | None = Depends(get_current_user),
):
    application = get_for_user(session, JobApplication, application_id, user_id)
    if not application or not application.job_url:
        raise HTTPException(400, "A job URL is required before preparing a submission.")
    try:
        plan = json.loads(application.selection_plan_json or "{}")
        confirmed = set(json.loads(application.selection_confirmations_json or "[]"))
    except json.JSONDecodeError as error:
        raise HTTPException(400, "Selection Criteria review state is invalid. Regenerate and review it again.") from error
    required_confirmations = criteria_requiring_confirmation(plan)
    if required_confirmations - confirmed:
        raise HTTPException(400, "Review and confirm every Transferable or Weak Selection Criterion before continuing.")
    # V1 deliberately returns an explicit user-confirmed task. Platform-specific Playwright adapters come in phase 5.
    return {"mode": "user_confirmed", "job_url": application.job_url, "message": "Open the job URL, review every field and submit it yourself. CAPTCHA and final submission are never automated."}
