import unittest

from app.content_check import consolidate_quality_issues


class ContentCheckConsolidationTests(unittest.TestCase):
    def test_evaluative_claim_and_ai_cliche_are_one_root_cause(self):
        result = consolidate_quality_issues([
            {"severity": "error", "code": "unsupported_evaluative_claim", "message": "strong record", "document_type": "tailored_resume"},
            {"severity": "error", "code": "unsupported_evaluative_claim", "message": "proven capability", "document_type": "tailored_resume"},
            {"severity": "warning", "code": "generic_ai_wording", "message": "AI wording", "document_type": "tailored_resume"},
            {"severity": "major", "code": "reviewer_ai_tone", "message": "Cliche", "document_type": "tailored_resume"},
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "unsupported_evaluative_claim")
        self.assertEqual(result[0]["severity"], "error")

    def test_different_fact_risks_are_not_merged(self):
        result = consolidate_quality_issues([
            {"severity": "error", "code": "unsupported_motivation", "message": "Motivation", "document_type": "cover_letter"},
            {"severity": "error", "code": "responsibility_or_result_upgrade", "message": "Responsibility", "document_type": "cover_letter"},
        ])
        self.assertEqual(len(result), 2)

    def test_reviewer_severities_map_to_product_levels(self):
        result = consolidate_quality_issues([
            {"severity": "critical", "code": "one", "message": "One"},
            {"severity": "advisory", "code": "two", "message": "Two"},
            {"severity": "unknown", "code": "three", "message": "Three"},
        ])
        self.assertEqual({item["code"]: item["severity"] for item in result}, {
            "one": "error", "two": "warning", "three": "information",
        })


if __name__ == "__main__":
    unittest.main()
