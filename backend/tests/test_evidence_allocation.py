import copy
import unittest

from app.evidence_allocation import apply_selection_allocation, build_evidence_allocation


def ev(evidence_id, quality="medium", result=""):
    return {"evidence_id": evidence_id, "evidence_quality": quality, "result": result}


class EvidenceAllocationTests(unittest.TestCase):
    def test_private_application_without_selection_plan_allocates_cover_evidence(self):
        resume = {"selected_evidence": [
            {"evidence_id": "ADMIN", "curation_action": "feature", "evidence_framing": "direct"},
            {"evidence_id": "BRIDGE", "curation_action": "include_concisely", "evidence_framing": "adjacent"},
        ]}
        decision = {"requirements": [
            {"criteria_id": "C1", "importance": "essential", "evidence_classification": "verified_match", "matched_evidence": ["ADMIN"]},
            {"criteria_id": "C2", "importance": "essential", "evidence_classification": "adjacent_match", "matched_evidence": ["BRIDGE"]},
            {"criteria_id": "C3", "importance": "desirable", "evidence_classification": "confirmed_gap", "matched_evidence": []},
        ]}
        plan = build_evidence_allocation(resume, {"items": []}, [ev("ADMIN", "high", "Result"), ev("BRIDGE")], decision)
        by_id = {item["evidence_id"]: item for item in plan["items"]}
        self.assertEqual(by_id["ADMIN"]["cover_letter"], {"use": "primary", "purpose": "differentiator"})
        self.assertEqual(by_id["BRIDGE"]["cover_letter"]["purpose"], "bridge")
        self.assertNotIn("C3", str(plan))

    def test_resume_breadth_can_remain_selection_depth(self):
        resume = {"selected_evidence": [{"evidence_id": "EV1", "curation_action": "feature", "evidence_framing": "direct"}]}
        selection = {"items": [{"criteria_id": "C1", "matched_evidence": ["EV1"], "match_type": "direct"}]}
        plan = build_evidence_allocation(resume, selection, [ev("EV1", "high", "Result")])
        item = plan["items"][0]
        self.assertEqual(item["resume"], {"use": "primary", "purpose": "breadth"})
        self.assertEqual(item["selection_criteria"][0]["use"], "primary")
        self.assertEqual(item["selection_criteria"][0]["purpose"], "criterion_depth")

    def test_equivalent_alternatives_reduce_primary_concentration(self):
        selection = {"items": [
            {"criteria_id": "C1", "matched_evidence": ["EV1", "EV2"], "match_type": "direct"},
            {"criteria_id": "C2", "matched_evidence": ["EV1", "EV2"], "match_type": "direct"},
        ]}
        plan = build_evidence_allocation({}, selection, [ev("EV1"), ev("EV2")])
        enriched = apply_selection_allocation(selection, plan)
        self.assertEqual([next(x["evidence_id"] for x in item["evidence_allocation"] if x["use"] == "primary") for item in enriched["items"]], ["EV1", "EV2"])
        self.assertEqual(enriched["items"][0]["matched_evidence"], ["EV1", "EV2"])

    def test_weaker_alternative_does_not_displace_strongest(self):
        selection = {"items": [{"criteria_id": "C1", "matched_evidence": ["WEAK", "STRONG"], "match_type": "direct"}]}
        plan = build_evidence_allocation({}, selection, [ev("WEAK", "low"), ev("STRONG", "high", "Result")])
        assignments = {x["evidence_id"]: x["selection_criteria"][0]["use"] for x in plan["items"]}
        self.assertEqual(assignments, {"WEAK": "allowed_if_needed", "STRONG": "primary"})
        cover = {item["evidence_id"]: item["cover_letter"]["use"] for item in plan["items"]}
        self.assertEqual(cover, {"WEAK": "avoid", "STRONG": "allowed_if_needed"})

    def test_adjacent_stays_bridge_and_decision_gaps_are_not_allocated(self):
        selection = {"items": [
            {"criteria_id": "A", "matched_evidence": ["ADJ"], "match_type": "inferred"},
            {"criteria_id": "G", "matched_evidence": ["GAP"], "match_type": "direct"},
        ]}
        decision = {"requirements": [{"criteria_id": "G", "evidence_classification": "unverified_possible"}]}
        plan = build_evidence_allocation({}, selection, [ev("ADJ"), ev("GAP")], decision)
        self.assertEqual([item["evidence_id"] for item in plan["items"]], ["ADJ"])
        self.assertEqual(plan["items"][0]["framing"], "adjacent")
        self.assertEqual(plan["items"][0]["cover_letter"]["purpose"], "bridge")

    def test_allocation_does_not_mutate_resume_or_selection_plans(self):
        resume = {"selected_evidence": [{"evidence_id": "EV1", "curation_action": "feature", "evidence_framing": "direct"}]}
        selection = {"items": [{"criteria_id": "C1", "matched_evidence": ["EV1"], "match_type": "direct"}]}
        before = copy.deepcopy((resume, selection))
        build_evidence_allocation(resume, selection, [ev("EV1")])
        self.assertEqual((resume, selection), before)


if __name__ == "__main__":
    unittest.main()
