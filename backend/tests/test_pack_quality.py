import json
import unittest

from app.pack_quality import document_evidence_issues, persist_selection_contract, required_generated_documents


class PackQualityTests(unittest.TestCase):
    def test_private_unknown_documents_are_not_invented(self):
        requirements = {"documents": {
            "resume": {"requirement": "unknown", "format": "unknown"},
            "cover_letter": {"requirement": "unknown", "format": "unknown"},
            "selection_criteria": {"requirement": "unknown", "format": "unknown"},
        }}
        self.assertEqual(required_generated_documents(requirements), ())

    def test_only_required_standalone_documents_are_routed(self):
        requirements = {"documents": {
            "resume": {"requirement": "required", "format": "standalone"},
            "cover_letter": {"requirement": "optional", "format": "standalone"},
            "selection_criteria": {"requirement": "required", "format": "embedded_in_cover_letter"},
        }}
        self.assertEqual(required_generated_documents(requirements), ("tailored_resume",))

    def test_resume_and_cover_letter_used_ids_must_be_selected(self):
        plan = json.dumps({"selected_evidence": [{"evidence_id": "KEEP"}]})
        for document_type in ("tailored_resume", "cover_letter"):
            with self.subTest(document_type=document_type):
                issues = document_evidence_issues(document_type, plan, '["KEEP","OTHER"]')
                self.assertEqual(len(issues), 1)
                self.assertIn("unselected_evidence", issues[0]["code"])

    def test_selection_criteria_ids_stay_within_each_criterion_allow_list(self):
        bundle = {
            "selection_plan": {"items": [{"criteria_id": "C1", "matched_evidence": ["E1"]}]},
            "responses": [{"criteria_id": "C1", "evidence_used": ["E2"]}],
        }
        issues = document_evidence_issues("selection_criteria", json.dumps(bundle), '["E2"]')
        self.assertIn("selection_criteria_unselected_evidence", [item["code"] for item in issues])

    def test_exact_applied_selection_plan_is_persisted_without_mutation(self):
        bundle = {"responses": []}
        plan = {"items": [{"criteria_id": "C1", "matched_evidence": ["E1"]}]}
        allocation = {"evidence": [{"evidence_id": "E1"}]}
        persisted = persist_selection_contract(bundle, plan, allocation)
        self.assertEqual(persisted["selection_plan"], plan)
        self.assertEqual(persisted["evidence_allocation"], allocation)
        self.assertEqual(bundle, {"responses": []})
