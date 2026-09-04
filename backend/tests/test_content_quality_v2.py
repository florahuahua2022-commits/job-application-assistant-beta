import json
import unittest
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.ai import _repair_document
from app.ats_verification import verify_document_export
from app.exporter import create_docx, create_pdf
from app.ingest import extract_resume_experiences
from app.auth import get_current_user
from app.ckb import build_career_knowledge_base
from app.database import get_session
from app.evidence_matcher import normalise_match_result
from app.main import app
from app.models import GeneratedDocument, JobApplication
from app.resume_plan import build_resume_curation_plan


class ContentQualityTests(unittest.TestCase):
    def test_undated_explicit_role_preserves_actions(self):
        text = "Work Experience\nProject Officer\nExample Agency\nCoordinated supplier visits. Prepared project reports."
        records = extract_resume_experiences(text)
        self.assertEqual(len(records), 1)
        self.assertIn("Prepared project reports.", records[0]["source_text"])

    def test_both_exports_preserve_repeated_facts_and_detect_loss(self):
        source = "ALEX MORGAN\nPerth\n## Work Experience\nCoordinated visits for 30 delegates.\nPrepared reports.\nPrepared reports."
        for format, render in (("docx", create_docx), ("pdf", create_pdf)):
            self.assertTrue(verify_document_export(source, render(source, "Tailored Resume"), format)["ready"])
            self.assertFalse(verify_document_export(source, render(source.rsplit("\n", 1)[0], "Tailored Resume"), format)["ready"])

    def test_all_relevant_facts_survive_match_and_plan(self):
        ckb = [{"evidence_id": f"E{i}", "evidence_type": "experience", "source_section": "Work > Agency > Officer",
                "source_text": f"Coordinated distinct project {i}", "action": f"Coordinated distinct project {i}"} for i in range(15)]
        model = {"criteria": [{"criteria_id": "C", "criteria_type": "essential"}]}
        matches = normalise_match_result({"matches": [{"criteria_id": "C", "matched_evidence": [item["evidence_id"] for item in ckb], "match_type": "direct", "coverage": "strong"}]}, model, ckb)
        plan = build_resume_curation_plan(model, matches, ckb)
        self.assertEqual(len(plan["selected_evidence"]), 15)
        self.assertIsNone(plan["roles"][0]["max_bullets"])

    def test_number_in_duty_is_not_an_employment_date(self):
        source = "Officer\nAgency\nPrepared 2024 budget reports.\nCoordinated visits for 30 delegates."
        ckb = build_career_knowledge_base(source, json.dumps([{
            "role_title": "Officer", "organization": "Agency", "source_text": source,
            "responsibility": "Prepared 2024 budget reports.\nCoordinated visits for 30 delegates.",
        }]))
        self.assertEqual(len(ckb), 2)
        self.assertIn("2024 budget", ckb[0]["action"])
        self.assertTrue(all(item["source_paragraph"] == source for item in ckb))

    def test_repair_does_not_choose_a_worse_version(self):
        reviews = [
            {"status": "fail", "results": [{"issues": [{"type": "requirement_omission", "severity": "major"}]}]},
            {"status": "fail", "results": [{"issues": [{"type": "fabricated_figure", "severity": "critical"}]}]},
        ]
        with patch("app.ai.review_tailored_resume"):
            chosen, result = _repair_document("Original case", lambda _: reviews.pop(0), lambda *_: "Invented case", 1)
        self.assertEqual(chosen, "Original case")
        self.assertEqual(len(result["versions"]), 2)
        self.assertEqual(result["status"], "fail")


class MaterialVersionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_current_user] = lambda: None
        self.client = TestClient(app)
        self.client.post("/resumes", json={"source_text": "Original resume", "experiences_json": "[]"})
        self.a = self.client.post("/applications", json={"company": "", "position_title": "", "job_description": "Office support"}).json()
        self.b = self.client.post("/applications", json={"company": "Other", "position_title": "Officer", "job_description": "Office support"}).json()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def test_update_is_local_and_rejects_stale_write(self):
        payload = {"source_text": "Confirmed new source", "expected_snapshot": self.a["resume_snapshot_json"]}
        response = self.client.put(f"/applications/{self.a['id']}/resume", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(response.json()["resume_snapshot_json"])["source_text"], "Confirmed new source")
        self.assertEqual(self.client.put(f"/applications/{self.a['id']}/resume", json=payload).status_code, 409)
        with Session(self.engine) as session:
            self.assertEqual(session.get(JobApplication, self.b["id"]).resume_snapshot_json, self.b["resume_snapshot_json"])

    def test_edit_keeps_original_and_requires_new_review(self):
        with Session(self.engine) as session:
            document = GeneratedDocument(application_id=self.a["id"], document_type="cover_letter", content="Original", reviewer_json='{"status":"pass"}')
            session.add(document); session.commit(); session.refresh(document)
            original_id = document.id
        response = self.client.patch(f"/documents/{original_id}", json={"content": "Edited"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.json()["id"], original_id)
        self.assertEqual(json.loads(response.json()["reviewer_json"])["status"], "pending")
        with Session(self.engine) as session:
            self.assertEqual(session.get(GeneratedDocument, original_id).content, "Original")
        history = self.client.get(f"/applications/{self.a['id']}/document-history")
        self.assertEqual(len(history.json()), 2)

    def test_duplicate_request_returns_same_document(self):
        def generated(payload, session, user_id):
            document = GeneratedDocument(application_id=payload.application_id, document_type=payload.document_type, content="Saved draft")
            session.add(document); session.commit(); session.refresh(document)
            return document
        payload = {"application_id": self.a["id"], "document_type": "cover_letter", "pack_id": str(uuid4())}
        with patch("app.main.generate_document", side_effect=generated) as generate:
            first = self.client.post("/generate", json=payload)
            second = self.client.post("/generate", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(generate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
