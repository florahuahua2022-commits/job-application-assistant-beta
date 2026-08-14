import unittest

from app.reviewer_core import SHARED_REVIEWER_SCHEMA_VERSION, findings_block_release, normalise_document_review, normalise_finding


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


if __name__ == "__main__":
    unittest.main()
