import unittest

from app.selection_logic import allocate_word_limits, build_selection_plan, criteria_requiring_confirmation, evidence_status, hard_validate_response


class SelectionApplicationLogicTests(unittest.TestCase):
    def test_only_transferable_and_weak_criteria_require_confirmation(self):
        plan = {"items": [
            {"criteria_id": "C1", "evidence_status": "strong"},
            {"criteria_id": "C2", "evidence_status": "transferable"},
            {"criteria_id": "C3", "evidence_status": "weak"},
        ]}

        self.assertEqual(criteria_requiring_confirmation(plan), {"C2", "C3"})

    def test_maps_match_and_coverage_to_frozen_user_status(self):
        self.assertEqual(evidence_status("direct", "strong"), "strong")
        self.assertEqual(evidence_status("direct", "partial"), "transferable")
        self.assertEqual(evidence_status("inferred", "strong"), "transferable")
        self.assertEqual(evidence_status("inferred", "weak"), "weak")
        self.assertEqual(evidence_status("insufficient", "strong"), "weak")

    def test_uses_exact_per_criterion_limit(self):
        model = {"limit_scope": "per_criteria", "per_criteria_word_limit": 300, "criteria": [
            {"criteria_id": "C1"}, {"criteria_id": "C2"},
        ]}

        self.assertEqual(allocate_word_limits(model), {"C1": 300, "C2": 300})

    def test_allocates_total_limit_with_weights_and_never_exceeds_total(self):
        model = {"limit_scope": "total", "total_word_limit": 1000, "criteria": [
            {"criteria_id": "C1", "criteria_type": "essential", "criterion_categories": ["experience", "behaviour"]},
            {"criteria_id": "C2", "criteria_type": "essential", "criterion_categories": ["technical"]},
            {"criteria_id": "C3", "criteria_type": "desirable", "criterion_categories": ["knowledge"]},
        ]}

        allocated = allocate_word_limits(model)

        self.assertEqual(sum(allocated.values()), 1000)
        self.assertGreater(allocated["C1"], allocated["C2"])
        self.assertGreater(allocated["C2"], allocated["C3"])

    def test_uses_configurable_default_when_limit_is_unspecified(self):
        model = {"limit_scope": "unspecified", "criteria": [{"criteria_id": "C1"}]}
        self.assertEqual(allocate_word_limits(model, default_target=375), {"C1": 375})

    def test_plan_reports_primary_evidence_reuse_and_source_concentration(self):
        criteria = [
            {"criteria_id": f"C{i}", "criteria_text": f"Criterion {i}", "criteria_type": "essential", "criterion_categories": ["experience"]}
            for i in range(1, 4)
        ]
        model = {"limit_scope": "unspecified", "criteria": criteria}
        matches = {"matches": [
            {"criteria_id": f"C{i}", "matched_evidence": ["EV1"], "match_type": "direct", "coverage": "strong"}
            for i in range(1, 4)
        ]}
        ckb = [{"evidence_id": "EV1", "source_section": "Work Experience > Example Agency > Officer"}]

        plan = build_selection_plan(model, matches, ckb)

        self.assertEqual(plan["primary_evidence_reuse"], {"EV1": 3})
        self.assertTrue(any("used for 3 criteria" in warning for warning in plan["warnings"]))
        self.assertTrue(any("100%" in warning for warning in plan["warnings"]))

    def test_hard_validation_recomputes_words_and_rejects_unmatched_evidence(self):
        response = {
            "evidence_used": ["EV2"],
            "star": {"situation": "S", "task": "T", "action": "A", "result": "R"},
            "final_response": " ".join(["word"] * 106),
            "word_count": 1,
        }
        plan_item = {"allocated_word_limit": 100, "matched_evidence": ["EV1"]}

        result = hard_validate_response(response, plan_item)

        self.assertEqual(result["actual_word_count"], 106)
        self.assertTrue(result["word_limit_exceeded"])
        self.assertIn("unmatched_evidence_used", {item["code"] for item in result["issues"]})


if __name__ == "__main__":
    unittest.main()
