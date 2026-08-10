import json
import unittest
from unittest.mock import patch

from app import ai
from app.evidence_matcher import matched_evidence_pack, normalise_match_result
from app.reviewer import normalise_review_result
from app.selection_logic import (
    allocate_word_limits,
    build_selection_plan,
    criteria_requiring_confirmation,
    hard_validate_response,
)


class FrozenSelectionCriteriaAcceptanceTests(unittest.TestCase):
    def test_missing_procurement_experience_stays_weak_and_requires_confirmation(self):
        ckb = [{
            "evidence_id": "EV001",
            "evidence_type": "experience",
            "source_section": "Work Experience > Community Agency > Project Officer",
            "source_text": "Prepared monthly project reports and coordinated community meetings.",
        }]
        job_model = {"limit_scope": "unspecified", "criteria": [{
            "criteria_id": "C1",
            "criteria_text": "Demonstrated procurement and contract-management experience",
            "criteria_type": "essential",
            "criterion_categories": ["experience", "technical"],
        }]}

        matches = normalise_match_result({"matches": []}, job_model, ckb)
        plan = build_selection_plan(job_model, matches, ckb)

        self.assertEqual(matches["matches"][0]["matched_evidence"], [])
        self.assertEqual(matches["matches"][0]["match_type"], "insufficient")
        self.assertEqual(plan["items"][0]["evidence_status"], "weak")
        self.assertEqual(criteria_requiring_confirmation(plan), {"C1"})

    def test_qualification_criterion_can_use_qualification_evidence_directly(self):
        ckb = [{
            "evidence_id": "EV-Q1",
            "evidence_type": "qualification",
            "source_section": "Qualifications > PRINCE2 Foundation",
            "source_text": "PRINCE2 Foundation Certificate, PeopleCert, 2025.",
        }]
        job_model = {"limit_scope": "per_criteria", "per_criteria_word_limit": 250, "criteria": [{
            "criteria_id": "C1",
            "criteria_text": "Relevant project-management qualification",
            "criteria_type": "essential",
            "criterion_categories": ["qualification"],
        }]}
        raw = {"matches": [{
            "criteria_id": "C1",
            "matched_evidence": ["EV-Q1"],
            "match_type": "direct",
            "coverage": "strong",
            "reasoning": "The certificate directly satisfies the qualification requirement.",
        }]}

        matches = normalise_match_result(raw, job_model, ckb)
        plan = build_selection_plan(job_model, matches, ckb)
        pack = matched_evidence_pack(json.dumps(ckb), json.dumps(matches))

        self.assertEqual(plan["items"][0]["evidence_status"], "strong")
        self.assertEqual(pack[0]["evidence_type"], "qualification")
        self.assertEqual(pack[0]["source_text"], ckb[0]["source_text"])

    def test_best_evidence_is_not_removed_when_one_employer_dominates(self):
        criteria = [{
            "criteria_id": f"C{number}",
            "criteria_text": f"Criterion {number}",
            "criteria_type": "essential",
            "criterion_categories": ["experience"],
        } for number in range(1, 4)]
        job_model = {"limit_scope": "unspecified", "criteria": criteria}
        matches = {"matches": [{
            "criteria_id": item["criteria_id"],
            "matched_evidence": ["EV001"],
            "match_type": "direct",
            "coverage": "strong",
            "reasoning": "This is the strongest available evidence.",
        } for item in criteria]}
        ckb = [{
            "evidence_id": "EV001",
            "source_section": "Work Experience > Long-term Agency > Senior Officer",
        }]

        plan = build_selection_plan(job_model, matches, ckb)

        self.assertTrue(all(item["matched_evidence"] == ["EV001"] for item in plan["items"]))
        self.assertTrue(any("100%" in warning for warning in plan["warnings"]))

    def test_total_word_limit_is_never_exceeded_by_allocations(self):
        job_model = {"limit_scope": "total", "total_word_limit": 750, "criteria": [
            {"criteria_id": "C1", "criteria_type": "essential", "criterion_categories": ["experience", "behaviour"]},
            {"criteria_id": "C2", "criteria_type": "essential", "criterion_categories": ["technical"]},
            {"criteria_id": "C3", "criteria_type": "desirable", "criterion_categories": ["knowledge"]},
        ]}

        allocations = allocate_word_limits(job_model)

        self.assertEqual(sum(allocations.values()), 750)
        self.assertGreater(allocations["C1"], allocations["C3"])

    def test_generator_cannot_bypass_retrieval_with_an_unmatched_item(self):
        response = {
            "criteria_id": "C1",
            "evidence_used": ["EV-NOT-MATCHED"],
            "star": {"situation": "S", "task": "T", "action": "A", "result": "R"},
            "final_response": "I managed procurement processes and vendor contracts.",
        }
        plan_item = {"allocated_word_limit": 300, "matched_evidence": []}

        result = hard_validate_response(response, plan_item)

        self.assertFalse(result["valid"])
        self.assertIn("unmatched_evidence_used", {issue["code"] for issue in result["issues"]})

    def test_generator_retries_evaluative_and_copied_criterion_wording(self):
        criterion = "Proven experience in event management stakeholder engagement and project coordination."
        response = {
            "criteria_id": "C1",
            "evidence_used": ["EV1"],
            "star": {"situation": "S", "task": "T", "action": "A", "result": "R"},
            "final_response": criterion + " This demonstrates proven capability in the area.",
        }
        plan_item = {"criteria_text": criterion, "allocated_word_limit": 300, "matched_evidence": ["EV1"]}

        result = hard_validate_response(response, plan_item)

        self.assertFalse(result["valid"])
        self.assertIn("unsupported_evaluative_wording", {issue["code"] for issue in result["issues"]})
        self.assertIn("jd_wording_repeated", {issue["code"] for issue in result["issues"]})

    def test_reviewer_surfaces_fabricated_figures_and_over_inference(self):
        raw = {"results": [
            {"criteria_id": "C1", "status": "fail", "issues": [{
                "type": "fabricated_figure",
                "description": "The claimed 35% saving is absent from source_text.",
            }]},
            {"criteria_id": "C2", "status": "fail", "issues": [{
                "type": "unsupported_inference",
                "description": "The response overstates adjacent experience as direct contract management.",
            }]},
        ]}

        result = normalise_review_result(raw, ["C1", "C2"])

        self.assertEqual(result["status"], "fail")
        self.assertEqual(
            {issue["type"] for item in result["results"] for issue in item["issues"]},
            {"fabricated_figure", "unsupported_inference"},
        )

    def test_weak_generator_prompt_has_no_evidence_and_forbids_fabrication(self):
        ckb = json.dumps([{
            "evidence_id": "EV001",
            "source_text": "Prepared monthly reports.",
        }])
        plan = json.dumps({"items": [{
            "criteria_id": "C1",
            "criteria_text": "Procurement and contract management",
            "allocated_word_limit": 100,
            "matched_evidence": [],
            "match_type": "insufficient",
            "coverage": "weak",
            "evidence_status": "weak",
        }]})
        conservative = json.dumps({
            "criteria_id": "C1",
            "evidence_used": [],
            "star": {"situation": "", "task": "", "action": "", "result": ""},
            "final_response": "My resume does not provide a direct procurement or contract-management example.",
            "word_count": 11,
        })

        with patch.object(ai, "_openai_draft", return_value=conservative) as provider:
            result = ai.generate_selection_criteria_bundle(ckb, plan)

        prompt = provider.call_args.args[0]
        self.assertIn("If evidence is insufficient, do not fabricate a story", prompt)
        self.assertIn('"matched_evidence": []', prompt)
        self.assertEqual(result["responses"][0]["evidence_used"], [])


if __name__ == "__main__":
    unittest.main()
