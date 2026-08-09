import unittest

from app.reviewer_core import SHARED_REVIEWER_SCHEMA_VERSION, findings_block_release, normalise_finding


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

    def test_unknown_issue_type_is_rejected(self):
        self.assertIsNone(normalise_finding({"type": "personal_preference"}))


if __name__ == "__main__":
    unittest.main()
