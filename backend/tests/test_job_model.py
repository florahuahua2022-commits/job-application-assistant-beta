import unittest

from app.job_model import build_job_model, parse_word_limits, validate_job_model


class SharedJobModelTests(unittest.TestCase):
    def test_parses_explicit_criteria_categories_and_per_criterion_limit(self):
        criteria = """Selection Criteria
1. Demonstrated experience coordinating projects and stakeholder reporting.
2. Desirable: Knowledge of government policy and governance frameworks.
Maximum 350 words per criterion.
"""

        model = build_job_model("Government Project Officer role.", criteria, "Project Officer", "Example Agency")

        self.assertEqual(model["requirement_mode"], "explicit_selection_criteria")
        self.assertEqual(model["limit_scope"], "per_criteria")
        self.assertEqual(model["per_criteria_word_limit"], 350)
        self.assertEqual(len(model["criteria"]), 2)
        self.assertEqual(model["criteria"][0]["criteria_type"], "essential")
        self.assertIn("experience", model["criteria"][0]["criterion_categories"])
        self.assertEqual(model["criteria"][1]["criteria_type"], "desirable")
        self.assertIn("knowledge", model["criteria"][1]["criterion_categories"])
        self.assertEqual(validate_job_model(model), [])

    def test_infers_requirements_when_no_full_criteria_are_supplied(self):
        jd = """Coordinate project schedules and prepare monthly reports.
Build effective stakeholder relationships and communicate written advice.
Applicants must demonstrate knowledge of government policy.
"""

        model = build_job_model(jd, "Focus on stakeholder engagement")

        self.assertEqual(model["requirement_mode"], "inferred_requirements")
        self.assertEqual(model["brief_guidance"], "Focus on stakeholder engagement")
        self.assertTrue(model["criteria"])
        self.assertTrue(all(item["criteria_type"] == "inferred" for item in model["criteria"]))
        self.assertTrue(all(item["source"] == "job_description" for item in model["criteria"]))

    def test_recognises_total_word_limit_without_inventing_per_item_limit(self):
        limits = parse_word_limits("Your complete response must not exceed 1,000 words in total.")

        self.assertEqual(limits["limit_scope"], "total")
        self.assertEqual(limits["total_word_limit"], 1000)
        self.assertIsNone(limits["per_criteria_word_limit"])

    def test_keeps_unknown_word_limit_unspecified(self):
        limits = parse_word_limits("Address the requirements in a concise response.")

        self.assertEqual(limits["limit_scope"], "unspecified")
        self.assertIsNone(limits["total_word_limit"])
        self.assertEqual(limits["limit_instruction"], "")

    def test_filters_job_administration_lines_from_explicit_criteria(self):
        criteria = """Selection Criteria
1. Proven experience in event coordination and stakeholder engagement.
2. Strong financial administration skills and accurate reporting.
Note: Employment checks will include misconduct screening.
Position description: PD [Project Officer] [524054].pdf
To learn more about this opportunity, please contact Fiona at fiona@example.edu.au
For enquiries on the recruitment process, contact Petrina at recruitment@example.edu.au
"""

        model = build_job_model("Project Officer role", criteria)

        self.assertEqual(len(model["criteria"]), 2)
        self.assertTrue(all("contact" not in item["criteria_text"].lower() for item in model["criteria"]))


if __name__ == "__main__":
    unittest.main()
