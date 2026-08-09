import json
import re
import unittest
from pathlib import Path

from app.ckb import build_career_knowledge_base, validate_career_knowledge_base
from app.evidence_matcher import normalise_match_result, validate_match_result
from app.job_model import build_job_model, validate_job_model
from app.reviewer_core import normalise_document_review
from app.selection_logic import build_selection_plan


ROOT = Path(__file__).resolve().parents[2]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def token_overlap(left: str, right: str) -> bool:
    tokens = lambda value: set(re.findall(r"[a-z]{4,}", value.lower()))
    return bool(tokens(left) & tokens(right))


class FixedFixtureMatrixTests(unittest.TestCase):
    def test_all_six_resume_job_pairings_complete_or_degrade_honestly(self):
        matrix = load_json(ROOT / "fixtures" / "expected" / "baseline_matrix.json")
        self.assertEqual(matrix["fixture_version"], "1.0")
        self.assertEqual(len(matrix["pairings"]), 6)

        for resume_id, jd_id in matrix["pairings"]:
            with self.subTest(resume=resume_id, job=jd_id):
                resume = load_json(ROOT / "fixtures" / "resumes" / f"{resume_id}.json")
                jd = load_json(ROOT / "fixtures" / "job_descriptions" / f"{jd_id}.json")
                ckb = build_career_knowledge_base(resume["source_text"], json.dumps(resume["experiences"]))
                self.assertEqual(validate_career_knowledge_base(ckb), [])
                self.assertTrue(all(item["source_text"] in resume["source_text"] for item in ckb))

                model = build_job_model(
                    jd["job_description"], jd["selection_criteria"], jd["position_title"], jd["organisation"]
                )
                self.assertEqual(validate_job_model(model), [])
                for key, expected in jd["expected"].items():
                    if key == "critical_gap_for_sparse_resume":
                        continue
                    self.assertEqual(model[key], expected)

                raw_matches = []
                for criterion in model["criteria"]:
                    evidence_ids = [
                        item["evidence_id"] for item in ckb
                        if token_overlap(criterion["criteria_text"], item["source_text"])
                    ][:3]
                    raw_matches.append({
                        "criteria_id": criterion["criteria_id"],
                        "matched_evidence": evidence_ids,
                        "match_type": "direct" if evidence_ids else "insufficient",
                        "coverage": "partial" if evidence_ids else "weak",
                        "reasoning": "Fixture token match." if evidence_ids else "No supportable fixture evidence.",
                    })
                matches = normalise_match_result({"matches": raw_matches}, model, ckb)
                self.assertEqual(validate_match_result(matches, model, ckb), [])
                plan = build_selection_plan(model, matches, ckb)
                self.assertEqual(len(plan["items"]), len(model["criteria"]))
                if model["limit_scope"] == "total":
                    self.assertLessEqual(sum(item["allocated_word_limit"] for item in plan["items"]), model["total_word_limit"])

                if resume_id == "RES-B" and jd_id == "JD-C":
                    self.assertTrue(all(item["evidence_status"] != "strong" for item in plan["items"]))

    def test_human_labelled_reviewer_pass_and_fail_cases_are_calibrated(self):
        pass_case = load_json(ROOT / "regression" / "reviewer" / "pass" / "PASS-001.json")
        fail_case = load_json(ROOT / "regression" / "reviewer" / "fail" / "FAIL-001.json")

        pass_result = normalise_document_review(pass_case["reviewer_output"], pass_case["document_type"])
        fail_result = normalise_document_review(fail_case["reviewer_output"], fail_case["document_type"])

        self.assertEqual(pass_result["status"], pass_case["human_label"])
        self.assertEqual(fail_result["status"], fail_case["human_label"])
        self.assertEqual(fail_result["results"][0]["issues"][0]["severity"], fail_case["severity"])


if __name__ == "__main__":
    unittest.main()
