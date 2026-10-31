"""Offline review follow-up cases; never contains private audit JSON."""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import main
from app.application_requirements import material_requirements_unknown
from app.auth import get_current_user
from app.database import get_session
from app.models import GeneratedDocument, JobApplication
from app.reviewer_core import normalise_saved_review


class FollowupPolicyTests(unittest.TestCase):
    def test_complete_but_unknown_material_requirement_still_blocks(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            application = JobApplication(company="Example", position_title="Officer", job_description="Synthetic duties")
            session.add(application); session.commit()
            for kind in ("resume", "selection_criteria"):
                with self.subTest(kind=kind):
                    requirements = {"review_status": "needs_confirmation", "completeness": "complete",
                                    "documents": {kind: {"requirement": "unknown", "format": "standalone"}}}
                    self.assertTrue(material_requirements_unknown(requirements))
                    with patch.object(main, "load_application_requirements", return_value=requirements):
                        result = main.quality_check(application.id, session, None)
                    reminder = next(x for x in result.issues if x.code == "application_requirements_confirmation_required")
                    unknown = next(x for x in result.issues if x.code == "application_requirements_unknown")
                    self.assertEqual(reminder.severity, "advisory")
                    self.assertFalse(reminder.blocks_release)
                    self.assertEqual(unknown.severity, "error")
                    self.assertTrue(unknown.blocks_release)
                    self.assertFalse(result.ready)
        engine.dispose()

    def test_pass_does_not_overwrite_existing_generation_or_quality_states(self):
        for generation in ("completed_low_confidence", "needs_review", "needs_ckb_update", "repaired", "future_state"):
            with self.subTest(generation=generation):
                raw = {"status": "pass", "factual_status": "fail", "quality_status": "fail",
                       "generation_status": generation, "telemetry": {"repair_rounds": 1},
                       "selected_round": 1, "versions": [{"round": 0}, {"round": 1}], "results": []}
                result = normalise_saved_review(raw)
                self.assertEqual(result["status"], "pass")
                self.assertEqual(result["generation_status"], generation)
                self.assertEqual(result["factual_status"], "fail")
                self.assertEqual(result["quality_status"], "fail")
                self.assertEqual(result["telemetry"], {"repair_rounds": 1})
                self.assertEqual(result["versions"], raw["versions"])
                self.assertEqual(result["selected_round"], 1)

    def test_empty_or_clean_generation_state_can_be_filled(self):
        for generation in (None, "", "clean"):
            with self.subTest(generation=generation):
                result = normalise_saved_review({"status": "pass", "factual_status": "", "quality_status": None,
                                                 "generation_status": generation, "results": []})
                self.assertEqual(result["generation_status"], "clean")
                self.assertEqual(result["factual_status"], "")
                self.assertIsNone(result["quality_status"])

    def test_fail_becoming_pass_resets_both_check_statuses(self):
        raw = {"status": "fail", "factual_status": "fail", "quality_status": "fail",
               "generation_status": "completed_low_confidence", "results": [{"status": "fail", "issues": [
                   {"type": "generation_under_utilized", "description": "Dayforce is repeated in Technical Skills and Work Experience."}]}]}
        result = normalise_saved_review(raw)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["factual_status"], "pass")
        self.assertEqual(result["quality_status"], "pass")
        self.assertEqual(result["generation_status"], "completed_low_confidence")
        self.assertEqual(raw["factual_status"], "fail")

    def test_original_pass_preserves_quality_failure_and_absent_factual_status(self):
        result = normalise_saved_review({"status": "pass", "quality_status": "fail", "results": []})
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["quality_status"], "fail")
        self.assertNotIn("factual_status", result)

    def test_still_failing_preserves_both_check_statuses(self):
        raw = {"status": "fail", "factual_status": "fail", "quality_status": "pass",
               "generation_status": "needs_review", "results": [{"status": "fail", "issues": [
                   {"type": "unsupported_claim", "description": "The CV claims a fabricated employer."}]}]}
        result = normalise_saved_review(raw)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["factual_status"], "fail")
        self.assertEqual(result["quality_status"], "pass")
        self.assertEqual(result["generation_status"], "needs_review")
        self.assertTrue(result["results"][0]["issues"][0]["blocks_release"])

    def test_document_list_sql_has_no_updates_even_after_flush(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        raw_review = json.dumps({"status": "fail", "results": [{"status": "fail", "issues": [
            {"type": "generation_under_utilized", "description": "Dayforce is repeated in Technical Skills and Work Experience."}]}]})
        with Session(engine) as session:
            application = JobApplication(company="Example", position_title="Officer", job_description="Synthetic duties")
            session.add(application); session.flush()
            document = GeneratedDocument(application_id=application.id, document_type="tailored_resume",
                                         content="Synthetic CV", reviewer_json=raw_review)
            session.add(document); session.commit()
            application_id, document_id = application.id, document.id
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()))
        def sessions():
            with Session(engine) as session:
                yield session
                # Prove that a future flush in the same request cannot persist presentation.
                session.flush()
                self.assertEqual(session.get(GeneratedDocument, document_id).reviewer_json, raw_review)
                session.commit()
        event.listen(engine, "before_cursor_execute", capture)
        main.app.dependency_overrides[get_session] = sessions
        main.app.dependency_overrides[get_current_user] = lambda: None
        try:
            response = TestClient(main.app).get(f"/applications/{application_id}/documents")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.json()[0]["reviewer_json"])["status"], "pass")
            mutations = [s for s in statements if s.split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}]
            self.assertEqual(mutations, [])
            self.assertTrue(statements)
            print("LIST SQL LOG: " + "; ".join(s.split()[0].upper() for s in statements)
                  + "; INSERT=0 UPDATE=0 DELETE=0 (including flush and commit)")
        finally:
            main.app.dependency_overrides.clear()
            event.remove(engine, "before_cursor_execute", capture)
            engine.dispose()
