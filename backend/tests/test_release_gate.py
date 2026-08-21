import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, QualityCheckIssue, QualityCheckResponse, Resume
from app.release_state import details_fingerprint, fingerprint, pack_fingerprint


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_current_user] = lambda: None
        with Session(self.engine) as session:
            profile = ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com")
            application = JobApplication(
                company="Example Agency", position_title="Project Officer", job_url="https://example.com/apply",
                job_description="Provide project support.", application_requirements_json=json.dumps({
                    "schema_version": "1.0", "source": "user_supplied", "review_status": "confirmed", "completeness": "complete",
                    "documents": {"resume": {"requirement": "required"}, "cover_letter": {"requirement": "required"}, "selection_criteria": {"requirement": "not_required"}},
                }),
            )
            session.add_all([profile, Resume(source_text="Alex Morgan", ckb_json="[]"), application]); session.flush()
            resume = GeneratedDocument(application_id=application.id, document_type="tailored_resume", content="Alex Morgan resume", reviewer_json='{"status":"pass"}', structured_content_json='{"schema_version":"1.1","roles":[]}')
            cover = GeneratedDocument(application_id=application.id, document_type="cover_letter", content="Application for Project Officer at Example Agency. " + "evidence " * 220, reviewer_json='{"status":"pass"}')
            session.add_all([resume, cover]); session.commit(); session.refresh(application); session.refresh(resume)
            self.application_id, self.resume_id = application.id, resume.id
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear(); self.engine.dispose()

    def seed_release(self, *, format="docx", template="classic", pack_blocking=False, ats_ready=True):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            profile = session.exec(select(ApplicantProfile)).first()
            documents = {item.document_type: item for item in session.exec(select(GeneratedDocument)).all()}
            pack_result = {"status": "fail" if pack_blocking else "pass", "blocks_release": pack_blocking, "skipped": not pack_blocking, "skip_reason": "No comparison candidates." if not pack_blocking else None, "results": [{"document_type": "cover_letter", "issues": [{"blocks_release": True}]}] if pack_blocking else []}
            resume = documents["tailored_resume"]
            ats_result = {"status": "pass" if ats_ready else "fail", "ready": ats_ready, "document_id": resume.id, "format": format, "template": template, "checks": [], "keywords": []}
            application.release_state_json = json.dumps({
                "schema_version": "1.0",
                "details_confirmation": {"fingerprint": details_fingerprint(application, profile)},
                "pack_review": {"fingerprint": pack_fingerprint(application, profile, documents), "result": pack_result},
                "ats": {"document_id": resume.id, "content_sha256": fingerprint(resume.content), "format": format, "template": template, "result": ats_result},
            })
            session.add(application); session.commit()

    @staticmethod
    def final(ready=True):
        issues = [] if ready else [QualityCheckIssue(severity="error", code="blocked", message="Blocked", blocks_release=True)]
        return QualityCheckResponse(ready=ready, issues=issues, checked_documents=["cover_letter", "tailored_resume"])

    def test_final_check_failure_and_missing_release_state_block_preparation(self):
        with patch("app.main.quality_check", return_value=self.final(False)):
            response = self.client.post(f"/applications/{self.application_id}/prepare-submission")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["detail"]["checklist"]["checks"]["final_check"]["ready"])

    def test_blocking_pack_review_cannot_be_ready(self):
        self.seed_release(pack_blocking=True)
        with patch("app.main.quality_check", return_value=self.final()):
            result = self.client.get(f"/applications/{self.application_id}/release-checklist").json()
        self.assertFalse(result["checks"]["pack_review"]["ready"]); self.assertFalse(result["ready"])

    def test_ats_identity_must_match_document_format_template_and_content(self):
        self.seed_release()
        with patch("app.main.quality_check", return_value=self.final()):
            self.assertTrue(self.client.get(f"/applications/{self.application_id}/release-checklist?format=docx&template=classic").json()["checks"]["ats"]["ready"])
            self.assertFalse(self.client.get(f"/applications/{self.application_id}/release-checklist?format=pdf&template=classic").json()["checks"]["ats"]["ready"])
            self.assertFalse(self.client.get(f"/applications/{self.application_id}/release-checklist?format=docx&template=modern").json()["checks"]["ats"]["ready"])
        with Session(self.engine) as session:
            resume = session.get(GeneratedDocument, self.resume_id); resume.content += " edited"; session.commit()
        with patch("app.main.quality_check", return_value=self.final()):
            self.assertFalse(self.client.get(f"/applications/{self.application_id}/release-checklist").json()["checks"]["ats"]["ready"])

    def test_skipped_pack_and_advisory_ats_can_prepare(self):
        self.seed_release()
        with patch("app.main.quality_check", return_value=self.final()):
            response = self.client.post(f"/applications/{self.application_id}/prepare-submission?format=docx&template=classic")
        self.assertEqual(response.status_code, 200)

    def test_failed_ats_blocks_and_clean_release_can_be_marked_ready(self):
        self.seed_release(ats_ready=False)
        with patch("app.main.quality_check", return_value=self.final()):
            self.assertEqual(self.client.post(f"/applications/{self.application_id}/prepare-submission").status_code, 409)
        self.seed_release()
        with patch("app.main.quality_check", return_value=self.final()):
            response = self.client.patch(f"/applications/{self.application_id}/status", json={"status": "ready_to_apply"})
        self.assertEqual(response.status_code, 200)

    def test_cross_user_release_state_is_not_accessible(self):
        app.dependency_overrides[get_current_user] = lambda: uuid4()
        self.assertEqual(self.client.get(f"/applications/{self.application_id}/release-checklist").status_code, 404)


if __name__ == "__main__":
    unittest.main()
