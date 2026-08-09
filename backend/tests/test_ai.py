import unittest
from unittest.mock import patch

from openai import APIConnectionError

from app import ai


class GenerateDraftTests(unittest.TestCase):
    def test_replaces_date_placeholder_with_written_current_date(self):
        expected = f"{ai.date.today().day} {ai.date.today().strftime('%B %Y')}"
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="[Date]\n\nDear Hiring Manager,"
        ):
            result = ai.generate_draft("resume", "JD", "cover_letter")

        self.assertTrue(result.startswith(expected))
        self.assertNotIn("[Date]", result)

    def setUp(self):
        self.settings = patch.multiple(
            ai.settings,
            ai_provider="openai",
            ai_fallback_to_deepseek=True,
            openai_api_key="test-openai-key",
            deepseek_api_key="test-deepseek-key",
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_uses_openai_by_default(self):
        with patch.object(ai, "_openai_draft", return_value="OpenAI draft") as openai_call, patch.object(
            ai, "_deepseek_draft"
        ) as deepseek_call:
            result = ai.generate_draft("resume", "JD", "cover_letter")

        self.assertEqual(result, "OpenAI draft")
        openai_call.assert_called_once()
        deepseek_call.assert_not_called()

    def test_falls_back_to_deepseek_when_openai_fails(self):
        error = APIConnectionError(request=None)
        with patch.object(ai, "_openai_draft", side_effect=error), patch.object(
            ai, "_deepseek_draft", return_value="DeepSeek draft"
        ) as deepseek_call:
            result = ai.generate_draft("resume", "JD", "tailored_resume")

        self.assertEqual(result, "DeepSeek draft")
        deepseek_call.assert_called_once()

    def test_can_select_deepseek_directly(self):
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="DeepSeek draft"
        ):
            result = ai.generate_draft("resume", "JD", "ats_analysis")

        self.assertEqual(result, "DeepSeek draft")

    def test_english_variant_is_configurable_without_changing_generation_logic(self):
        with patch.object(ai.settings, "target_english_variant", "Australian English"):
            self.assertEqual(ai.target_english_variant(), "Australian English")
            self.assertIn("professional Australian English", ai.safety_instruction())

        with patch.object(ai.settings, "target_english_variant", "British English"):
            self.assertIn("professional British English", ai.safety_instruction())

    def test_safety_instruction_blocks_unsupported_comparisons_and_jd_claims(self):
        instruction = ai.safety_instruction()

        self.assertIn("employer requirement, not as evidence", instruction)
        self.assertIn("Never compare the value, scale, complexity", instruction)
        self.assertIn("state the gap plainly", instruction)

    def test_cover_letter_prompt_requires_evidence_for_recruiter_relationship_and_matching_signoff(self):
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="Draft"
        ) as deepseek_call:
            ai.generate_draft("Resume", "Job description", "cover_letter", company="Example Company")

        prompt = deepseek_call.call_args.args[0]
        self.assertIn("Do not infer a recruiter/client relationship", prompt)
        self.assertIn("use 'Yours faithfully' after a generic salutation", prompt)

    def test_builds_ranked_evidence_pack_from_structured_experience(self):
        experiences = '[{"id":"EV-project","role_title":"Project Officer","organization":"Agency","responsibility":"Managed stakeholder workshops","result":"Delivered the project on time"},{"id":"EV-retail","role_title":"Assistant","organization":"Shop","responsibility":"Processed sales"}]'

        result = ai.build_evidence_pack(
            "resume",
            experiences,
            "The role requires stakeholder engagement and project delivery.",
        )

        self.assertEqual(result[0]["evidence_id"], "EV-project")
        self.assertIn("Managed stakeholder workshops", result[0]["source_text"])

    def test_falls_back_to_traceable_resume_excerpts(self):
        result = ai.build_evidence_pack(
            "Project Officer\nManaged consultation with community stakeholders and delivered clear reports.\nRetail Assistant\nServed customers.",
            "[]",
            "stakeholder consultation",
        )

        self.assertEqual(result[0]["evidence_id"], "RES001")
        self.assertIn("community stakeholders", result[0]["source_text"])

    def test_selection_criteria_prompt_uses_only_matched_resume_evidence(self):
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="Draft"
        ) as deepseek_call:
            ai.generate_draft(
                "Project Officer\nManaged stakeholder workshops and delivered reports.",
                "Stakeholder engagement is essential.",
                "selection_criteria",
                "Demonstrated stakeholder engagement",
            )

        prompt = deepseek_call.call_args.args[0]
        self.assertIn("MATCHED RESUME EVIDENCE", prompt)
        self.assertIn("requirements only, never applicant evidence", prompt)


if __name__ == "__main__":
    unittest.main()
