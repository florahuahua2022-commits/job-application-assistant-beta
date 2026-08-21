import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import JobApplication


class ApplicationDecisionApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)
        self.client.post("/resumes", json={
            "source_text": "Project Officer\nPrepared monthly reports.",
            "experiences_json": "[]",
        })

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def create_application(self, criteria="A current driver's licence is required."):
        return self.client.post("/applications", json={
            "company": "Example Agency",
            "position_title": "Project Officer",
            "job_description": "Project Officer role. Submit your CV and cover letter. Submit a separate Selection Criteria statement.",
            "selection_criteria": f"Selection Criteria\n1. {criteria}",
        }).json()

    @staticmethod
    def no_match(ckb_json, job_model_json):
        import json
        model = json.loads(job_model_json)
        return {"schema_version": "1.0", "matches": [{
            "criteria_id": item["criteria_id"], "matched_evidence": [],
            "match_type": "insufficient", "coverage": "weak", "reasoning": "No support.",
        } for item in model["criteria"]], "unused_evidence": []}

    def test_diagnose_confirm_and_re_evaluate_with_provenance(self):
        application = self.create_application()
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            decision = self.client.post(f"/applications/{application['id']}/decision").json()
            self.assertEqual(decision["status"], "needs_confirmation")
            self.assertTrue(decision["questions"][0]["material"])

            confirmed = self.client.post(
                f"/applications/{application['id']}/decision/confirm",
                json={"question_id": decision["questions"][0]["question_id"], "answer": True},
            ).json()

        self.assertEqual(confirmed["status"], "ready")
        self.assertEqual(confirmed["requirements"][0]["hard_gate_status"], "pass")
        self.assertTrue(confirmed["questions"][0]["answer"])
        self.assertEqual(confirmed["questions"][0]["provenance"], "user_confirmed")
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            self.assertIn('"provenance": "user_confirmed"', stored.application_decision_json)

    def test_negative_confirmation_blocks_and_generation_requires_ready_decision(self):
        application = self.create_application("Current professional registration is required.")
        before = self.client.post("/generate", json={"application_id": application["id"], "document_type": "cover_letter"})
        self.assertEqual(before.status_code, 409)
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            decision = self.client.post(f"/applications/{application['id']}/decision").json()
            blocked = self.client.post(
                f"/applications/{application['id']}/decision/confirm",
                json={"question_id": decision["questions"][0]["question_id"], "answer": False},
            ).json()
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["application_recommendation"], "do_not_apply")
        after = self.client.post("/generate", json={"application_id": application["id"], "document_type": "cover_letter"})
        self.assertEqual(after.status_code, 409)

    def test_job_change_invalidates_decision(self):
        application = self.create_application("Project reporting experience.")
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            self.assertEqual(self.client.post(f"/applications/{application['id']}/decision").status_code, 200)
        updated = self.client.patch(f"/applications/{application['id']}", json={"position_title": "Senior Project Officer"}).json()
        self.assertEqual(updated["application_decision_json"], "{}")

    def test_missing_jdf_keeps_diagnosis_incomplete_and_generation_blocked(self):
        application = self.client.post("/applications", json={
            "company": "Transwa",
            "position_title": "Project Officer",
            "job_description": "Submit a cover letter addressing criteria 1, 2 and 3 in the attached JDF.",
        }).json()
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            decision = self.client.post(f"/applications/{application['id']}/decision").json()

        self.assertEqual(decision["status"], "needs_confirmation")
        self.assertEqual(decision["application_recommendation"], "reconsider")
        self.assertEqual(decision["requirements"], [])
        self.assertEqual(decision["questions"], [])
        self.assertEqual(decision["blocking_issues"][0]["code"], "employer_requirements_incomplete")
        response = self.client.post("/generate", json={
            "application_id": application["id"], "document_type": "cover_letter",
        })
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
