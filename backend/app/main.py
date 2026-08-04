from datetime import datetime
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlmodel import Session, select
from .ai import AIServiceError, generate_draft
from .backup import create_backup, list_backups, read_backup, restore_backup
from .config import settings
from .database import create_db_and_tables, get_session
from .exporter import create_docx, create_pdf, safe_filename
from .ingest import extract_resume_text, import_job_url, parse_job_ad_text
from .models import ApplicantProfile, ApplicantProfilePayload, ApplicantProfileResponse, GeneratedDocument, GeneratedDocumentUpdate, GenerateRequest, JobAdParseRequest, JobAdParseResponse, JobApplication, JobApplicationCreate, JobApplicationStatusUpdate, JobApplicationSubmissionUpdate, JobApplicationUpdate, JobUrlImportRequest, JobUrlImportResponse, QualityCheckIssue, QualityCheckResponse, Referee, RestoreBackupRequest, Resume, ResumeCreate, ResumeUpdate

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
    }


@app.get("/backups")
def get_backups():
    return list_backups()


@app.post("/backups")
def make_backup(session: Session = Depends(get_session)):
    return create_backup(session)


@app.get("/backups/{filename}/download")
def download_backup(filename: str):
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
    values = profile.model_dump(exclude={"created_at"})
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
    polished = re.sub(
        r"(?i)\bI am writing to apply\b",
        "Please accept my application",
        content,
    )
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


@app.get("/profile", response_model=ApplicantProfileResponse | None)
def get_profile(session: Session = Depends(get_session)):
    profile = session.exec(select(ApplicantProfile).order_by(ApplicantProfile.id)).first()
    if not profile:
        return None
    referees = session.exec(
        select(Referee)
        .where(Referee.profile_id == profile.id)
        .order_by(Referee.display_order)
    ).all()
    return profile_response(profile, referees)


@app.put("/profile", response_model=ApplicantProfileResponse)
def save_profile(payload: ApplicantProfilePayload, session: Session = Depends(get_session)):
    if len(payload.referees) > 2:
        raise HTTPException(400, "A maximum of two referees can be saved.")
    profile = session.exec(select(ApplicantProfile).order_by(ApplicantProfile.id)).first()
    profile_values = payload.model_dump(exclude={"referees"})
    if profile:
        for key, value in profile_values.items():
            setattr(profile, key, value)
        profile.updated_at = datetime.utcnow()
    else:
        profile = ApplicantProfile.model_validate(profile_values)
    session.add(profile)
    session.commit()
    session.refresh(profile)

    existing_referees = session.exec(select(Referee).where(Referee.profile_id == profile.id)).all()
    for referee in existing_referees:
        session.delete(referee)
    for index, referee_payload in enumerate(payload.referees, start=1):
        referee = Referee(
            profile_id=profile.id,
            display_order=index,
            **referee_payload.model_dump(),
        )
        session.add(referee)
    session.commit()
    referees = session.exec(
        select(Referee)
        .where(Referee.profile_id == profile.id)
        .order_by(Referee.display_order)
    ).all()
    return profile_response(profile, referees)


@app.post("/resumes", response_model=Resume)
def create_resume(payload: ResumeCreate, session: Session = Depends(get_session)):
    resume = Resume.model_validate(payload)
    session.add(resume); session.commit(); session.refresh(resume)
    return resume


@app.post("/resumes/upload", response_model=Resume)
async def upload_resume(
    file: UploadFile = File(...),
    title: str = Form("Master Resume"),
    session: Session = Depends(get_session),
):
    try:
        source_text = extract_resume_text(file.filename or "resume", await file.read())
    except ValueError as error:
        raise HTTPException(400, str(error))
    current = session.exec(select(Resume).order_by(Resume.updated_at.desc())).first()
    if current:
        current.title = title.strip() or "Master Resume"
        current.source_text = source_text
        current.updated_at = datetime.utcnow()
        resume = current
    else:
        resume = Resume(title=title.strip() or "Master Resume", source_text=source_text)
    session.add(resume); session.commit(); session.refresh(resume)
    return resume


@app.get("/resumes", response_model=list[Resume])
def list_resumes(session: Session = Depends(get_session)):
    return session.exec(select(Resume).order_by(Resume.updated_at.desc())).all()


