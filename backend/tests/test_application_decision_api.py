import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.ai import AIServiceError
from app.database import get_session
from app.main import app
from app.job_model import build_job_model
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
            self.assertTrue(decision["diagnosed_at"])

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

    def test_new_application_keeps_a_resume_snapshot(self):
        application = self.create_application()
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            snapshot = json.loads(stored.resume_snapshot_json)
        self.assertEqual(snapshot["source_text"], "Project Officer\nPrepared monthly reports.")
        self.assertTrue(snapshot["ckb_json"])

    def test_negative_confirmation_does_not_gate_generation(self):
        application = self.create_application("Current professional registration is required.")
        before = self.client.post("/generate", json={"application_id": application["id"], "document_type": "cover_letter"})
        self.assertNotEqual(before.status_code, 409)
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            decision = self.client.post(f"/applications/{application['id']}/decision").json()
            blocked = self.client.post(
                f"/applications/{application['id']}/decision/confirm",
                json={"question_id": decision["questions"][0]["question_id"], "answer": False},
            ).json()
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["application_recommendation"], "do_not_apply")
        after = self.client.post("/generate", json={"application_id": application["id"], "document_type": "cover_letter"})
        self.assertNotEqual(after.status_code, 409)

    def test_job_change_invalidates_decision(self):
        application = self.create_application("Project reporting experience.")
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            self.assertEqual(self.client.post(f"/applications/{application['id']}/decision").status_code, 200)
        updated = self.client.patch(f"/applications/{application['id']}", json={"position_title": "Senior Project Officer"}).json()
        self.assertEqual(updated["application_decision_json"], "{}")

    def test_missing_jdf_keeps_diagnosis_incomplete_without_gating_generation(self):
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
        self.assertNotEqual(response.status_code, 409)

    def test_diagnosis_rebuilds_stale_inferred_bennco_requirements(self):
        jd = """About Bennco Group
Bennco Group is a multi-disciplinary building and construction contractor supporting Tier 1 clients across the Pilbara and wider WA. From our beginnings in Tom Price, we’ve grown into a trusted provider delivering high-quality building, plumbing, and electrical projects across regional WA.
About You
Project and site administration experience.
Strong planning and organisation skills.
Microsoft Office and relevant project systems experience.
Experience in construction or mining (preferred).
Our Values
Pride & Commitment – We own our work and get the job done.
Growth & Improvement – We push ourselves to evolve and excel.
Family & Loyalty – We look after our people and create a welcoming team culture.
Trust & Respect – We communicate openly and honour our commitments.
"""
        application = self.client.post("/applications", json={
            "company": "Bennco Group", "position_title": "Project Administrator - Capital Projects",
            "job_description": jd,
        }).json()
        legitimate = build_job_model(jd, None, "Project Administrator - Capital Projects", "Bennco Group")
        stale = {**legitimate, "criteria": [
            *legitimate["criteria"],
            *[{"criteria_id": f"NOISE{index}", "criteria_text": text, "criteria_type": "inferred",
               "criterion_categories": ["behaviour"], "primary_category": "behaviour",
               "key_competencies": [], "source": "job_description"}
              for index, text in enumerate([
                  "Bennco Group is a multi-disciplinary building and construction contractor supporting Tier 1 clients across the Pilbara and wider WA. From our beginnings in Tom Price, we’ve grown into a trusted provider delivering high-quality building, plumbing, and electrical projects across regional WA.",
                  "Pride & Commitment – We own our work and get the job done.",
                  "Growth & Improvement – We push ourselves to evolve and excel.",
                  "Family & Loyalty – We look after our people and create a welcoming team culture.",
                  "Trust & Respect – We communicate openly and honour our commitments.",
              ])],
        ]}
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            stored.job_model_json = json.dumps(stale, ensure_ascii=False)
            stored.evidence_matches_json = '{"matches":[{"criteria_id":"NOISE0"}]}'
            stored.application_decision_json = '{"status":"stale"}'
            stored.selection_plan_json = '{"items":[{"criteria_id":"NOISE0"}]}'
            stored.selection_confirmations_json = '["NOISE0"]'
            stored.release_state_json = '{"pack_review":{"status":"pass"},"ats":{"ready":true}}'
            session.add(stored); session.commit()

        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            decision = self.client.post(f"/applications/{application['id']}/decision")

        self.assertEqual(decision.status_code, 200, decision.text)
        cards = [item["requirement_text"] for item in decision.json()["requirements"]]
        self.assertEqual(cards, [item["criteria_text"] for item in legitimate["criteria"]])
        self.assertIn("Experience in construction or mining (preferred).", cards)
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            self.assertEqual(json.loads(stored.job_model_json), legitimate)
            self.assertNotIn("NOISE0", stored.evidence_matches_json)
            self.assertEqual((stored.selection_plan_json, stored.selection_confirmations_json), ("{}", "[]"))
            release = json.loads(stored.release_state_json)
            self.assertTrue(release["generation_contract_required"])
            self.assertNotIn("pack_review", release)
            self.assertNotIn("ats", release)

            stored.job_model_json = json.dumps(stale, ensure_ascii=False)
            stored.evidence_matches_json = '{"matches":[{"criteria_id":"NOISE0"}]}'
            stored.application_decision_json = '{"status":"stale"}'
            stored.selection_plan_json = '{"items":[{"criteria_id":"NOISE0"}]}'
            stored.selection_confirmations_json = '["NOISE0"]'
            stored.release_state_json = '{"pack_review":{"status":"pass"},"ats":{"ready":true}}'
            session.add(stored); session.commit()

        with patch("app.main.match_evidence_batch", side_effect=AIServiceError("Evidence matching failed. Please try again.")):
            failed = self.client.post(f"/applications/{application['id']}/decision")
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json(), {"detail": "Evidence matching failed. Please try again."})
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            self.assertEqual(json.loads(stored.job_model_json), legitimate)
            self.assertEqual((stored.evidence_matches_json, stored.application_decision_json), ("{}", "{}"))
            self.assertEqual((stored.selection_plan_json, stored.selection_confirmations_json), ("{}", "[]"))
            release = json.loads(stored.release_state_json)
            self.assertTrue(release["generation_contract_required"])
            self.assertNotIn("pack_review", release)
            self.assertNotIn("ats", release)

        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            retried = self.client.post(f"/applications/{application['id']}/decision")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(
            [item["requirement_text"] for item in retried.json()["requirements"]],
            [item["criteria_text"] for item in legitimate["criteria"]],
        )

    def test_identical_inferred_model_diagnosis_preserves_dependent_state(self):
        jd = """About You
Project and site administration experience.
Strong planning and organisation skills.
Experience in construction or mining (preferred).
"""
        application = self.client.post("/applications", json={
            "company": "Example Contractor", "position_title": "Project Administrator",
            "job_description": jd,
        }).json()
        with patch("app.main.match_evidence_batch", side_effect=self.no_match):
            first = self.client.post(f"/applications/{application['id']}/decision")
        self.assertEqual(first.status_code, 200, first.text)
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            stored.selection_plan_json = '{"items":[{"criteria_id":"CURRENT"}]}'
            stored.selection_confirmations_json = '["CURRENT"]'
            stored.release_state_json = '{"pack_review":{"status":"pass"},"ats":{"ready":true}}'
            expected = (
                stored.job_model_json, stored.evidence_matches_json, stored.application_decision_json,
                stored.selection_plan_json, stored.selection_confirmations_json, stored.release_state_json,
            )
            session.add(stored); session.commit()

        with patch("app.main.match_evidence_batch", side_effect=AssertionError("current matches must be reused")):
            second = self.client.post(f"/applications/{application['id']}/decision")
        self.assertEqual(second.status_code, 200, second.text)
        with Session(self.engine) as session:
            stored = session.get(JobApplication, application["id"])
            actual = (
                stored.job_model_json, stored.evidence_matches_json, stored.application_decision_json,
                stored.selection_plan_json, stored.selection_confirmations_json, stored.release_state_json,
            )
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
