"""Offline small-fix regressions using invented data only."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app import main
from app.delivery_checks import delivery_issues
from app.models import GeneratedDocument, JobApplication
from app.reviewer_core import normalise_document_review, normalise_saved_review


class ReviewSmallFixTests(unittest.TestCase):
    def test_empty_confirmed_email_is_missing_not_a_conflict_or_exception(self):
        for email in ("", None):
            with self.subTest(email=email):
                profile = SimpleNamespace(first_name="Synthetic", last_name="Applicant", email=email,
                                          phone="0400000000", work_rights="not_specified")
                text = "Synthetic Applicant\n0400000000 | synthetic@example.invalid\n\n## Work Experience\nSorted paper tokens."
                codes = {i["code"] for i in delivery_issues(text, "tailored_resume", profile)}
                self.assertIn("canonical_email_missing", codes)
                self.assertNotIn("canonical_email_conflict", codes)

    def test_quality_check_blocks_each_saved_review_exception_at_either_call(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                app = JobApplication(company="Imaginary Token Shed", position_title="Token Clerk", job_description="Synthetic duties.")
                session.add(app); session.flush()
                document = GeneratedDocument(application_id=app.id, document_type="cover_letter", content="Synthetic letter.",
                                             reviewer_json='{"status":"pass","results":[]}')
                session.add(document); session.commit()
                for error_type in (ValueError, TypeError, AttributeError):
                    for second_only in (False, True):
                        with self.subTest(error_type=error_type, second_only=second_only):
                            error = error_type("Synthetic unreadable review")
                            results = [{"status": "pass", "results": []}, error] if second_only else [error, error]
                            with patch.object(main, "required_generated_documents", return_value=["cover_letter"]), \
                                 patch.object(main, "current_required_documents", return_value={"cover_letter": document}), \
                                 patch.object(main, "saved_document_review", side_effect=results) as read:
                                result = main.quality_check(app.id, session, None)
                            self.assertEqual(read.call_count, 2)
                            self.assertFalse(result.ready)
                            self.assertTrue(any(i.code == "document_review_required" and i.blocks_release for i in result.issues))
        finally:
            engine.dispose()

    def test_blank_serious_finding_cannot_become_a_passing_decision(self):
        for status in (None, "fail", "pass"):
            for description in ("", "  "):
                for kind in ("fabricated_figure", "fabricated_entity", "unsupported_claim"):
                    with self.subTest(status=status, description=description, kind=kind):
                        raw = {"issues": [{"type": kind, "description": description}]}
                        if status is not None:
                            raw["status"] = status
                        direct = normalise_document_review(raw, "tailored_resume")
                        self.assertEqual(direct["status"], "pending")
                        saved = {"results": [{"issues": raw["issues"]}]}
                        if status is not None:
                            saved["status"] = status
                        self.assertEqual(normalise_saved_review(saved)["status"], "pending")

    def test_valid_advice_cannot_mask_a_blank_serious_finding(self):
        issues = [{"type": "ai_tone", "description": "The fictional token sentence is repetitive."},
                  {"type": "fabricated_figure", "description": ""}]
        self.assertEqual(normalise_document_review({"status": "fail", "issues": issues}, "tailored_resume")["status"], "pending")
        self.assertEqual(normalise_saved_review({"status": "fail", "results": [{"issues": issues}]})["status"], "pending")