@app.patch("/resumes/{resume_id}", response_model=Resume)
def update_resume(resume_id: int, payload: ResumeUpdate, session: Session = Depends(get_session)):
    resume = session.get(Resume, resume_id)
    if not resume:
        raise HTTPException(404, "Resume not found.")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(resume, key, value)
    resume.updated_at = datetime.utcnow()
    session.add(resume); session.commit(); session.refresh(resume)
    return resume


@app.post("/applications", response_model=JobApplication)
def create_application(payload: JobApplicationCreate, session: Session = Depends(get_session)):
    application = JobApplication.model_validate(payload)
    session.add(application); session.commit(); session.refresh(application)
    return application


@app.post("/applications/import-url", response_model=JobUrlImportResponse)
def import_application_url(payload: JobUrlImportRequest):
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
def parse_application_ad(payload: JobAdParseRequest, session: Session = Depends(get_session)):
    previous_companies = [item.company for item in session.exec(select(JobApplication)).all()]
    try:
        return JobAdParseResponse.model_validate(parse_job_ad_text(payload.raw_text, previous_companies))
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get("/applications", response_model=list[JobApplication])
def list_applications(session: Session = Depends(get_session)):
    return session.exec(select(JobApplication).order_by(JobApplication.updated_at.desc())).all()


@app.patch("/applications/{application_id}", response_model=JobApplication)
def update_application(
    application_id: int,
    payload: JobApplicationUpdate,
    session: Session = Depends(get_session),
):
    application = session.get(JobApplication, application_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    values = payload.model_dump(exclude_unset=True)
    for required_field in ("company", "position_title", "job_description"):
        if required_field in values and not str(values[required_field] or "").strip():
            raise HTTPException(400, f"{required_field.replace('_', ' ').title()} cannot be blank.")
    for key, value in values.items():
        if key in {"job_url", "selection_criteria", "submission_reference"} and isinstance(value, str) and not value.strip():
            value = None
        setattr(application, key, value.strip() if isinstance(value, str) else value)
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
):
    application = session.get(JobApplication, application_id)
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
):
    application = session.get(JobApplication, application_id)
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
def list_generated_documents(application_id: int, session: Session = Depends(get_session)):
    if not session.get(JobApplication, application_id):
        raise HTTPException(404, "Application not found.")
    return session.exec(
        select(GeneratedDocument)
        .where(GeneratedDocument.application_id == application_id)
        .order_by(GeneratedDocument.created_at.desc())
    ).all()


