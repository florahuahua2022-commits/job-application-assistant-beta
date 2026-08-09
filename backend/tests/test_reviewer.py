import unittest

from app.reviewer import normalise_review_result, validate_review_result


class BatchReviewerTests(unittest.TestCase):
    def test_normalises_supported_findings_and_marks_batch_failed(self):
        raw = {"results": [
            {"criteria_id": "C1", "status": "fail", "issues": [
                {"type": "fabricated_figure", "description": "The percentage is absent from source evidence."},
                {"type": "style_preference", "description": "Use a different opening."},
            ]},
            {"criteria_id": "C2", "status": "pass", "issues": []},
        ]}

        result = normalise_review_result(raw, ["C1", "C2"])

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["results"][0]["issues"], [{
            "type": "fabricated_figure", "description": "The percentage is absent from source evidence.",
        }])
        self.assertEqual(validate_review_result(result, ["C1", "C2"]), [])

    def test_missing_reviewer_decision_fails_closed(self):
        result = normalise_review_result({"results": [{"criteria_id": "C1", "status": "pass", "issues": []}]}, ["C1", "C2"])

        missing = next(item for item in result["results"] if item["criteria_id"] == "C2")
        self.assertEqual(missing["status"], "fail")
        self.assertTrue(missing["issues"])


if __name__ == "__main__":
    unittest.main()
