from pathlib import Path
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine
from .config import settings

database_url = settings.database_url
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)

if database_url.startswith("sqlite:///"):
    Path("data").mkdir(exist_ok=True)

connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    # P0 safety migration: older releases silently defaulted every profile to
    # permanent residency. Reset that legacy value once so it must be declared
    # again explicitly before it can appear in an application document.
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS appmigration "
            "(migration_key VARCHAR(160) PRIMARY KEY)"
        ))
        migration_key = "20260810_reset_implicit_permanent_residency"
        applied = connection.execute(
            text("SELECT migration_key FROM appmigration WHERE migration_key = :migration_key"),
            {"migration_key": migration_key},
        ).first()
        if not applied:
            if "applicantprofile" in table_names:
                connection.execute(text(
                    "UPDATE applicantprofile SET work_rights = 'not_specified' "
                    "WHERE work_rights = 'permanent_resident'"
                ))
            connection.execute(
                text("INSERT INTO appmigration (migration_key) VALUES (:migration_key)"),
                {"migration_key": migration_key},
            )
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            if "resume" in table_names:
                resume_columns = {column["name"] for column in inspector.get_columns("resume")}
                if "experiences_json" not in resume_columns:
                    connection.execute(text("ALTER TABLE resume ADD COLUMN experiences_json TEXT NOT NULL DEFAULT '[]'"))
                if "ckb_json" not in resume_columns:
                    connection.execute(text("ALTER TABLE resume ADD COLUMN ckb_json TEXT NOT NULL DEFAULT '[]'"))
            if "generateddocument" in table_names:
                document_columns = {column["name"] for column in inspector.get_columns("generateddocument")}
                if "used_experiences_json" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN used_experiences_json TEXT NOT NULL DEFAULT '[]'"))
                if "closing_styles_json" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN closing_styles_json TEXT NOT NULL DEFAULT '[]'"))
                if "structured_content_json" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN structured_content_json TEXT NOT NULL DEFAULT '{}'"))
                if "reviewer_json" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN reviewer_json TEXT NOT NULL DEFAULT '{}'"))
                if "run_id" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN run_id VARCHAR"))
                if "trace_json" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN trace_json TEXT NOT NULL DEFAULT '{}'"))
                if "context_fingerprint" not in document_columns:
                    connection.execute(text("ALTER TABLE generateddocument ADD COLUMN context_fingerprint VARCHAR NOT NULL DEFAULT ''"))
            if "jobapplication" in table_names:
                application_columns = {column["name"] for column in inspector.get_columns("jobapplication")}
                if "job_model_json" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN job_model_json TEXT NOT NULL DEFAULT '{}'"))
                if "evidence_matches_json" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN evidence_matches_json TEXT NOT NULL DEFAULT '{}'"))
                if "selection_plan_json" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN selection_plan_json TEXT NOT NULL DEFAULT '{}'"))
                if "selection_confirmations_json" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN selection_confirmations_json TEXT NOT NULL DEFAULT '[]'"))
                if "quality_check_fingerprint" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN quality_check_fingerprint VARCHAR NOT NULL DEFAULT ''"))
                if "quality_checked_at" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN quality_checked_at TIMESTAMP"))
                if "quality_override_ids_json" not in application_columns:
                    connection.execute(text("ALTER TABLE jobapplication ADD COLUMN quality_override_ids_json TEXT NOT NULL DEFAULT '[]'"))
            if "applicantprofile" in table_names:
                profile_columns = {column["name"] for column in inspector.get_columns("applicantprofile")}
                if "target_direction" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN target_direction TEXT"))
                if "motivation" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN motivation TEXT"))
                if "writing_tone" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN writing_tone VARCHAR NOT NULL DEFAULT 'natural_professional'"))
                if "preferences_notes" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN preferences_notes TEXT"))
                if "work_rights_confirmed" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN work_rights_confirmed BOOLEAN NOT NULL DEFAULT FALSE"))
                if "availability_confirmed" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN availability_confirmed BOOLEAN NOT NULL DEFAULT FALSE"))
                if "motivation_confirmed" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN motivation_confirmed BOOLEAN NOT NULL DEFAULT FALSE"))
    if engine.dialect.name == "sqlite":
        existing = {column["name"] for column in inspector.get_columns("jobapplication")}
        with engine.begin() as connection:
            connection.execute(text("UPDATE jobapplication SET status = 'applied' WHERE status IN ('interview', 'rejected', 'offer')"))
            if "submission_reference" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN submission_reference VARCHAR"))
            if "submitted_at" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN submitted_at DATETIME"))
            if "job_model_json" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN job_model_json TEXT DEFAULT '{}'"))
            if "evidence_matches_json" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN evidence_matches_json TEXT DEFAULT '{}'"))
            if "selection_plan_json" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN selection_plan_json TEXT DEFAULT '{}'"))
            if "selection_confirmations_json" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN selection_confirmations_json TEXT DEFAULT '[]'"))
            if "quality_check_fingerprint" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN quality_check_fingerprint VARCHAR DEFAULT ''"))
            if "quality_checked_at" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN quality_checked_at DATETIME"))
            if "quality_override_ids_json" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN quality_override_ids_json TEXT DEFAULT '[]'"))
            resume_columns = {column["name"] for column in inspector.get_columns("resume")}
            if "experiences_json" not in resume_columns:
                connection.execute(text("ALTER TABLE resume ADD COLUMN experiences_json TEXT DEFAULT '[]'"))
            if "ckb_json" not in resume_columns:
                connection.execute(text("ALTER TABLE resume ADD COLUMN ckb_json TEXT DEFAULT '[]'"))
            document_columns = {column["name"] for column in inspector.get_columns("generateddocument")}
            if "used_experiences_json" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN used_experiences_json TEXT DEFAULT '[]'"))
            if "closing_styles_json" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN closing_styles_json TEXT DEFAULT '[]'"))
            if "structured_content_json" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN structured_content_json TEXT DEFAULT '{}'"))
            if "reviewer_json" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN reviewer_json TEXT DEFAULT '{}'"))
            if "run_id" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN run_id VARCHAR"))
            if "trace_json" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN trace_json TEXT DEFAULT '{}'"))
            if "context_fingerprint" not in document_columns:
                connection.execute(text("ALTER TABLE generateddocument ADD COLUMN context_fingerprint VARCHAR DEFAULT ''"))
            for table_name in ("applicantprofile", "referee", "resume", "jobapplication", "generateddocument"):
                if table_name in inspector.get_table_names():
                    columns = {column["name"] for column in inspector.get_columns(table_name)}
                    if "user_id" not in columns:
                        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN user_id CHAR(32)"))
            if "applicantprofile" in inspector.get_table_names():
                profile_columns = {column["name"] for column in inspector.get_columns("applicantprofile")}
                if "availability_notice" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN availability_notice VARCHAR DEFAULT 'not_specified'"))
                if "target_direction" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN target_direction TEXT"))
                if "motivation" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN motivation TEXT"))
                if "writing_tone" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN writing_tone VARCHAR DEFAULT 'natural_professional'"))
                if "preferences_notes" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN preferences_notes TEXT"))
                if "work_rights_confirmed" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN work_rights_confirmed BOOLEAN DEFAULT 0"))
                if "availability_confirmed" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN availability_confirmed BOOLEAN DEFAULT 0"))
                if "motivation_confirmed" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN motivation_confirmed BOOLEAN DEFAULT 0"))


def get_session():
    with Session(engine) as session:
        yield session
