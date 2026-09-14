import unittest

from app.reviewer_core import (
    REVIEW_POLICY_VERSION,
    SHARED_REVIEWER_SCHEMA_VERSION,
    findings_block_release,
    normalise_document_review,
    normalise_finding,
    reconcile_review_grounding,
)


class SharedReviewerCoreTests(unittest.TestCase):
    def test_material_grounding_issues_have_deterministic_severity_and_block(self):
        finding = normalise_finding({
            "type": "fabricated_figure",
            "description": "The claimed 40% result is not in source_text.",
            "evidence": "No figure appears in EV001.",
            "location": "Second paragraph",
        })

        self.assertEqual(SHARED_REVIEWER_SCHEMA_VERSION, "1.0")
        self.assertEqual(finding["severity"], "critical")
        self.assertTrue(finding["blocks_release"])
        self.assertTrue(findings_block_release([finding]))

    def test_style_only_feedback_is_advisory_and_does_not_block(self):
        finding = normalise_finding({"type": "style_only", "description": "A shorter opening may read better."})

        self.assertEqual(finding["severity"], "advisory")
        self.assertFalse(finding["blocks_release"])
        self.assertFalse(findings_block_release([finding]))

    def test_unknown_issue_type_fails_closed_as_major(self):
        finding = normalise_finding({"type": "personal_preference", "description": "Use a different style."})

        self.assertEqual(finding["type"], "unknown_reviewer_issue")
        self.assertEqual(finding["severity"], "major")
        self.assertTrue(finding["blocks_release"])
        self.assertIn("personal_preference", finding["description"])

    def test_document_review_does_not_fail_for_style_only_feedback(self):
        result = normalise_document_review({"status": "fail", "issues": [
            {"type": "style_only", "description": "A shorter opening may read better."},
        ]}, "cover_letter")

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["results"][0]["criteria_id"], "cover_letter")

    def test_critical_issue_overrides_llm_pass_status(self):
        result = normalise_document_review({"status": "pass", "issues": [{
            "type": "fabricated_entity",
            "description": "The named program is absent from its permitted source.",
        }]}, "cover_letter")

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["results"][0]["status"], "fail")
        self.assertTrue(result["results"][0]["issues"][0]["blocks_release"])

    def test_exact_document_quote_must_exist_in_current_content(self):
        review = normalise_document_review({"issues": [{
            "type": "fabricated_figure",
            "description": "The Professional Summary states 'over 12 years', which exceeds the allowed claim.",
            "location": "over 12 years",
            "location_kind": "exact_quote",
        }]}, "tailored_resume")

        reconcile_review_grounding(review, "Professional Summary\n12+ years of total employment experience")

        issue = review["results"][0]["issues"][0]
        self.assertEqual(review["status"], "pass")
        self.assertEqual(issue["grounding_status"], "unverified")
        self.assertEqual(issue["severity"], "advisory")
        self.assertFalse(issue["blocks_release"])
        self.assertEqual(review["review_policy_version"], REVIEW_POLICY_VERSION)

    def test_quote_matching_normalises_case_spacing_and_smart_punctuation(self):
        review = normalise_document_review({"issues": [{
            "type": "unsupported_claim",
            "description": "The CV states a claim.",
            "location": "Managed suppliers - and clients",
            "location_kind": "exact_quote",
        }]}, "tailored_resume")

        reconcile_review_grounding(review, "MANAGED  suppliers —\nand clients.")

        self.assertEqual(review["status"], "fail")
        self.assertEqual(review["results"][0]["issues"][0]["grounding_status"], "verified")

    def test_source_quote_is_not_checked_against_document_content(self):
        review = normalise_document_review({"issues": [{
            "type": "unsupported_claim",
            "description": "CKB source_text records 'Prepared monthly reports', but the CV overstates the responsibility.",
            "location": "Professional Summary",
            "location_kind": "section",
        }]}, "tailored_resume")

        reconcile_review_grounding(review, "## Professional Summary\nLed the reporting function.")

        self.assertEqual(review["status"], "fail")
        self.assertTrue(review["results"][0]["issues"][0]["blocks_release"])

    def test_document_and_source_quotes_are_assigned_to_their_own_contexts(self):
        review = normalise_document_review({"issues": [{
            "type": "unsupported_inference",
            "description": "The CV states 'Led monthly reporting', while CKB source_text records 'Prepared monthly reports'.",
            "location": "Led monthly reporting",
            "location_kind": "exact_quote",
        }]}, "tailored_resume")

        reconcile_review_grounding(review, "## Professional Summary\nLed monthly reporting across the team.")

        issue = review["results"][0]["issues"][0]
        self.assertEqual(review["status"], "fail")
        self.assertEqual(issue["grounding_status"], "verified")
        self.assertTrue(issue["blocks_release"])

    def test_paraphrased_finding_without_document_quote_is_preserved(self):
        review = normalise_document_review({"issues": [{
            "type": "unsupported_claim",
            "description": "The Professional Summary overstates the applicant's reporting responsibility.",
            "location": "Professional Summary",
        }]}, "tailored_resume")

        reconcile_review_grounding(review, "## Professional Summary\nLed the reporting function.")

        self.assertEqual(review["status"], "fail")
        self.assertTrue(review["results"][0]["issues"][0]["blocks_release"])

    def test_legacy_description_quote_claimed_as_document_text_is_grounded(self):
        review = normalise_document_review({"issues": [{
            "type": "unsupported_claim",
            "description": "The letter claims 'I led the project', but the evidence supports assistance only.",
            "location": "Opening paragraph",
        }]}, "cover_letter")

        reconcile_review_grounding(review, "I supported the project team.")

        self.assertEqual(review["status"], "pass")
        self.assertEqual(review["results"][0]["issues"][0]["grounding_status"], "unverified")


if __name__ == "__main__":
    unittest.main()
