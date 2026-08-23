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

    def test_organisation_context_preserves_full_name_when_header_is_abbreviated(self):
        model = build_job_model(
            "The Zoological Parks Authority at Perth Zoo is seeking a project coordinator.",
            None,
            "Project Coordinator",
            "ZOO",
        )

        self.assertIn("Zoological Parks Authority at Perth Zoo", model["organisation_context"])

    def test_selection_instructions_are_not_treated_as_criteria(self):
        criteria = """Applicants should address the following four (4) criteria.
These should be addressed in no more than two (2) pages in total.
1. Demonstrated project delivery experience.
2. Strong written communication skills.
3. Ability to coordinate contractors and consultants.
4. Effective planning and prioritisation skills.
"""

        model = build_job_model("Project role.", criteria, "Project Coordinator", "Perth Zoo")

        self.assertEqual(len(model["criteria"]), 4)
        self.assertTrue(all("Applicants should address" not in item["criteria_text"] for item in model["criteria"]))
        self.assertTrue(all("pages in total" not in item["criteria_text"] for item in model["criteria"]))

    def test_values_prose_and_arbitrary_words_do_not_become_competencies(self):
        jd = """About You
Strong planning and organisation skills.
Previous administration experience within construction or mining is preferred.
Our Values
Pride & Commitment – We own our work and get the job done.
Growth & Improvement – We push ourselves to evolve and excel.
Family & Loyalty – We look after our people and create a welcoming team culture.
Trust & Respect – We communicate openly and honour our commitments.
"""

        model = build_job_model(jd)
        competencies = [value for item in model["criteria"] for value in item["key_competencies"]]
        criteria = " ".join(item["criteria_text"] for item in model["criteria"]).lower()

        self.assertEqual(competencies, ["planning and organisation", "construction or mining experience"])
        self.assertNotIn("pride", criteria)
        self.assertNotIn("preferred", competencies)

    def test_unmapped_descriptive_sentence_has_no_fallback_competencies(self):
        model = build_job_model("Applicants must bring curiosity, energy and enthusiasm every day.")

        self.assertEqual(model["criteria"][0]["key_competencies"], [])

    def test_legitimate_technical_and_governance_requirements_remain_mapped(self):
        model = build_job_model("""Technical capability using relevant project systems is required.
Demonstrated knowledge of policy and governance requirements.
""")

        competencies = [value for item in model["criteria"] for value in item["key_competencies"]]
        self.assertIn("technical capability", competencies)
        self.assertIn("policy and governance", competencies)


if __name__ == "__main__":
    unittest.main()
