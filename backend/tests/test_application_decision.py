import unittest
from types import SimpleNamespace

from app.application_decision import build_application_decision, validate_application_decision


def criterion(criteria_id, text, criteria_type="essential"):
    return {"criteria_id": criteria_id, "criteria_text": text, "criteria_type": criteria_type}


class ApplicationDecisionTests(unittest.TestCase):
    def decide(self, criteria, matches, previous=None, application_requirements=None):
        return build_application_decision(
            {"criteria": criteria}, application_requirements or {"documents": {}}, {"matches": matches}, [],
            SimpleNamespace(work_rights="permanent_resident", availability_notice="not_specified"), previous,
        )

    def test_verified_and_adjacent_matches_remain_distinct(self):
        decision = self.decide(
            [criterion("C1", "Reporting experience"), criterion("C2", "Policy experience")],
            [
                {"criteria_id": "C1", "matched_evidence": ["EV1"], "match_type": "direct", "coverage": "strong"},
                {"criteria_id": "C2", "matched_evidence": ["EV2"], "match_type": "inferred", "coverage": "partial"},
            ],
        )
        self.assertEqual([item["evidence_classification"] for item in decision["requirements"]], ["verified_match", "adjacent_match"])
        self.assertEqual(decision["requirements"][1]["recommended_action"], "reframe")
        self.assertEqual(decision["requirements"][1]["disclosure_strategy"], "bridge")
        self.assertEqual(validate_application_decision(decision), [])

    def test_only_material_recoverable_hard_gate_creates_question(self):
        decision = self.decide([
            criterion("C1", "A current driver's licence is required"),
            criterion("C2", "Cloud experience is desirable", "desirable"),
            criterion("C3", "Commercial judgement", "inferred"),
        ], [])
        self.assertEqual([item["criteria_id"] for item in decision["questions"]], ["C1"])
        self.assertTrue(decision["questions"][0]["material"])
        self.assertIsNone(decision["requirements"][1]["evidence_classification"])
        self.assertEqual(decision["requirements"][1]["recommended_action"], "omit")
        self.assertEqual(decision["requirements"][1]["disclosure_strategy"], "none")
        self.assertEqual(decision["status"], "needs_confirmation")

    def test_explicit_answers_pass_or_fail_gate_without_becoming_evidence(self):
        initial = self.decide([criterion("C1", "Current registration is essential")], [])
        question = initial["questions"][0]
        question.update(answer=True, provenance="user_confirmed")
        passed = self.decide([criterion("C1", "Current registration is essential")], [], initial)
        self.assertEqual(passed["requirements"][0]["hard_gate_status"], "pass")
        self.assertEqual(passed["requirements"][0]["evidence_classification"], "unverified_possible")
        self.assertEqual(passed["requirements"][0]["matched_evidence"], [])
        self.assertEqual(passed["status"], "ready")

        question.update(answer=False)
        failed = self.decide([criterion("C1", "Current registration is essential")], [], initial)
        self.assertEqual(failed["requirements"][0]["evidence_classification"], "confirmed_gap")
        self.assertEqual(failed["requirements"][0]["hard_gate_status"], "fail")
        self.assertEqual(failed["application_recommendation"], "do_not_apply")
        self.assertEqual(failed["requirements"][0]["disclosure_strategy"], "none")

    def test_private_inferred_requirement_keeps_unknown_importance(self):
        decision = self.decide([criterion("C1", "Commercial judgement", "inferred")], [])
        self.assertEqual(decision["requirements"][0]["importance"], "unknown")
        self.assertEqual(decision["questions"], [])
        self.assertEqual(decision["status"], "ready")

    def test_essential_experience_can_be_explicitly_confirmed_as_a_non_gate_gap(self):
        initial = self.decide([criterion("C1", "Demonstrated procurement experience")], [])
        self.assertEqual(initial["status"], "needs_confirmation")
        initial["questions"][0].update(answer=False, provenance="user_confirmed")
        decision = self.decide([criterion("C1", "Demonstrated procurement experience")], [], initial)
        item = decision["requirements"][0]
        self.assertEqual(item["evidence_classification"], "confirmed_gap")
        self.assertEqual(item["hard_gate_status"], "not_applicable")
        self.assertEqual(item["disclosure_strategy"], "none")
        self.assertEqual(decision["application_recommendation"], "reconsider")

    def test_non_gate_yes_keeps_hard_gate_not_applicable(self):
        initial = self.decide([criterion("C1", "Demonstrated procurement experience")], [])
        initial["questions"][0].update(answer=True, provenance="user_confirmed")

        decision = self.decide([criterion("C1", "Demonstrated procurement experience")], [], initial)

        self.assertEqual(decision["requirements"][0]["hard_gate_status"], "not_applicable")
        self.assertEqual(decision["status"], "ready")

    def test_incomplete_employer_requirements_are_cautious_without_candidate_gaps(self):
        decision = self.decide([], [], application_requirements={
            "completeness": "incomplete",
            "warnings": ["The referenced JDF criteria 1, 2 and 3 could not be resolved."],
            "documents": {},
        })

        self.assertEqual(decision["status"], "needs_confirmation")
        self.assertEqual(decision["application_recommendation"], "reconsider")
        self.assertEqual(decision["requirements"], [])
        self.assertEqual(decision["questions"], [])
        self.assertEqual(decision["blocking_issues"][0]["code"], "employer_requirements_incomplete")


if __name__ == "__main__":
    unittest.main()
