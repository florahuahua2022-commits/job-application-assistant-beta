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
    if engine.dialect.name == "sqlite":
        inspector = inspect(engine)
        existing = {column["name"] for column in inspector.get_columns("jobapplication")}
        with engine.begin() as connection:
            connection.execute(text("UPDATE jobapplication SET status = 'applied' WHERE status IN ('interview', 'rejected', 'offer')"))
            if "submission_reference" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN submission_reference VARCHAR"))
            if "submitted_at" not in existing:
                connection.execute(text("ALTER TABLE jobapplication ADD COLUMN submitted_at DATETIME"))
            for table_name in ("applicantprofile", "referee", "resume", "jobapplication", "generateddocument"):
                if table_name in inspector.get_table_names():
                    columns = {column["name"] for column in inspector.get_columns(table_name)}
                    if "user_id" not in columns:
                        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN user_id CHAR(32)"))
            if "applicantprofile" in inspector.get_table_names():
                profile_columns = {column["name"] for column in inspector.get_columns("applicantprofile")}
                if "availability_notice" not in profile_columns:
                    connection.execute(text("ALTER TABLE applicantprofile ADD COLUMN availability_notice VARCHAR DEFAULT 'not_specified'"))


def get_session():
    with Session(engine) as session:
        yield session
