import unittest
from unittest.mock import patch

from app import ai
from app.government_writing_rules import GOVERNMENT_WRITING_RULES_VERSION, government_writing_rules


class GovernmentWritingRulesTests(unittest.TestCase):
    def test_shared_rules_are_versioned_and_preserve_grounding_boundaries(self):
        rules = government_writing_rules()

        self.assertEqual(GOVERNMENT_WRITING_RULES_VERSION, "1.0")
        self.assertIn("GOVERNMENT_WRITING_RULES_v1.0", rules)
        self.assertIn("traceable to supplied CKB source_text", rules)
        self.assertIn("not as evidence", rules)
        self.assertIn("only when the supporting source_text contains the number", rules)
        self.assertIn("never use RE: or Subject:", rules)
        self.assertIn("must not become managed, led, owned", rules)
        self.assertIn("does not by itself support claims of discretion", rules)

    def test_variant_is_configurable_without_forking_rules(self):
        self.assertIn("professional British English", government_writing_rules("British English"))
        self.assertIn("professional Australian English", government_writing_rules("Australian English"))

    def test_generator_and_reviewer_consume_the_same_versioned_rules(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared monthly reports."}]'
        plan = '{"items":[{"criteria_id":"C1","criteria_text":"Reporting","allocated_word_limit":100,"matched_evidence":["EV001"],"match_type":"direct","coverage":"strong","evidence_status":"strong"}]}'
        generated = '{"criteria_id":"C1","evidence_used":["EV001"],"star":{"situation":"Monthly cycle","task":"Prepare reports","action":"Compiled records","result":"Reports submitted"},"final_response":"I prepared monthly reports from verified records."}'
        review = '{"results":[{"criteria_id":"C1","status":"pass","issues":[]}]}'

        with patch.object(ai, "_openai_draft", side_effect=[generated, review]) as provider:
            bundle = ai.generate_selection_criteria_bundle(ckb, plan)
            ai.review_selection_criteria_batch(ckb, plan, bundle)

        generator_prompt = provider.call_args_list[0].args[0]
        reviewer_prompt = provider.call_args_list[1].args[0]
        self.assertIn("GOVERNMENT_WRITING_RULES_v1.0", generator_prompt)
        self.assertIn("GOVERNMENT_WRITING_RULES_v1.0", reviewer_prompt)
        self.assertIn("traceable to supplied CKB source_text", generator_prompt)
        self.assertIn("traceable to supplied CKB source_text", reviewer_prompt)

    def test_general_safety_instruction_uses_shared_rules(self):
        instruction = ai.safety_instruction()

        self.assertIn("GOVERNMENT_WRITING_RULES_v1.0", instruction)
        self.assertIn("traceable to supplied CKB source_text", instruction)


if __name__ == "__main__":
    unittest.main()
