from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4
from sqlmodel import Field, SQLModel


class ApplicationStatus(str, Enum):
    draft = "draft"
    ready_to_apply = "ready_to_apply"
    applied = "applied"


class Resume(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID | None = Field(default=None, index=True)
    title: str = "Master Resume"
    source_text: str
    experiences_json: str = "[]"
    ckb_json: str = "[]"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ApplicantProfile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID | None = Field(default=None, index=True)
    title: str | None = None
    first_name: str
    last_name: str
    preferred_name: str | None = None
    phone: str
    email: str
    postal_address: str | None = None
    suburb: str | None = None
    state: str = "WA"
    postcode: str | None = None
    country: str = "Australia"
    work_rights: str = "not_specified"
    work_rights_confirmed: bool = False
    availability_notice: str = "not_specified"
    availability_confirmed: bool = False
    target_direction: str | None = None
    motivation: str | None = None
    motivation_confirmed: bool = False
    writing_tone: str = "natural_professional"
    preferences_notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Referee(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID | None = Field(default=None, index=True)
    profile_id: int = Field(foreign_key="applicantprofile.id")
    display_order: int
    organisation: str
    name: str
    position_title: str
    phone: str
    relationship: str
    email: str
    postal_address: str | None = None
    suburb: str | None = None
    state: str = "WA"
    postcode: str | None = None
    country: str = "Australia"


class JobApplication(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID | None = Field(default=None, index=True)
    company: str
    position_title: str
    job_url: str | None = None
    job_description: str
    selection_criteria: str | None = None
    job_model_json: str = "{}"
    evidence_matches_json: str = "{}"
    selection_plan_json: str = "{}"
    selection_confirmations_json: str = "[]"
    quality_check_fingerprint: str = ""
    quality_checked_at: datetime | None = None
    quality_override_ids_json: str = "[]"
    deadline: date | None = None
    status: ApplicationStatus = ApplicationStatus.draft
    submission_reference: str | None = None
    submitted_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GeneratedDocument(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID | None = Field(default=None, index=True)
    application_id: int = Field(foreign_key="jobapplication.id")
    document_type: str
    content: str
    structured_content_json: str = "{}"
    reviewer_json: str = "{}"
    run_id: str = Field(default_factory=lambda: str(uuid4()), index=True)
    trace_json: str = "{}"
    context_fingerprint: str = ""
    used_experiences_json: str = "[]"
    closing_styles_json: str = "[]"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GenerationUsage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID = Field(index=True)
    application_id: int | None = None
    pack_id: UUID
    generated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class CreditLedger(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID = Field(index=True)
    delta: int
    reason: str
    reference_id: str | None = None
    idempotency_key: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class Referral(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    inviter_user_id: UUID = Field(index=True)
    invited_user_id: UUID = Field(unique=True, index=True)
    status: str = "earned"
    reward_credits: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContentCheckOverride(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # Audit references deliberately remain immutable identifiers rather than
    # cascading foreign keys, so a normal backup restore cannot erase history.
    application_id: int = Field(index=True)
    document_id: int | None = None
    applicant_user_id: UUID | None = Field(default=None, index=True)
    admin_user_id: UUID = Field(index=True)
    issue_id: str = Field(index=True)
    issue_code: str
    document_type: str | None = None
    original_issue_json: str
    reason: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    revoked_at: datetime | None = None
    export_count: int = 0
    last_exported_at: datetime | None = None
    earned_at: datetime | None = Field(default_factory=datetime.utcnow)


class ResumeCreate(SQLModel):
    title: str = "Master Resume"
    source_text: str
    experiences_json: str = "[]"


class RefereePayload(SQLModel):
    organisation: str
    name: str
    position_title: str
    phone: str
    relationship: str
    email: str
    postal_address: str | None = None
    suburb: str | None = None
    state: str = "WA"
    postcode: str | None = None
    country: str = "Australia"


class ApplicantProfilePayload(SQLModel):
    title: str | None = None
    first_name: str
    last_name: str
    preferred_name: str | None = None
    phone: str
    email: str
    postal_address: str | None = None
    suburb: str | None = None
    state: str = "WA"
    postcode: str | None = None
    country: str = "Australia"
    work_rights: str = "not_specified"
    work_rights_confirmed: bool = False
    availability_notice: str = "not_specified"
    availability_confirmed: bool = False
    target_direction: str | None = None
    motivation: str | None = None
    motivation_confirmed: bool = False
    writing_tone: str = "natural_professional"
    preferences_notes: str | None = None
    referees: list[RefereePayload] = Field(default_factory=list)


class ApplicantProfileResponse(ApplicantProfilePayload):
    id: int
    updated_at: datetime


class ResumeUpdate(SQLModel):
    title: str | None = None
    source_text: str | None = None
    experiences_json: str | None = None


class JobApplicationCreate(SQLModel):
    company: str
    position_title: str
    job_url: str | None = None
    job_description: str
    selection_criteria: str | None = None
    deadline: date | None = None


class JobApplicationUpdate(SQLModel):
    company: str | None = None
    position_title: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    selection_criteria: str | None = None
    submission_reference: str | None = None
    deadline: date | None = None


class JobUrlImportRequest(SQLModel):
    job_url: str


class JobUrlImportResponse(SQLModel):
    company: str = ""
    position_title: str = ""
    job_description: str = ""
    job_url: str
    source: str


class JobAdParseRequest(SQLModel):
    raw_text: str


class JobAdParseResponse(SQLModel):
    company: str = ""
    position_title: str = ""
    job_description: str
    selection_criteria: str = ""
    warnings: list[str] = Field(default_factory=list)


class JobApplicationSubmissionUpdate(SQLModel):
    submission_reference: str | None = None
    submitted_at: datetime | None = None


class JobApplicationStatusUpdate(SQLModel):
    status: ApplicationStatus


class GenerateRequest(SQLModel):
    application_id: int
    document_type: str  # tailored_resume | cover_letter | selection_criteria | ats_analysis
    pack_id: UUID | None = None


class ReferralClaimRequest(SQLModel):
    referral_code: str


class SelectionCriteriaAccessResponse(SQLModel):
    unlimited: bool = False
    included_credits: int = 2
    referral_credits: int = 0
    used_credits: int = 0
    remaining_credits: int | None = None
    referral_code: str | None = None
    referral_claimed: bool = False


class SelectionCriteriaConfirmationRequest(SQLModel):
    criteria_ids: list[str] = Field(default_factory=list)


class GeneratedDocumentUpdate(SQLModel):
    content: str


class QualityCheckIssue(SQLModel):
    issue_id: str = ""
    severity: str  # error | warning | information
    code: str
    message: str
    document_type: str | None = None
    excerpt: str | None = None
    source: str | None = None
    rule: str | None = None
    recommended_action: str | None = None
    overridden: bool = False
    override_reason: str | None = None


class ContentCheckOverrideRequest(SQLModel):
    issue_ids: list[str] = Field(default_factory=list)
    reason: str


class QualityCheckResponse(SQLModel):
    ready: bool
    issues: list[QualityCheckIssue]
    checked_documents: list[str]


class ContentCheckOverrideResponse(SQLModel):
    application_id: int
    overridden_issue_ids: list[str]
    quality_check: QualityCheckResponse


class ResumeContentCheckItem(SQLModel):
    field: str
    label: str
    value: str
    status: str  # matched | review | missing
    message: str


class ResumeContentCheckResponse(SQLModel):
    ready: bool
    matched_count: int
    review_count: int
    missing_count: int
    items: list[ResumeContentCheckItem]


class RestoreBackupRequest(SQLModel):
    confirm: bool = False
