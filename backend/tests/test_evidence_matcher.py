import json
import unittest

from app.evidence_matcher import matched_evidence_pack, normalise_match_result, validate_match_result


class EvidenceMatcherTests(unittest.TestCase):
    def setUp(self):
        self.ckb = [
            {"evidence_id": "EV001", "evidence_type": "experience", "source_section": "Work", "source_text": "Prepared monthly project reports."},
            {"evidence_id": "EV002", "evidence_type": "education", "source_section": "Education", "source_text": "Bachelor of Business."},
        ]
        self.job_model = {"criteria": [
            {"criteria_id": "C1", "criteria_text": "Reporting experience"},
            {"criteria_id": "C2", "criteria_text": "Relevant qualification"},
        ]}

    def test_filters_unknown_evidence_and_fills_missing_criterion(self):
        raw = {"matches": [{
            "criteria_id": "C1", "matched_evidence": ["EV001", "INVENTED"],
            "match_type": "direct", "coverage": "strong", "reasoning": "Direct reporting evidence.",
        }]}

        result = normalise_match_result(raw, self.job_model, self.ckb)

        self.assertEqual(result["matches"][0]["matched_evidence"], ["EV001"])
        self.assertEqual(result["matches"][1]["criteria_id"], "C2")
        self.assertEqual(result["matches"][1]["match_type"], "insufficient")
        self.assertEqual(result["unused_evidence"], ["EV002"])
        self.assertEqual(validate_match_result(result, self.job_model, self.ckb), [])

    def test_builds_generation_pack_only_from_matched_ids(self):
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": ["EV001"]}]}

        pack = matched_evidence_pack(json.dumps(self.ckb), json.dumps(matches))

        self.assertEqual([item["evidence_id"] for item in pack], ["EV001"])
        self.assertEqual(pack[0]["source_text"], "Prepared monthly project reports.")


if __name__ == "__main__":
    unittest.main()
