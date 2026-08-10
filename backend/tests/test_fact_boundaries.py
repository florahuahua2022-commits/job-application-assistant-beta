import unittest

from app.fact_boundaries import find_fact_boundary_issues


class FactBoundaryTests(unittest.TestCase):
    def test_blocks_reported_adaptability_and_delivery_upgrade(self):
        issues = find_fact_boundary_issues(
            "This experience required adapting to changing requirements while keeping deliverables on track.",
            "Supported Pratt & Whitney documentation activities.",
        )

        self.assertEqual(
            {issue["code"] for issue in issues if issue["severity"] == "error"},
            {"responsibility_or_result_upgrade"},
        )
        self.assertEqual(len([issue for issue in issues if issue["severity"] == "error"]), 2)

    def test_blocks_reported_responsibility_and_quality_upgrade(self):
        issues = find_fact_boundary_issues(
            "I take responsibility for delivering accurate, timely administrative outputs.",
            "Liaised between China Communications Construction Company and external stakeholders.",
        )

        self.assertTrue(any(issue["code"] == "responsibility_or_result_upgrade" for issue in issues))

    def test_blocks_unconfirmed_motivation_but_allows_neutral_application_intent(self):
        unsupported = find_fact_boundary_issues(
            "What draws me to this position is the opportunity to support public projects.",
            "Project support experience.",
            motivation_confirmed=False,
        )
        neutral = find_fact_boundary_issues(
            "I am applying for the Project Officer position at DTMI.",
            "Project support experience.",
            motivation_confirmed=False,
        )

        self.assertIn("unsupported_motivation", {issue["code"] for issue in unsupported})
        self.assertEqual(neutral, [])

    def test_confirmed_motivation_is_allowed(self):
        issues = find_fact_boundary_issues(
            "What draws me to this position is the opportunity to support public projects.",
            "Project support experience.",
            motivation_confirmed=True,
        )

        self.assertFalse(any(issue["code"] == "unsupported_motivation" for issue in issues))

    def test_blocks_unsupported_evaluative_cv_language(self):
        issues = find_fact_boundary_issues(
            "Proven capability in reporting with a strong record of managing priorities.",
            "Assisted with monthly reports and maintained project records.",
        )

        self.assertEqual(len([issue for issue in issues if issue["code"] == "unsupported_evaluative_claim"]), 2)

    def test_explicit_source_wording_is_not_blocked(self):
        content = "Kept deliverables on track and produced accurate and timely administrative outputs."
        issues = find_fact_boundary_issues(content, content)

        self.assertEqual(issues, [])

    def test_broad_alignment_is_warning_not_error(self):
        issues = find_fact_boundary_issues(
            "The role aligns closely with the work I have delivered throughout my career.",
            "Project coordination and administrative support.",
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertEqual(issues[0]["code"], "broad_alignment_claim")


if __name__ == "__main__":
    unittest.main()
