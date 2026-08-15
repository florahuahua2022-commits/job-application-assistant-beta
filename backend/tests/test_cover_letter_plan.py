import unittest
from types import SimpleNamespace

from app.cover_letter_plan import COVER_LETTER_PLAN_SCHEMA_VERSION, build_cover_letter_plan, selected_cover_letter_evidence_ids


class CoverLetterPlanTests(unittest.TestCase):
    def setUp(self):
        self.job_model = {"criteria": [
            {"criteria_id": "C1", "criteria_text": "Stakeholder engagement", "criteria_type": "essential"},
            {"criteria_id": "C2", "criteria_text": "Government reporting", "criteria_type": "essential"},
            {"criteria_id": "C3", "criteria_text": "Procurement", "criteria_type": "desirable"},
        ]}
        self.matches = {"matches": [
            {"criteria_id": "C1", "matched_evidence": ["EV1"], "match_type": "direct", "coverage": "strong"},
            {"criteria_id": "C2", "matched_evidence": ["EV2"], "match_type": "direct", "coverage": "partial"},
            {"criteria_id": "C3", "matched_evidence": [], "match_type": "insufficient", "coverage": "weak"},
        ]}
        self.ckb = [
            {"evidence_id": "EV1", "source_section": "Work Experience > Agency > Officer"},
            {"evidence_id": "EV2", "source_section": "Project > Reporting improvement"},
        ]

    def test_plan_prioritises_strong_requirements_and_selects_at_most_two_evidence_items(self):
        profile = SimpleNamespace(
            target_direction="Government project roles", motivation="Improve public services",
            writing_tone="concise_direct", preferences_notes="One page",
        )

        plan = build_cover_letter_plan(self.job_model, self.matches, self.ckb, profile)

        self.assertEqual(COVER_LETTER_PLAN_SCHEMA_VERSION, "1.0")
        self.assertEqual(plan["priorities"][0]["criteria_id"], "C1")
        self.assertEqual([item["evidence_id"] for item in plan["selected_evidence"]], ["EV1", "EV2"])
        self.assertEqual(plan["evidence_gaps"], ["C3"])
        self.assertEqual(sum(item["target_share"] for item in plan["narrative_plan"]), 1.0)
        self.assertEqual(plan["declared_intent"]["source"], "user_declared_intent_not_career_evidence")
        self.assertEqual(selected_cover_letter_evidence_ids(plan), {"EV1", "EV2"})

    def test_plan_prefers_evidence_not_already_detailed_in_selection_criteria(self):
        plan = build_cover_letter_plan(self.job_model, self.matches, self.ckb, evidence_already_detailed=["EV1"])

        self.assertEqual(plan["selected_evidence"][0]["evidence_id"], "EV2")
        self.assertFalse(plan["selected_evidence"][0]["previously_detailed"])

    def test_plan_forbids_invented_values_when_motivation_is_missing(self):
        profile = SimpleNamespace(
            target_direction="", motivation="", writing_tone="natural_professional", preferences_notes="",
        )

        plan = build_cover_letter_plan(self.job_model, self.matches, self.ckb, profile)

        alignment = next(item for item in plan["narrative_plan"] if item["section"] == "role_and_organisation_alignment")
        self.assertIn("do not invent applicant motivation", alignment["purpose"])


if __name__ == "__main__":
    unittest.main()
