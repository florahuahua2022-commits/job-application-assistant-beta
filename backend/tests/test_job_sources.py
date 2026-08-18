import json
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.database import get_session
from app.ingest import parse_job_page
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

    def test_html_discovers_direct_relative_and_extensionless_job_descriptions(self):
        html = """
        <p>Role documents: <a href="https://jobs.example/JDF.pdf">JDF</a></p>
        <p><a href="../files/position.pdf" title="Position Description Form">Download</a></p>
        <p><a href="/document/42">Job Description Form</a></p>
        """
        discoveries = parse_job_page(html, "https://jobs.example/vacancies/123")["discovered_sources"]
        sources = build_job_sources("See the attached JDF.", "https://jobs.example/vacancies/123", discoveries)

        attachments = [source for source in sources if source["source_type"] == "job_description_attachment"]
        self.assertEqual(len(attachments), 3)
        self.assertEqual({source["source_url"] for source in attachments}, {
            "https://jobs.example/JDF.pdf",
            "https://jobs.example/files/position.pdf",
            "https://jobs.example/document/42",
        })
        self.assertTrue(all(source["acquisition_status"] == "discovered" for source in attachments))
        self.assertTrue(all(source["extraction_status"] == "not_attempted" for source in attachments))
        self.assertTrue(all(source["extracted_text"] == "" for source in attachments))

    def test_html_classifies_information_packs_and_mandatory_forms(self):
        html = """
        <ul>
          <li><a href="candidate-pack.pdf">Candidate Information Pack</a></li>
          <li><a href="forms/integrity.pdf">Integrity and Qualification Declaration</a></li>
          <li><a href="forms/consent.docx">Consent Form</a></li>
        </ul>
        """
        discoveries = parse_job_page(html, "https://jobs.example/job/1")["discovered_sources"]
        sources = build_job_sources("Apply online.", "https://jobs.example/job/1", discoveries)

        self.assertEqual([source["source_type"] for source in sources[1:]], [
            "application_instruction_attachment", "mandatory_form", "mandatory_form",
        ])

    def test_unrelated_pdf_is_not_classified_as_job_description(self):
        html = """
        <p><a href="annual-report.pdf">Annual Report</a></p>
        <p><a href="benefits.pdf">Employee benefits</a></p>
        """
        discoveries = parse_job_page(html, "https://jobs.example/job/1")["discovered_sources"]
        sources = build_job_sources("Apply online.", "https://jobs.example/job/1", discoveries)

        self.assertFalse(any(source["source_type"] == "job_description_attachment" for source in sources))
        self.assertFalse(any(source["filename"] == "annual-report.pdf" for source in sources))
        self.assertEqual(sources[-1]["source_type"], "unknown_attachment")

    def test_duplicate_fragments_are_deduplicated_and_unresolved_jdf_is_reconciled(self):
        html = """
        <p>Refer to the attached JDF:
          <a href="files/JDF.pdf#page=1">JDF</a>
          <a href="files/JDF.pdf#page=2">Job Description Form</a>
        </p>
        """
        discoveries = parse_job_page(html, "https://jobs.example/job/1")["discovered_sources"]
        application = self.create_application(
            "Refer to the attached JDF.",
            job_url="https://jobs.example/job/1",
            discovered_sources=discoveries,
        ).json()
        sources = self.client.get(f"/applications/{application['id']}/sources").json()

        jdfs = [source for source in sources if source["source_type"] == "job_description_attachment"]
        self.assertEqual(len(jdfs), 1)
        self.assertEqual(jdfs[0]["source_url"], "https://jobs.example/job/files/JDF.pdf")
        self.assertEqual(json.loads(jdfs[0]["warnings_json"]), [])

    def test_attachment_discovery_is_capped_at_ten(self):
        html = "".join(f'<a href="file-{index}.pdf">Attachment {index}</a>' for index in range(12))
        discoveries = parse_job_page(html, "https://jobs.example/job/1")["discovered_sources"]
        sources = build_job_sources("Apply online.", "https://jobs.example/job/1", discoveries)

        self.assertEqual(len(sources), 11)
        self.assertEqual(len({source["canonical_url_hash"] for source in sources[1:]}), 10)

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
        self.assertEqual(self.client.post(f"/applications/{application_id}/sources/acquire").status_code, 404)

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

    def test_acquisition_endpoint_persists_validated_source_state(self):
        application = self.create_application("Refer to the attached JDF.", discovered_sources=[{
            "url": "https://files.example/jdf.pdf", "label": "JDF", "filename": "jdf.pdf",
        }]).json()

        def fake_acquire(sources):
            attachment = next(source for source in sources if source.source_type == "job_description_attachment")
            attachment.acquisition_status = "fetched"
            attachment.extraction_status = "extracted"
            attachment.content_type = "application/pdf"
            attachment.content_sha256 = "a" * 64
            attachment.extracted_text = "Extracted position description text."

        with patch("app.main.acquire_sources", side_effect=fake_acquire):
            response = self.client.post(f"/applications/{application['id']}/sources/acquire")
        reloaded = self.client.get(f"/applications/{application['id']}/sources").json()

        self.assertEqual(response.status_code, 200)
        attachment = next(source for source in reloaded if source["source_type"] == "job_description_attachment")
        self.assertEqual((attachment["acquisition_status"], attachment["extraction_status"]), ("fetched", "extracted"))
        self.assertEqual(attachment["content_sha256"], "a" * 64)

    def test_sqlite_and_supabase_jobsource_columns_match(self):
        sqlite_columns = {column["name"] for column in inspect(self.engine).get_columns("jobsource")}
        migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
        migration = "\n".join(path.read_text(encoding="utf-8") for path in (
            migrations / "20260818_job_sources.sql",
            migrations / "20260818_job_source_discovery.sql",
        ))

        self.assertEqual(sqlite_columns, {column.name for column in JobSource.__table__.columns})
        for column in sqlite_columns:
            self.assertIn(column, migration)
        self.assertIn("enable row level security", migration)
        self.assertIn("application_id, user_id", migration)


if __name__ == "__main__":
    unittest.main()
