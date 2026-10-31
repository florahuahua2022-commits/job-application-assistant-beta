"""Offline policy regressions. Reviewer findings are fixtures, not live AI verdicts."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine
from app import main, reviewer_core, quality
from app.delivery_checks import delivery_issues, profile_missing_fields, apply_delivery_review
from app.models import JobApplication, GeneratedDocument, Resume
from app.reviewer_core import normalise_document_review, normalise_finding, normalise_saved_review, reconcile_review_grounding


FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "fixtures/content_quality/synthetic-review-samples.json").read_text())
TOOL_CKB = [{"evidence_id": "SYNTHETIC_TOOL", "source_text": "The imaginary observatory uses Dayforce."}]
TOOL_PLAN = {"selected_evidence": [{"evidence_id": "SYNTHETIC_TOOL"}]}


class FindingPolicyTests(unittest.TestCase):
    def test_N14_dayforce_is_retained_as_advisory(self):
        finding = normalise_finding(FIXTURE["dayforce_finding"], ckb=TOOL_CKB, plan=TOOL_PLAN)
        self.assertIsNotNone(finding)
        self.assertEqual(finding["severity"], "advisory")
        self.assertFalse(finding["blocks_release"])
        self.assertEqual(finding["category"], "consistency")

    def test_N14_reverse_omission_blocks_but_pure_repetition_is_advisory(self):
        for description, severity, blocks in [
            ("Dayforce is in Technical Skills but not in Work Experience.", "major", True),
            ("Dayforce is repeated in Technical Skills and Work Experience.", "advisory", False),
        ]:
            with self.subTest(description=description):
                finding = normalise_finding({"type": "declared_evidence_unused", "description": description})
                self.assertEqual(finding["severity"], severity)
                self.assertEqual(finding["blocks_release"], blocks)

    def test_N18_exact_duplicates_only_are_merged(self):
        issue = {"type": "unsupported_claim", "description": "A skill label has no source.", "location": "Key Skills: leadership"}
        review = normalise_document_review({"issues": [issue, dict(issue)]}, "tailored_resume")
        self.assertEqual(len(review["results"][0]["issues"]), 1)

    def test_R3_different_figures_locations_and_documents_survive(self):
        issues = [{"type": "fabricated_figure", "description": "The 30% result is unsupported.", "location": "first"},
                  {"type": "fabricated_figure", "description": "The 40% result is unsupported.", "location": "first"},
                  {"type": "fabricated_figure", "description": "The 30% result is unsupported.", "location": "second"}]
        review = normalise_document_review({"issues": issues}, "tailored_resume")
        self.assertEqual(len(review["results"][0]["issues"]), 3)
        self.assertEqual(review["status"], "fail")

    def test_N19_empty_and_explicit_nonissues_are_hidden(self):
        for description in ["", "   ", "No issue with content.", "No issues found.",
                            "The closing matches the confirmed profile. No issue with content."]:
            with self.subTest(description=description):
                self.assertIsNone(normalise_finding({"type": "unsupported_claim", "description": description}))

    def test_R4_no_issue_or_no_action_cannot_hide_a_real_problem(self):
        for description in ["No issue except the phone number is wrong.",
                            "The applicant claims an unsupported 30% result. No issue with content.",
                            "The applicant claims a CPA qualification that the source does not establish. No issue with content.",
                            "The phone is wrong but the email is supported. No issue with content.",
                            "The source omits CPA but other claims are supported. No issue with content."]:
            with self.subTest(description=description):
                finding = normalise_finding({"type": "unsupported_claim", "description": description,
                                             "recommended_action": "No action required."})
                self.assertIsNotNone(finding)
                self.assertTrue(finding["blocks_release"])

    def test_N19_bare_fail_stays_pending_not_an_empty_content_error(self):
        for raw_issues in [[], [{"type": "unsupported_claim", "description": ""}]]:
            review = normalise_document_review({"status": "fail", "issues": raw_issues}, "tailored_resume")
            self.assertEqual(review["status"], "pending")
            self.assertEqual(review["results"][0]["issues"], [])

    def test_N20_writing_and_unknown_warning_levels(self):
        finding = normalise_finding({"type": "ai_tone", "description": "The opening reads like a clipped duty."})
        self.assertEqual(finding["severity"], "advisory")
        unknown = normalise_finding({"type": "new_unrecognised_check", "description": "Check could not be interpreted."})
        self.assertEqual(unknown["display_level"], "warning")
        self.assertTrue(unknown["blocks_release"])

    def test_R2_advice_does_not_swallow_factual_error(self):
        review = normalise_document_review({"issues": [FIXTURE["dayforce_finding"],
            {"type": "unsupported_claim", "description": "The applicant claims proficiency in Dayforce without source evidence."}]}, "tailored_resume", ckb=TOOL_CKB, plan=TOOL_PLAN)
        self.assertEqual(review["status"], "fail")
        self.assertEqual(sum(x["blocks_release"] for x in review["results"][0]["issues"]), 1)

    def test_N14_obligatory_omission_is_not_a_skills_advisory(self):
        finding = normalise_finding({"type": "requirement_omission", "obligation": "must_answer",
            "description": "Dayforce in Technical Skills is not an answer to the required declaration."})
        self.assertTrue(finding["blocks_release"])

    def test_F1_fabricated_figure_blocks(self):
        self.assertTrue(normalise_finding({"type": "fabricated_figure", "description": "Invented 30% improvement."})["blocks_release"])

    def test_F2_responsibility_upgrade_blocks(self):
        self.assertTrue(normalise_finding({"type": "unsupported_inference", "description": "The source says assisted; the CV says led."})["blocks_release"])

    def test_F3_invented_industry_blocks(self):
        self.assertTrue(normalise_finding({"type": "unsupported_claim", "description": "Mining experience is not in the infrastructure source."})["blocks_release"])

    def test_F4_invented_system_blocks(self):
        self.assertTrue(normalise_finding({"type": "fabricated_entity", "description": "JDE proficiency is absent from source."})["blocks_release"])

    def test_F5_invented_qualification_blocks(self):
        self.assertTrue(normalise_finding({"type": "unsupported_claim", "description": "The claimed licence is absent from source."})["blocks_release"])

    def test_F6_false_employer_or_dates_blocks(self):
        self.assertTrue(normalise_finding({"type": "evidence_mismatch", "description": "The employer and dates differ from the source."})["blocks_release"])

    def test_F7_two_documents_agreeing_on_fabrication_still_block(self):
        for kind in ["tailored_resume", "cover_letter"]:
            self.assertEqual(normalise_document_review({"issues": [{"type": "fabricated_entity", "description": "Both documents claim unsupported JDE."}]}, kind)["status"], "fail")

    def test_F8_cross_document_contradiction_blocks(self):
        self.assertTrue(normalise_finding({"type": "contradiction", "description": "The two documents give different employment dates."})["blocks_release"])

    def test_F9_negated_system_claim_with_passing_review_is_not_failed(self):
        review = normalise_document_review({"issues": [{"type": "unsupported_claim",
            "description": "The letter claims direct JDE experience.", "location_kind": "exact_quote", "location": "I have JDE experience"}]}, "cover_letter")
        reconcile_review_grounding(review, "I have no direct experience using JDE.")
        self.assertEqual(normalise_saved_review(review)["status"], "pass")

    def test_F10_supported_transferable_evidence_with_passing_review_is_not_failed(self):
        self.assertEqual(normalise_document_review({"status": "pass", "issues": []}, "tailored_resume")["status"], "pass")

    def test_undeclared_motivation_is_not_style_feedback(self):
        finding = normalise_finding({"type": "unsupported_motivation", "description": "Values alignment is not declared in Profile."})
        self.assertTrue(finding["blocks_release"])

    def test_N18_saved_rows_normalise_without_mutating_original(self):
        finding = dict(FIXTURE["dayforce_finding"])
        original = {"status": "fail", "results": [{"status": "fail", "issues": [finding, dict(finding)]}]}
        result = normalise_saved_review(original, ckb=TOOL_CKB, plan=TOOL_PLAN)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["results"][0]["issues"]), 1)
        self.assertEqual(original["status"], "fail")
        self.assertEqual(len(original["results"][0]["issues"]), 2)

    def test_N19_missing_review_never_becomes_a_pass(self):
        self.assertEqual(normalise_saved_review({})["status"], "pending")

    def test_N20_delivery_hard_checks_cannot_be_relabelled_advisory(self):
        review = {"status": "pass", "results": [{"criteria_id": "delivery_hard_checks", "issues": [
            {"type": "canonical_email_conflict", "description": "The header email differs from Profile.", "severity": "advisory", "blocks_release": False}]}]}
        self.assertEqual(normalise_saved_review(review)["status"], "fail")

    def test_N21_grounded_unsupported_claim_still_blocks(self):
        review = normalise_document_review({"issues": [{"type": "unsupported_claim", "description": "Only assistance is evidenced.",
            "location_kind": "exact_quote", "location": "I led the project."}]}, "cover_letter")
        reconcile_review_grounding(review, "I led the project.")
        self.assertEqual(normalise_saved_review(review)["status"], "fail")

    def test_dayforce_factual_contradiction_or_wrong_employer_is_not_advisory(self):
        for description in ["Dayforce in Technical Skills claims unsupported proficiency.",
                            "Dayforce in Technical Skills is attributed to the wrong employer."]:
            finding = normalise_finding({"type": "generation_under_utilized", "description": description})
            self.assertTrue(finding["blocks_release"])


class ContactAndSpacingTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(first_name="Test", last_name="Applicant", phone="0400000000", email="test@example.invalid", work_rights="permanent_resident")
        self.good = "Test Applicant\n0400000000 | test@example.invalid\n\n## Work Experience\nSupported reporting."

    def test_C1_wrong_phone_blocks(self):
        self.assertTrue(delivery_issues(self.good.replace("0400000000", "0499999999"), "tailored_resume", self.profile))

    def test_C2_wrong_email_blocks(self):
        self.assertTrue(delivery_issues(self.good.replace("test@example.invalid", "wrong@example.invalid"), "cover_letter", self.profile))

    def test_C3_wrong_header_and_correct_signature_still_block(self):
        text = self.good.replace("0400000000", "0499999999") + "\n\nYours faithfully\nTest Applicant\n0400000000 | test@example.invalid"
        self.assertTrue(delivery_issues(text, "cover_letter", self.profile))

    def test_C4_conflicting_contacts_across_documents_block(self):
        self.assertTrue(delivery_issues(self.good.replace("test@example.invalid", "other@example.invalid"), "cover_letter", self.profile))

    def test_C5_equivalent_international_phone_is_accepted(self):
        self.assertEqual(delivery_issues(self.good.replace("0400000000", "+61 400 000 000"), "tailored_resume", self.profile), [])

    def test_C6_recruiter_contacts_in_body_are_not_applicant_contacts(self):
        self.assertEqual(delivery_issues(self.good + "\nContact recruiter: 0499999999 | recruiter@example.invalid", "tailored_resume", self.profile), [])

    def test_C7_missing_profile_is_not_guessed(self):
        self.assertTrue(profile_missing_fields(None))

    def test_R1_hard_check_overrides_passing_review(self):
        review = apply_delivery_review({"status": "pass"}, delivery_issues(self.good.replace("0400000000", "0499999999"), "tailored_resume", self.profile))
        self.assertEqual(review["status"], "fail")

    def test_identity_error_remains_blocking(self):
        self.assertTrue(delivery_issues(self.good + "\nI am an Australian citizen.", "cover_letter", self.profile))

    def test_N22_spacing_is_repaired_in_both_document_polishers(self):
        for polish in [lambda text: main.auto_polish_cover_letter(text, SimpleNamespace(availability_notice="immediate")), main.auto_polish_tailored_resume]:
            self.assertIn("ready. The lantern", polish(FIXTURE["letter_closing"]))

    def test_N22_preserves_abbreviations_numbers_urls_emails_and_initials(self):
        repair = getattr(quality, "repair_sentence_spacing", None)
        self.assertTrue(callable(repair), "sentence spacing repair is not implemented")
        text = "Dr.Smith uses e.g.Excel. U.S.A. and A.B. stay. 3.14 https://Example.COM/Test applicant@Example.COM"
        self.assertEqual(repair(text), text)
        self.assertEqual(repair("Prepared reports!Then filed them?Next step.Records updated."), "Prepared reports! Then filed them? Next step. Records updated.")


class QualityEndpointPolicyTests(unittest.TestCase):
    def test_N17_confirmation_only_is_advisory_but_real_gaps_still_error(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            application = JobApplication(company="Example", position_title="Officer", job_description="Support reports.")
            session.add(application); session.commit()
            requirements = {"review_status": "needs_confirmation", "completeness": "complete", "documents": {}}
            with patch.object(main, "load_application_requirements", return_value=requirements), patch.object(main, "material_requirements_unknown", return_value=False):
                result = main.quality_check(application.id, session, None)
                reminder = next(x for x in result.issues if x.code == "application_requirements_confirmation_required")
                self.assertEqual(reminder.severity, "advisory")
                self.assertFalse(reminder.blocks_release)
            requirements["completeness"] = "incomplete"
            with patch.object(main, "load_application_requirements", return_value=requirements), patch.object(main, "material_requirements_unknown", return_value=True):
                result = main.quality_check(application.id, session, None)
                self.assertTrue(any(x.blocks_release for x in result.issues if x.code == "employer_requirements_incomplete"))
        engine.dispose()

    def test_N18_endpoint_preserves_distinct_locations_and_exposes_advice(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            application = JobApplication(company="Example", position_title="Officer", job_description="Support reports.")
            session.add(application); session.flush()
            findings = [{"type": "generation_under_utilized", "description": "Dayforce is repeated in Technical Skills and Work Experience."},
                {"type": "unsupported_claim", "description": "A label has no source.", "location": "leadership"},
                {"type": "unsupported_claim", "description": "A label has no source.", "location": "strategy"}]
            document = GeneratedDocument(application_id=application.id, document_type="cover_letter", content="Officer at Example.",
                structured_content_json=json.dumps(TOOL_PLAN), trace_json=json.dumps({"inputs": {"ckb": TOOL_CKB}}),
                reviewer_json=json.dumps({"status": "fail", "results": [{"status": "fail", "issues": findings}]}))
            session.add(document); session.commit()
            with patch.object(main, "current_required_documents", return_value={"cover_letter": document}):
                result = main.quality_check(application.id, session, None)
            reviewer_findings = [x for x in result.issues if x.code.startswith("reviewer_")]
            self.assertEqual(len(reviewer_findings), 3)
            self.assertEqual(sum(x.blocks_release for x in reviewer_findings), 2)
            self.assertTrue(any(x.severity == "advisory" and "Dayforce" in x.message for x in reviewer_findings))
        engine.dispose()
