import json
import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.application_decision import decision_inputs
from app.ai import AIServiceError
from app.application_requirements import empty_application_requirements
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, JobSource, Resume
from app.outcome_learning import build_submission_snapshot
from app.release_state import generation_inputs_fingerprint


class RealUserRegressionTests(unittest.TestCase):
    def test_reviewer_provider_failure_keeps_same_draft_for_safe_retry(self):
        application_id = self.seed(required=("cover_letter",))
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            profile = session.exec(select(ApplicantProfile)).first()
            requirements = json.loads(application.application_requirements_json)
            requirements["source"] = "source_aware_parser"
            application.application_requirements_json = json.dumps(requirements)
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.add(application); session.commit()
        draft = "Application for Office Administrator\n\n" + "Grounded administration support. " * 40
        with patch("app.main.match_evidence_batch", return_value={"schema_version": "1.0", "matches": [], "unused_evidence": []}), patch(
            "app.main.generate_draft", return_value=draft
        ), patch("app.main.repair_cover_letter", side_effect=AIServiceError("Unterminated string at line 144")):
            failed = self.client.post("/generate", json={"application_id": application_id, "document_type": "cover_letter"})

        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertNotIn("Unterminated", failed.json()["detail"]["message"])
        document_id = failed.json()["detail"]["document_id"]
        documents = self.client.get(f"/applications/{application_id}/documents").json()
        self.assertEqual([item["id"] for item in documents], [document_id])
        self.assertEqual(json.loads(documents[0]["reviewer_json"])["status"], "provider_failed")

        with patch("app.main.review_cover_letter", return_value={"status": "pass", "results": []}):
            retried = self.client.post(f"/documents/{document_id}/review")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["id"], document_id)
        self.assertEqual(json.loads(retried.json()["reviewer_json"])["status"], "pass")

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def sessions():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def requirements(*required):
        result = empty_application_requirements("User-confirmed requirements", "user_supplied")
        result["review_status"] = "confirmed"
        for document in result["documents"].values():
            document.update(requirement="not_required", format="not_applicable")
        for name in required:
            result["documents"][name].update(requirement="required", format="standalone")
        return result

    def seed(self, required=("resume", "cover_letter")):
        with Session(self.engine) as session:
            profile = ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com")
            resume = Resume(source_text="Alex Morgan\nOffice administration experience.", ckb_json="[]")
            requirements = self.requirements(*required)
            application = JobApplication(
                company="SRG Global", position_title="Office Administrator", job_url="https://example.com/apply",
                job_description="Office administration and procurement support.",
                application_requirements_json=json.dumps(requirements), job_model_json='{"schema_version":"1.0","criteria":[],"limit_scope":"unspecified"}',
            )
            session.add_all([profile, resume, application]); session.flush()
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.add(application); session.commit(); session.refresh(application)
            return application.id

    def test_not_required_selection_criteria_is_rejected_and_cannot_block_final_check(self):
        application_id = self.seed()
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            application.selection_plan_json = '{"items":[{"criteria_id":"C1","evidence_status":"weak"}]}'
            session.add_all([
                GeneratedDocument(application_id=application_id, document_type="tailored_resume", content="Alex Morgan\n## Professional Summary\nAdmin\n## Key Skills\nAdmin\n## Work Experience\nAdmin", reviewer_json='{"status":"pass"}', structured_content_json='{"schema_version":"1.1","roles":[]}'),
                GeneratedDocument(application_id=application_id, document_type="cover_letter", content="Application for Office Administrator at SRG Global. " + "evidence " * 220, reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=application_id, document_type="selection_criteria", content="Unnecessary", reviewer_json='{"status":"fail","generation_status":"needs_ckb_update","results":[]}'),
            ])
            session.commit()

        rejected = self.client.post("/generate", json={"application_id": application_id, "document_type": "selection_criteria"})
        final = self.client.get(f"/applications/{application_id}/quality-check").json()
        release = self.client.get(f"/applications/{application_id}/release-checklist").json()
        exported = self.client.get(f"/applications/{application_id}/export-pack?format=docx&template=classic")

        self.assertEqual(rejected.status_code, 409)
        self.assertNotIn("selection_criteria", final["checked_documents"])
        self.assertFalse(any(item.get("document_type") == "selection_criteria" for item in final["issues"]))
        self.assertTrue(release["checks"]["selection_confirmations"]["ready"])
        self.assertNotIn("selection_criteria", [item["document_type"] for item in self.client.get(f"/applications/{application_id}/documents").json()])
        self.assertEqual(exported.status_code, 200)
        with ZipFile(BytesIO(exported.content)) as archive:
            self.assertFalse(any("Selection_Criteria" in name for name in archive.namelist()))

    def test_job_edit_updates_primary_source_and_makes_old_documents_historical(self):
        application_id = self.seed()
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            session.add(JobSource(
                application_id=application_id, source_id="primary", source_type="primary_advertisement",
                title="Job advertisement", label="Primary advertisement", acquisition_status="fetched",
                extraction_status="extracted", classification_confidence="high", extracted_text=application.job_description,
            ))
            session.add(GeneratedDocument(application_id=application_id, document_type="tailored_resume", content="Old CV", reviewer_json='{"status":"pass"}'))
            session.commit()

        updated = self.client.patch(f"/applications/{application_id}", json={"job_description": "Changed procurement and workforce administration duties."})
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            profile = session.exec(select(ApplicantProfile)).first()
            requirements = self.requirements("resume", "cover_letter")
            application.application_requirements_json = json.dumps(requirements)
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.commit()
        documents = self.client.get(f"/applications/{application_id}/documents").json()
        final = self.client.get(f"/applications/{application_id}/quality-check").json()
        with Session(self.engine) as session:
            source = session.exec(select(JobSource).where(JobSource.application_id == application_id)).first()

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(documents, [])
        self.assertIn("Changed procurement", source.extracted_text)
        self.assertIn("stale_generated_document", [item["code"] for item in final["issues"]])
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            resume = session.exec(select(Resume)).first()
            profile = session.exec(select(ApplicantProfile)).first()
            session.add(GeneratedDocument(
                application_id=application_id, document_type="tailored_resume", content="Current CV",
                reviewer_json='{"status":"pass"}',
                trace_json=json.dumps({"input_fingerprint": generation_inputs_fingerprint(application, resume, profile)}),
            ))
            session.commit()
            self.assertEqual(len(session.exec(select(GeneratedDocument).where(GeneratedDocument.application_id == application_id)).all()), 2)
        self.assertEqual(len(self.client.get(f"/applications/{application_id}/documents").json()), 1)

    def test_manual_cover_letter_edit_can_be_re_reviewed(self):
        application_id = self.seed(required=("cover_letter",))
        with Session(self.engine) as session:
            document = GeneratedDocument(
                application_id=application_id, document_type="cover_letter", content="Edited grounded letter.",
                reviewer_json="{}", structured_content_json='{"priorities":["administration"]}',
            )
            session.add(document); session.commit(); session.refresh(document); document_id = document.id

        with patch("app.main.review_cover_letter", return_value={"status": "pass", "results": []}):
            response = self.client.post(f"/documents/{document_id}/review")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.json()["reviewer_json"])["status"], "pass")

    def test_manual_selection_criteria_edit_preserves_plan_for_re_review(self):
        application_id = self.seed(required=("selection_criteria",))
        plan = {"items": [{"criteria_id": "C1", "criteria_text": "Communication", "matched_evidence": [], "allocated_word_limit": 100}]}
        bundle = {
            "selection_plan": plan,
            "responses": [{"criteria_id": "C1", "final_response": "Original.", "evidence_used": [], "star": {"situation": "", "task": "", "action": "", "result": ""}}],
        }
        with Session(self.engine) as session:
            document = GeneratedDocument(
                application_id=application_id, document_type="selection_criteria",
                content="## Communication\n\nEdited grounded response.", reviewer_json="{}",
                structured_content_json=json.dumps(bundle),
            )
            session.add(document); session.commit(); session.refresh(document); document_id = document.id

        with patch("app.main.review_selection_criteria_batch", return_value={"status": "pass", "results": []}):
            response = self.client.post(f"/documents/{document_id}/review")

        self.assertEqual(response.status_code, 200)
        structured = json.loads(response.json()["structured_content_json"])
        self.assertEqual(structured["responses"][0]["final_response"], "Edited grounded response.")

    def test_submission_snapshot_excludes_unrequired_selection_criteria(self):
        application = JobApplication(company="SRG Global", position_title="Office Administrator", job_description="JD")
        documents = [
            GeneratedDocument(id=1, application_id=1, document_type="tailored_resume", content="CV"),
            GeneratedDocument(id=2, application_id=1, document_type="cover_letter", content="Letter"),
            GeneratedDocument(id=3, application_id=1, document_type="selection_criteria", content="Historical SC"),
        ]
        snapshot = build_submission_snapshot(application, documents, [], "Australia", [], documents[0].created_at, ("tailored_resume", "cover_letter"))
        self.assertNotIn("selection_criteria", snapshot["documents"])


if __name__ == "__main__":
    unittest.main()
