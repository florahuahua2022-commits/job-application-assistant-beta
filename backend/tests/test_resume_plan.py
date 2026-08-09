import unittest

import json

from app.resume_plan import RESUME_PLAN_SCHEMA_VERSION, build_resume_curation_plan, resume_evidence_pack, selected_resume_evidence_ids


class ResumeCurationPlanTests(unittest.TestCase):
    def test_plan_prioritises_relevant_evidence_and_preserves_required_sections(self):
        job_model = {"criteria": [
            {"criteria_id": "C1", "criteria_type": "essential"},
            {"criteria_id": "C2", "criteria_type": "desirable"},
        ]}
        matches = {"matches": [
            {"criteria_id": "C1", "matched_evidence": ["EV1", "EV2"], "match_type": "direct", "coverage": "strong"},
            {"criteria_id": "C2", "matched_evidence": ["EV2"], "match_type": "direct", "coverage": "partial"},
        ]}
        ckb = [
            {"evidence_id": "EV1", "evidence_type": "experience", "source_section": "Work > Agency", "evidence_quality": "high", "result": "Delivered on time"},
            {"evidence_id": "EV2", "evidence_type": "project", "source_section": "Projects", "evidence_quality": "medium", "result": ""},
            {"evidence_id": "EV3", "evidence_type": "experience", "source_section": "Unrelated work", "evidence_quality": "high", "result": "Increased sales"},
            {"evidence_id": "EV4", "evidence_type": "qualification", "source_section": "Qualifications", "evidence_quality": "medium", "result": ""},
        ]

        plan = build_resume_curation_plan(job_model, matches, ckb)

        self.assertEqual(RESUME_PLAN_SCHEMA_VERSION, "1.0")
        self.assertEqual(plan["required_sections"], ["Professional Summary", "Key Skills", "Work Experience"])
        self.assertEqual(plan["maximum_pages"], 2)
        self.assertEqual(plan["selected_evidence"][0]["evidence_id"], "EV2")
        self.assertIn("EV4", selected_resume_evidence_ids(plan))
        self.assertIn("EV3", plan["omitted_evidence_ids"])
        pack = resume_evidence_pack(json.dumps(ckb), json.dumps(plan))
        self.assertEqual({item["evidence_id"] for item in pack}, selected_resume_evidence_ids(plan))

    def test_plan_never_exceeds_configured_evidence_limit(self):
        ckb = [{"evidence_id": f"EV{i}", "evidence_type": "qualification", "evidence_quality": "medium"} for i in range(20)]

        plan = build_resume_curation_plan({"criteria": []}, {"matches": []}, ckb, max_evidence=6)

        self.assertEqual(len(plan["selected_evidence"]), 6)


if __name__ == "__main__":
    unittest.main()
