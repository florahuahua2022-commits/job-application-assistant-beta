import unittest
from unittest.mock import patch

from openai import APIConnectionError

from app import ai


class GenerateDraftTests(unittest.TestCase):
    def test_resume_review_errors_use_only_safe_fix_types(self):
        review = {"results": [{"issues": [
            {"type": "unsupported_claim", "severity": "critical", "description": "Excel is not mentioned in source_text.", "location": "Advanced Excel"},
            {"type": "unsupported_inference", "severity": "major", "description": "The CKB does not provide dates supporting this duration.", "location": "10+ years"},
            {"type": "style_only", "severity": "advisory", "description": "Split a long sentence.", "location": "Summary"},
        ]}]}

        errors = ai.classify_resume_review_errors(review)

        self.assertEqual([item["fix_type"] for item in errors], ["remove", "remove_or_soften"])
        self.assertEqual([item["id"] for item in errors], ["err_1", "err_2"])

    def test_resume_auto_fix_prompt_prefers_removal_and_uses_ckb_only(self):
        errors = [{"id": "err_1", "claim": "Advanced Excel", "issue": "Not in CKB", "fix_type": "remove"}]
        with patch.object(ai, "_openai_draft", return_value="## Professional Summary\nSupported reporting.") as provider:
            fixed = ai.auto_fix_tailored_resume("## Key Skills\n- Advanced Excel", errors, '[{"source_text":"Supported reporting."}]')

        prompt = provider.call_args.args[0]
        self.assertIn('For fix_type "remove", delete', prompt)
        self.assertIn("only ground truth", prompt)
        self.assertNotIn("Advanced Excel", fixed)

    def test_resume_repair_stops_after_two_corrections_and_final_validation(self):
        failed = {"status": "fail", "results": [{"issues": [{
            "type": "unsupported_claim", "severity": "critical", "description": "Excel is not mentioned.", "location": "Excel"
        }]}]}
        with patch.object(ai, "review_tailored_resume", side_effect=[failed, failed, failed]) as reviewer, patch.object(
            ai, "auto_fix_tailored_resume", side_effect=["fixed once", "fixed twice"]
        ) as fixer:
            content, review = ai.repair_tailored_resume("draft", "[]", "{}", "{}")

        self.assertEqual(content, "fixed twice")
        self.assertEqual(reviewer.call_count, 3)
        self.assertEqual(fixer.call_count, 2)
        self.assertEqual(review["generation_status"], "needs_ckb_update")

    def test_resume_repair_returns_early_when_clean(self):
        clean = {"status": "pass", "results": [{"issues": []}]}
        with patch.object(ai, "review_tailored_resume", return_value=clean) as reviewer, patch.object(
            ai, "auto_fix_tailored_resume"
        ) as fixer:
            content, review = ai.repair_tailored_resume("draft", "[]", "{}", "{}")

        self.assertEqual(content, "draft")
        self.assertEqual(reviewer.call_count, 1)
        fixer.assert_not_called()
        self.assertEqual(review["generation_status"], "clean")

    def test_resume_reviewer_checks_factual_curation_and_relevance(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared monthly reports."}]'
        job_model = '{"criteria":[{"criteria_id":"C1","criteria_text":"Reporting"}]}'
        plan = '{"selected_evidence":[{"evidence_id":"EV001"}],"required_sections":["Professional Summary","Key Skills","Work Experience"]}'
        reviewer_output = '{"status":"fail","issues":[{"type":"fabricated_figure","description":"The 40% result is not in the CKB."}]}'
        with patch.object(ai, "_openai_draft", return_value=reviewer_output) as provider:
            result = ai.review_tailored_resume(ckb, job_model, plan, "## Professional Summary\nText")

        prompt = provider.call_args.args[0]
        self.assertIn("factual and relevance Reviewer", prompt)
        self.assertIn("Do not penalise factual compression or reordering", prompt)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["results"][0]["issues"][0]["severity"], "critical")

    def test_resume_generation_prompt_contains_strict_ckb_constraint(self):
        plan = '{"selected_evidence":[{"evidence_id":"EV001","source_text":"Prepared reports."}]}'
        with patch.object(ai, "_openai_draft", return_value='CV\n<!-- GENERATION_META {"used_experiences":["EV001"],"closing_styles":[]} -->') as provider:
            ai.generate_draft("Resume", "JD", "tailored_resume", resume_plan_json=plan)

        prompt = provider.call_args.args[0]
        self.assertIn("CRITICAL CONSTRAINT", prompt)
        self.assertIn("never name tools that do not literally appear", prompt)

    def test_cover_letter_reviewer_checks_grounding_intent_and_priorities(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared monthly reports."}]'
        job_model = '{"criteria":[{"criteria_id":"C1","criteria_text":"Reporting"}]}'
        plan = '{"priorities":[{"criteria_id":"C1"}],"selected_evidence":[{"evidence_id":"EV001"}]}'
        reviewer_output = '{"status":"fail","issues":[{"type":"unsupported_motivation","description":"The motivation was not declared."}],"recommendation":"Remove the claim."}'
        with patch.object(ai, "_openai_draft", return_value=reviewer_output) as provider:
            result = ai.review_cover_letter(ckb, job_model, plan, "Motivation: Not provided", "Cover letter text")

        prompt = provider.call_args.args[0]
        self.assertIn("intent may support motivation", prompt)
        self.assertIn("requirement_omission", prompt)
        self.assertIn("fabricated_entity", prompt)
        self.assertIn("only against COVER LETTER PLAN priorities", prompt)
        self.assertIn("not a second Selection Criteria response", prompt)
        self.assertIn("short organisation field is not an exclusive canonical name", prompt)
        self.assertIn("used only to identify or honestly acknowledge an evidence gap", prompt)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["results"][0]["issues"][0]["severity"], "major")

    def test_cover_letter_repair_removes_unsupported_claim_and_rechecks(self):
        failed = {"status":"fail","results":[{"issues":[{"type":"unsupported_claim","severity":"critical","description":"Over a decade is unsupported.","location":"over a decade"}]}],"telemetry":{}}
        passed = {"status":"pass","results":[{"issues":[]}],"telemetry":{}}
        with patch.object(ai, "review_cover_letter", side_effect=[failed, passed]), patch.object(
            ai, "_selection_provider_response", return_value="I have administrative experience."
        ) as provider:
            content, review = ai.repair_cover_letter("I have over a decade of experience.", "[]", "{}", "{}", "Residency: permanent resident")
        self.assertEqual(content, "I have administrative experience.")
        self.assertEqual(review["generation_status"], "clean")
        self.assertIn("must not become more specific", provider.call_args.args[0])
        self.assertIn("fabricated_entity", provider.call_args.args[0])
        self.assertIn("motivation is absent or \"Not provided\"", provider.call_args.args[0])
        self.assertIn("Do not expand the letter to answer non-priority criteria", provider.call_args.args[0])

    def test_batch_reviewer_checks_all_responses_in_one_call_without_rewriting(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared monthly reports."}]'
        plan = '{"items":[{"criteria_id":"C1","criteria_text":"Reporting"},{"criteria_id":"C2","criteria_text":"Procurement"}]}'
        bundle = {"responses": [
            {"criteria_id": "C1", "evidence_used": ["EV001"], "final_response": "I prepared reports."},
            {"criteria_id": "C2", "evidence_used": [], "final_response": "I have no direct procurement example."},
        ]}
        reviewer_output = '{"results":[{"criteria_id":"C1","status":"pass","issues":[]},{"criteria_id":"C2","status":"fail","issues":[{"type":"unsupported_claim","description":"The claim needs evidence."}],"recommendation":"Review manually."}]}'
        with patch.object(ai, "_openai_draft", return_value=reviewer_output) as call:
            result = ai.review_selection_criteria_batch(ckb, plan, bundle)

        self.assertEqual(call.call_count, 1)
        self.assertIn("Do not rewrite", call.call_args.args[0])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["results"][1]["issues"][0]["type"], "unsupported_claim")

    def test_selection_repair_removes_unsupported_outcome_and_rechecks(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared monthly reports."}]'
        plan = '{"items":[{"criteria_id":"C1","criteria_text":"Reporting","allocated_word_limit":100,"matched_evidence":["EV001"]}]}'
        bundle = {"content": "old", "responses": [{"criteria_id":"C1","evidence_used":["EV001"],"star":{"situation":"S","task":"T","action":"Prepared reports","result":"Earned trust"},"final_response":"I prepared reports and earned trust.","word_count":8}]}
        failed = {"status":"fail","results":[{"criteria_id":"C1","status":"fail","issues":[{"type":"unsupported_claim","description":"Trust is unsupported."}]}],"telemetry":{}}
        passed = {"status":"pass","results":[{"criteria_id":"C1","status":"pass","issues":[]}],"telemetry":{}}
        fixed = '{"criteria_id":"C1","evidence_used":["EV001"],"star":{"situation":"S","task":"T","action":"Prepared monthly reports","result":""},"final_response":"I prepared monthly reports."}'
        with patch.object(ai, "review_selection_criteria_batch", side_effect=[failed, passed]), patch.object(
            ai, "_selection_provider_response", return_value=fixed
        ):
            repaired, review = ai.repair_selection_criteria_bundle(ckb, plan, bundle)
        self.assertEqual(review["generation_status"], "clean")
        self.assertEqual(review["telemetry"]["repair_rounds"], 1)
        self.assertNotIn("trust", repaired["content"].lower())

    def test_generates_and_validates_each_selection_criterion_separately(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared monthly reports."},{"evidence_id":"EV002","source_text":"Completed a business degree."}]'
        plan = '{"items":[{"criteria_id":"C1","criteria_text":"Reporting","allocated_word_limit":100,"matched_evidence":["EV001"],"match_type":"direct","coverage":"strong","evidence_status":"strong"},{"criteria_id":"C2","criteria_text":"Qualification","allocated_word_limit":100,"matched_evidence":["EV002"],"match_type":"direct","coverage":"strong","evidence_status":"strong"}]}'
        outputs = [
            '{"criteria_id":"C1","evidence_used":["EV001"],"star":{"situation":"Monthly cycle","task":"Prepare reports","action":"Compiled records","result":"Reports submitted"},"final_response":"I prepared accurate monthly reports from verified project records.","word_count":999}',
            '{"criteria_id":"C2","evidence_used":["EV002"],"star":{"situation":"Study","task":"Complete degree","action":"Completed coursework","result":"Degree awarded"},"final_response":"I completed a Bachelor of Business qualification.","word_count":999}',
        ]
        with patch.object(ai, "_openai_draft", side_effect=outputs) as call:
            result = ai.generate_selection_criteria_bundle(ckb, plan)

        self.assertEqual(call.call_count, 2)
        self.assertEqual(len(result["responses"]), 2)
        self.assertEqual(result["used_experiences"], ["EV001", "EV002"])
        self.assertIn("## Reporting", result["content"])
        self.assertNotEqual(result["responses"][0]["word_count"], 999)
        self.assertTrue(result["responses"][0]["validation"]["valid"])

    def test_retries_only_the_criterion_that_fails_hard_validation(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared reports."},{"evidence_id":"EV002","source_text":"Completed degree."}]'
        plan = '{"items":[{"criteria_id":"C1","criteria_text":"Reporting","allocated_word_limit":100,"matched_evidence":["EV001"]},{"criteria_id":"C2","criteria_text":"Qualification","allocated_word_limit":100,"matched_evidence":["EV002"]}]}'
        invalid = '{"criteria_id":"C1","evidence_used":["FAKE"],"star":{"situation":"S","task":"T","action":"A","result":"R"},"final_response":"Reporting example."}'
        valid_c1 = '{"criteria_id":"C1","evidence_used":["EV001"],"star":{"situation":"S","task":"T","action":"A","result":"R"},"final_response":"I prepared reports."}'
        valid_c2 = '{"criteria_id":"C2","evidence_used":["EV002"],"star":{"situation":"S","task":"T","action":"A","result":"R"},"final_response":"I completed the degree."}'
        with patch.object(ai, "_openai_draft", side_effect=[invalid, valid_c1, valid_c2]) as call:
            result = ai.generate_selection_criteria_bundle(ckb, plan)

        self.assertEqual(call.call_count, 3)
        self.assertEqual(len(result["responses"]), 2)
        self.assertEqual(result["responses"][0]["evidence_used"], ["EV001"])
        self.assertEqual(result["telemetry"]["generator_retries"], 1)

    def test_batch_matcher_uses_all_criteria_and_rejects_unknown_evidence(self):
        ckb = '[{"evidence_id":"EV001","source_text":"Prepared reports."}]'
        job_model = '{"criteria":[{"criteria_id":"C1","criteria_text":"Reporting"},{"criteria_id":"C2","criteria_text":"Procurement"}]}'
        response = '{"matches":[{"criteria_id":"C1","matched_evidence":["EV001","FAKE"],"match_type":"direct","coverage":"strong","reasoning":"Relevant."}],"unused_evidence":[]}'
        with patch.object(ai, "_openai_draft", return_value=response) as call:
            result = ai.match_evidence_batch(ckb, job_model)

        self.assertEqual(call.call_count, 1)
        self.assertIn("C1", call.call_args.args[0])
        self.assertIn("C2", call.call_args.args[0])
        self.assertEqual(result["matches"][0]["matched_evidence"], ["EV001"])
        self.assertEqual(result["matches"][1]["match_type"], "insufficient")

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
        self.assertIn("Never use 'RE:'", prompt)

    def test_cover_letter_prompt_uses_traceable_priority_and_narrative_plan(self):
        plan = '{"schema_version":"1.0","priorities":[{"criteria_id":"C1","requirement":"Stakeholder engagement"}],"selected_evidence":[{"evidence_id":"EV1"}],"narrative_plan":[{"section":"role_and_organisation_alignment","target_share":0.45}]}'
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="Draft"
        ) as provider:
            ai.generate_draft("Resume", "Job description", "cover_letter", cover_letter_plan_json=plan)

        prompt = provider.call_args.args[0]
        self.assertIn("COVER LETTER PLAN", prompt)
        self.assertIn("role_and_organisation_alignment", prompt)
        self.assertIn("follow the COVER LETTER PLAN as authoritative", prompt)
        self.assertIn("do not turn the letter into a response to every Selection Criteria item", prompt)
        self.assertIn("If motivation is absent or 'Not provided'", prompt)

    def test_resume_prompt_requires_standard_sections_and_two_page_limit(self):
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="Draft"
        ) as deepseek_call:
            ai.generate_draft("Resume", "Job description", "tailored_resume")

        prompt = deepseek_call.call_args.args[0]
        self.assertIn("## Professional Summary", prompt)
        self.assertIn("## Key Skills", prompt)
        self.assertIn("## Work Experience", prompt)
        self.assertIn("no more than two pages", prompt)

    def test_resume_prompt_uses_curation_plan_and_declares_used_evidence(self):
        plan = '{"schema_version":"1.0","selected_evidence":[{"evidence_id":"EV1","curation_action":"feature"}],"maximum_pages":2}'
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="Draft"
        ) as provider:
            ai.generate_draft("Resume", "Job description", "tailored_resume", resume_plan_json=plan)

        prompt = provider.call_args.args[0]
        self.assertIn("RESUME CURATION PLAN", prompt)
        self.assertIn("Every evidence ID must exist in RESUME CURATION PLAN", prompt)
        self.assertIn("GENERATION_META", prompt)

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

    def test_short_selection_guidance_is_expanded_from_explicit_jd_requirements(self):
        with patch.object(ai.settings, "ai_provider", "deepseek"), patch.object(
            ai, "_deepseek_draft", return_value="Draft"
        ) as deepseek_call:
            ai.generate_draft(
                "Project Officer\nManaged stakeholder workshops and prepared reports.",
                "The role requires stakeholder engagement and government reporting.",
                "selection_criteria",
                "Focus on stakeholder engagement and reporting",
            )

        prompt = deepseek_call.call_args.args[0]
        self.assertIn("SELECTION INPUT MODE: brief user guidance", prompt)
        self.assertIn("Do not invent additional employer criteria", prompt)

    def test_long_selection_input_is_treated_as_full_criteria(self):
        criteria = "\n".join(f"Criterion {index}: " + "demonstrated capability " * 8 for index in range(1, 5))
        self.assertEqual(ai.selection_input_mode(criteria), "full selection criteria")


if __name__ == "__main__":
    unittest.main()