@app.get("/applications/{application_id}/quality-check", response_model=QualityCheckResponse)
def quality_check(application_id: int, session: Session = Depends(get_session)):
    application = session.get(JobApplication, application_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    documents = session.exec(
        select(GeneratedDocument)
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

    profile = session.exec(select(ApplicantProfile).order_by(ApplicantProfile.id)).first()
    content_to_check = {key: value.content for key, value in latest.items() if key in required}
    cover = content_to_check.get("cover_letter", "")
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

    placeholders = ("[your name]", "[current date]", "[date]", "[company name]", "[hiring manager]")
    cliches = (
        "i am confident", "i am excited", "i am writing to express my interest", "i am writing to apply",
        "i am comfortable learning", "adapt quickly", "learn quickly",
        "proven track record", "dynamic professional", "passionate about",
        "leverage my skills", "well placed",
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

    selection_response = content_to_check.get("selection_criteria", "")
    if (application.selection_criteria or "").strip() and selection_response:
        lowered_selection = selection_response.lower()
        result_signals = ("result", "outcome", "as a result", "this enabled", "leading to", "which allowed")
        if not any(signal in lowered_selection for signal in result_signals):
            issues.append(QualityCheckIssue(
                severity="warning",
                code="selection_result_unclear",
                message="The selection criteria response does not clearly signal outcomes. Check that each example explains what changed or was achieved.",
                document_type="selection_criteria",
            ))

    return QualityCheckResponse(
        ready=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        checked_documents=sorted(content_to_check),
    )


@app.patch("/documents/{document_id}", response_model=GeneratedDocument)
def update_generated_document(document_id: int, payload: GeneratedDocumentUpdate, session: Session = Depends(get_session)):
    document = session.get(GeneratedDocument, document_id)
    if not document:
        raise HTTPException(404, "Generated document not found.")
    document.content = payload.content
    session.add(document); session.commit(); session.refresh(document)
    return document


@app.get("/documents/{document_id}/export")
def export_generated_document(document_id: int, format: str = Query(pattern="^(docx|pdf)$"), session: Session = Depends(get_session)):
    document = session.get(GeneratedDocument, document_id)
    if not document:
        raise HTTPException(404, "Generated document not found.")
    application = session.get(JobApplication, document.application_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    label = {
        "tailored_resume": "Tailored_Resume",
        "cover_letter": "Cover_Letter",
        "selection_criteria": "Selection_Criteria",
    }.get(document.document_type, document.document_type)
    filename = safe_filename(f"{application.position_title}_{label}")
    if format == "docx":
        payload = create_docx(document.content, label.replace("_", " "))
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        payload = create_pdf(document.content, label.replace("_", " "))
        media_type = "application/pdf"
    return Response(payload, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}.{format}"'})


@app.get("/applications/{application_id}/export-pack")
def export_application_pack(application_id: int, format: str = Query(pattern="^(docx|pdf)$"), session: Session = Depends(get_session)):
    application = session.get(JobApplication, application_id)
    if not application:
        raise HTTPException(404, "Application not found.")
    documents = session.exec(
        select(GeneratedDocument)
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
            payload = create_docx(document.content, label.replace("_", " ")) if format == "docx" else create_pdf(document.content, label.replace("_", " "))
            archive.writestr(f"{safe_filename(application.position_title)}_{label}.{format}", payload)
    filename = safe_filename(f"{application.position_title}_Application_Pack_{format.upper()}")
    return Response(archive_stream.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}.zip"'})


@app.post("/generate", response_model=GeneratedDocument)
def generate(payload: GenerateRequest, session: Session = Depends(get_session)):
    application = session.get(JobApplication, payload.application_id)
    master_resume = session.exec(select(Resume).order_by(Resume.updated_at.desc())).first()
    if not application or not master_resume:
        raise HTTPException(400, "Create a Master Resume and job application first.")
    profile = session.exec(select(ApplicantProfile).order_by(ApplicantProfile.id)).first()
    profile_text = None
    if profile:
        profile_text = "\n".join(filter(None, [
            f"Name: {' '.join(filter(None, [profile.title, profile.first_name, profile.last_name]))}",
            f"Phone: {profile.phone}",
            f"Email: {profile.email}",
            f"Address: {', '.join(filter(None, [profile.postal_address, profile.suburb, profile.state, profile.postcode, profile.country]))}",
            f"Work rights: {profile.work_rights.replace('_', ' ')}",
            f"Confirmed availability wording: {confirmed_availability_wording(profile.availability_notice)}",
        ]))
    try:
        content = generate_draft(
            master_resume.source_text,
            application.job_description,
            payload.document_type,
            application.selection_criteria,
            profile_text,
            application.position_title,
            application.company,
        )
        if profile and payload.document_type in {"tailored_resume", "cover_letter"}:
            content = enforce_profile_contact(content, profile, payload.document_type)
        if payload.document_type == "cover_letter":
            content = auto_polish_cover_letter(content, profile, application.job_description)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(400, str(error))
    except AIServiceError as error:
        raise HTTPException(502, str(error))
    document = GeneratedDocument(application_id=application.id, document_type=payload.document_type, content=content)
    session.add(document); session.commit(); session.refresh(document)
    return document


@app.post("/applications/{application_id}/prepare-submission")
def prepare_submission(application_id: int, session: Session = Depends(get_session)):
    application = session.get(JobApplication, application_id)
    if not application or not application.job_url:
        raise HTTPException(400, "A job URL is required before preparing a submission.")
    # V1 deliberately returns an explicit user-confirmed task. Platform-specific Playwright adapters come in phase 5.
    return {"mode": "user_confirmed", "job_url": application.job_url, "message": "Open the job URL, review every field and submit it yourself. CAPTCHA and final submission are never automated."}
