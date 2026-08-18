import json
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.database import get_session
from app.job_sources import build_job_sources, validate_job_source
from app.main import app
from app.models import JobApplication, JobSource


class JobSourceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def create_application(self, job_description: str, **extra):
        return self.client.post("/applications", json={
            "company": "Example Agency",
            "position_title": "Project Officer",
            "job_description": job_description,
            **extra,
        })

    def test_ordinary_advertisement_creates_one_primary_source(self):
        application = self.create_application("Coordinate projects and prepare reports.").json()
        sources = self.client.get(f"/applications/{application['id']}/sources").json()

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["schema_version"], "1.0")
        self.assertEqual(sources[0]["source_type"], "primary_advertisement")
        self.assertEqual(sources[0]["acquisition_status"], "fetched")
        self.assertEqual(sources[0]["extraction_status"], "extracted")
        self.assertEqual(sources[0]["extracted_text"], "Coordinate projects and prepare reports.")
        self.assertEqual(validate_job_source(sources[0]), [])

    def test_attached_jdf_creates_unresolved_source_without_fabricated_text(self):
        application = self.create_application("Submit a CV and refer to the attached JDF.").json()
        sources = self.client.get(f"/applications/{application['id']}/sources").json()

        self.assertEqual([source["source_type"] for source in sources], ["primary_advertisement", "job_description_attachment"])
        expected = sources[1]
        self.assertEqual(expected["acquisition_status"], "discovered")
        self.assertEqual(expected["extraction_status"], "not_attempted")
        self.assertEqual(expected["extracted_text"], "")
        self.assertIsNone(expected["content_sha256"])
        self.assertEqual(expected["discovered_from_source_id"], sources[0]["source_id"])
        self.assertIn("has not been acquired", json.loads(expected["warnings_json"])[0])

    def test_transwa_style_reference_is_recorded_once(self):
        text = "A Cover Letter addressing criteria 1, 2 and 3 as highlighted in the attached JDF."
        sources = build_job_sources(text, "https://example.com/transwa")

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1]["title"], "Job Description Form (JDF)")
        self.assertIn("criteria 1, 2 and 3", json.loads(sources[1]["warnings_json"])[0])
        self.assertEqual(sources[1]["extracted_text"], "")

    def test_named_application_pack_is_an_unresolved_instruction_source(self):
        sources = build_job_sources("Application instructions are contained in the Candidate Information Pack.")

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1]["source_type"], "application_instruction_attachment")
        self.assertEqual(sources[1]["acquisition_status"], "discovered")

    def test_validator_rejects_invalid_statuses(self):
        source = build_job_sources("Coordinate projects.")[0]
        source["acquisition_status"] = "complete"
        source["extraction_status"] = "downloaded"

        errors = validate_job_source(source)

        self.assertIn("Invalid Job Source acquisition_status.", errors)
        self.assertIn("Invalid Job Source extraction_status.", errors)

    def test_sources_are_isolated_by_application_owner(self):
        owner_id, other_id = uuid4(), uuid4()
        with Session(self.engine) as session:
            application = JobApplication(user_id=owner_id, company="Private", position_title="Role", job_description="Refer to the attached JDF.")
            session.add(application); session.commit(); session.refresh(application)
            source = JobSource(application_id=application.id, user_id=owner_id, **build_job_sources(application.job_description)[0])
            session.add(source); session.commit()
            application_id = application.id
        app.dependency_overrides[get_current_user] = lambda: other_id

        self.assertEqual(self.client.get(f"/applications/{application_id}/sources").status_code, 404)

    def test_unrelated_application_update_preserves_sources(self):
        application = self.create_application("Submit a CV and see the attached position description.").json()
        before = self.client.get(f"/applications/{application['id']}/sources").json()

        response = self.client.patch(f"/applications/{application['id']}", json={
            "company": "Renamed Agency", "job_url": "https://example.com/new", "deadline": "2026-12-01",
        })
        after = self.client.get(f"/applications/{application['id']}/sources").json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([(item["source_id"], item["extracted_text"], item["acquisition_status"]) for item in after],
                         [(item["source_id"], item["extracted_text"], item["acquisition_status"]) for item in before])

    def test_sqlite_and_supabase_jobsource_columns_match(self):
        sqlite_columns = {column["name"] for column in inspect(self.engine).get_columns("jobsource")}
        migration = (Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20260818_job_sources.sql").read_text(encoding="utf-8")

        self.assertEqual(sqlite_columns, {column.name for column in JobSource.__table__.columns})
        for column in sqlite_columns:
            self.assertIn(column, migration)
        self.assertIn("enable row level security", migration)
        self.assertIn("application_id, user_id", migration)


if __name__ == "__main__":
    unittest.main()
