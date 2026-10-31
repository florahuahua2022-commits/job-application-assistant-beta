"""Invented examples only; no applicant trace content belongs in this file."""
import json
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app import ai, main
from app.models import GeneratedDocument, GenerateRequest, JobApplication, Resume
from app.reviewer_core import normalise_finding, normalise_saved_review


class SourceClassificationTests(unittest.TestCase):
    def setUp(self):
        self.quote = "Assisted in sorting coloured telescope tokens."
        self.section = "Work Experience > Imaginary Moon Workshop > Token Clerk"
        self.ckb = [{"evidence_id": "EV_SYNTHETIC", "evidence_type": "experience",
                     "source_section": self.section,
                     "source_text": self.quote,
                     "time_period": {"start": "Jan 2018", "end": "Dec 2019"}}]
        self.role = {"source_section": self.section, "employer_marker": "Imaginary Moon Workshop",
                     "role_marker": "Token Clerk", "display_period": "Jan 2018 - Dec 2019",
                     "include_role_header": True, "selected_evidence_ids": ["EV_SYNTHETIC"]}
        self.plan = {"roles": [self.role], "selected_evidence": [{"evidence_id": "EV_SYNTHETIC"}]}
        self.content = "## Work Experience\n### Token Clerk | Imaginary Moon Workshop\nJan 2018 - Dec 2019\n- " + self.quote
        self.issue = {"type": "evidence_mismatch", "description": "The token duty overstates responsibility.",
                      "location_kind": "exact_quote", "location": self.quote,
                      "recommended_action": "Weaken the token duty."}

    def classify(self, issue=None, content=None, ckb=None, plan=None):
        return normalise_finding(self.issue if issue is None else issue,
                                 content=self.content if content is None else content,
                                 ckb=self.ckb if ckb is None else ckb,
                                 plan=self.plan if plan is None else plan)

    def test_internal_skills_omission_is_visible_consistency_advice(self):
        result = normalise_finding({"type": "internal_inconsistency",
                                    "description": "The Technical Skills list omits the sample tool LanternLedger."},
                                   ckb=[{**self.ckb[0], "source_text": "Used LanternLedger."}], plan=self.plan)
        self.assertEqual(result["severity"], "advisory")
        self.assertEqual(result["category"], "consistency")
        self.assertFalse(result["blocks_release"])

    def test_named_tool_omission_does_not_need_literal_word_tool(self):
        result = normalise_finding({"type": "internal_inconsistency",
                                    "description": "Technical Skills omits LanternLedger."},
                                   ckb=[{**self.ckb[0], "source_text": "Used LanternLedger."}], plan=self.plan)
        self.assertEqual(result["severity"], "advisory")
        self.assertEqual(result["category"], "consistency")
        self.assertFalse(result["blocks_release"])

    def test_skills_omission_with_real_error_or_obligation_still_blocks(self):
        ckb = [{**self.ckb[0], "source_text": "Used LanternLedger."}]
        base = "The Technical Skills list omits LanternLedger."
        self.assertFalse(normalise_finding({"type": "internal_inconsistency", "description": base}, ckb=ckb, plan=self.plan)["blocks_release"])
        for extra in [" The tool proficiency is unsupported.", " The phone is wrong.", " The identity is incorrect."]:
            with self.subTest(extra=extra):
                result = normalise_finding({"type": "internal_inconsistency", "description":
                    "The Technical Skills list omits LanternLedger." + extra}, ckb=ckb, plan=self.plan)
                self.assertTrue(result["blocks_release"])
        result = normalise_finding({"type": "internal_inconsistency", "obligation": "must_answer",
                                   "description": base}, ckb=ckb, plan=self.plan)
        self.assertTrue(result["blocks_release"])

    def test_jd_repetition_is_writing_advice(self):
        result = normalise_finding({"type": "jd_wording_repeated", "description": "The moon-ticket phrase repeats advertisement wording."})
        self.assertEqual(result["severity"], "advisory")
        self.assertEqual(result["category"], "writing")
        self.assertFalse(result["blocks_release"])

    def test_jd_repetition_with_factual_problem_still_blocks(self):
        for text in ["Advertisement wording claims unsupported telescope expertise.",
                     "The repeated phrase upgrades assisted work to led work.",
                     "The repeated phrase claims expertise without evidence.",
                     "The repeated phrase gives the wrong contact details."]:
            with self.subTest(text=text):
                self.assertTrue(normalise_finding({"type": "jd_wording_repeated", "description": text})["blocks_release"])

    def test_verbatim_same_role_duty_is_advisory_with_reason(self):
        for kind in ("evidence_mismatch", "unsupported_inference"):
            with self.subTest(kind=kind):
                issue = {**self.issue, "type": kind}
                result = self.classify(issue)
                self.assertEqual(result["severity"], "advisory")
                self.assertFalse(result["blocks_release"])
                self.assertEqual(result["classification_reason"], "verbatim_same_role_source")
                self.assertEqual(result["source_evidence_id"], "EV_SYNTHETIC")
                self.assertIn("same employer, role and period", result["recommended_action"])

    def test_other_employer_cannot_borrow_identical_duty(self):
        other = {**self.role, "source_section": "Work Experience > Imaginary Star Garage > Token Clerk",
                 "employer_marker": "Imaginary Star Garage", "selected_evidence_ids": []}
        plan = {**self.plan, "roles": [self.role, other]}
        self.assertTrue(self.classify(content=self.content.replace("Imaginary Moon Workshop", "Imaginary Star Garage"), plan=plan)["blocks_release"])

    def test_role_header_can_place_period_on_employer_line_or_same_line(self):
        for header in ["### Token Clerk\nImaginary Moon Workshop | Jan 2018 - Dec 2019",
                       "### Token Clerk | Imaginary Moon Workshop | Jan 2018 - Dec 2019",
                       "### Token Clerk\nImaginary Moon Workshop\nJan 2018 - Dec 2019"]:
            with self.subTest(header=header):
                result = self.classify(content="## Work Experience\n" + header + "\n- " + self.quote)
                self.assertFalse(result["blocks_release"])

    def test_assisted_to_led_still_blocks(self):
        stronger = self.quote.replace("Assisted in sorting", "Led the sorting of")
        self.assertTrue(self.classify({**self.issue, "location": stronger}, content=self.content.replace(self.quote, stronger))["blocks_release"])

    def test_other_persons_action_cannot_be_cut_out_of_source_sentence(self):
        quote = "managed telescope tokens."
        ckb = [{**self.ckb[0], "source_text": "Assisted the supervisor who managed telescope tokens."}]
        issue = {**self.issue, "type": "unsupported_inference", "location": quote,
                 "description": "The responsibility upgrades assisting a supervisor to managing."}
        self.assertTrue(self.classify(issue, content=self.content.replace(self.quote, quote), ckb=ckb)["blocks_release"])

    def test_explicit_cv_source_comparison_still_blocks_without_error_keywords(self):
        ckb = [{**self.ckb[0], "source_text": "Used LanternLedger."}]
        descriptions = {
            "jd_wording_repeated": "The CV repeats advertisement wording and says led token sorting; the source says assisted with token sorting.",
            "internal_inconsistency": "The Technical Skills list omits LanternLedger; the CV says led token sorting while the source says assisted with token sorting.",
        }
        for kind, description in descriptions.items():
            with self.subTest(kind=kind):
                control = "Technical Skills omits LanternLedger." if kind == "internal_inconsistency" else "The token phrase repeats advertisement wording."
                self.assertFalse(normalise_finding({"type": kind, "description": control}, ckb=ckb, plan=self.plan)["blocks_release"])
                self.assertTrue(normalise_finding({"type": kind, "description": description}, ckb=ckb, plan=self.plan)["blocks_release"])

    def test_wrong_role_employer_and_dates_still_block(self):
        for old, new in [("Token Clerk", "Token Director"), ("Imaginary Moon Workshop", "Imaginary Sun Shed"),
                         ("Jan 2018 - Dec 2019", "Jan 2015 - Dec 2023")]:
            with self.subTest(old=old):
                self.assertTrue(self.classify(content=self.content.replace(old, new))["blocks_release"])
        changed_plan = deepcopy(self.plan)
        changed_plan["roles"][0]["display_period"] = "Jan 2015 - Dec 2023"
        self.assertTrue(self.classify(content=self.content.replace("Jan 2018 - Dec 2019", "Jan 2015 - Dec 2023"), plan=changed_plan)["blocks_release"])

    def test_nonverbatim_or_partial_quote_still_blocks(self):
        for quote in [self.quote.replace("coloured", "colourful"), "sorting coloured telescope tokens"]:
            with self.subTest(quote=quote):
                self.assertTrue(self.classify({**self.issue, "location": quote})["blocks_release"])
        self.assertTrue(self.classify({**self.issue, "location_kind": "section"})["blocks_release"])

    def test_contacts_and_identity_cannot_use_source_quote_exception(self):
        for text in ["The contact phone is wrong.", "The applicant email is incorrect.",
                     "The applicant name is incorrect.",
                     "The citizenship statement conflicts with the profile."]:
            with self.subTest(text=text):
                self.assertTrue(self.classify({**self.issue, "description": text})["blocks_release"])

    def test_missing_context_unselected_evidence_and_unknown_header_still_block(self):
        self.assertTrue(normalise_finding({**self.issue, "classification_reason": "verbatim_same_role_source"})["blocks_release"])
        self.assertTrue(self.classify(ckb=[])["blocks_release"])
        self.assertTrue(self.classify(plan={**self.plan, "selected_evidence": []})["blocks_release"])
        content = self.content.replace("- " + self.quote, "### Token Director | Imaginary Sun Shed\nJan 2018 - Dec 2019\n- " + self.quote)
        self.assertTrue(self.classify(content=content)["blocks_release"])

    def test_saved_document_uses_frozen_source_and_preserves_real_blockers(self):
        review = {"status": "fail", "results": [{"criteria_id": "tailored_resume", "issues": [self.issue]}]}
        document = GeneratedDocument(application_id=1, document_type="tailored_resume", content=self.content,
            structured_content_json=json.dumps(self.plan), reviewer_json=json.dumps(review),
            trace_json=json.dumps({"inputs": {"ckb": self.ckb}}))
        result = main.saved_document_review(document)
        self.assertEqual(result["status"], "pass")
        review["results"][0]["issues"].append({"type": "fabricated_entity", "description": "An invented spacecraft licence."})
        document.reviewer_json = json.dumps(review)
        self.assertEqual(main.saved_document_review(document)["status"], "fail")

    def test_resume_reviewer_passes_context_without_changing_prompts(self):
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps({"issues": [self.issue]})):
            result = ai.review_tailored_resume(json.dumps(self.ckb), "{}", json.dumps(self.plan), self.content)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["results"][0]["issues"][0]["classification_reason"], "verbatim_same_role_source")

    def test_saved_review_context_does_not_mutate_original(self):
        review = {"status": "fail", "results": [{"criteria_id": "tailored_resume", "issues": [self.issue]}]}
        original = deepcopy(review)
        result = normalise_saved_review(review, content=self.content, ckb=self.ckb, plan=self.plan)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(review, original)

    def generate_synthetic_document(self, session, document_type="tailored_resume", review=None):
        resume = Resume(source_text="Synthetic master text", ckb_json=json.dumps(self.ckb))
        application = JobApplication(company="Imaginary Moon Workshop", position_title="Token Clerk",
                                     job_description="Synthetic token duties.", job_model_json='{"criteria":[]}')
        session.add(resume); session.add(application); session.commit()
        review = review or {"status": "fail", "results": [{"criteria_id": "tailored_resume", "issues": [self.issue]}]}
        with ExitStack() as stack:
            for name, value in {
                "application_master_resume": resume,
                "require_current_resume_snapshot": None,
                "application_ckb": (self.ckb, "current"),
                "serialise_job_model": application.job_model_json,
                "match_cache_is_current": True,
                "build_resume_curation_plan": deepcopy(self.plan),
                "build_cover_letter_plan": deepcopy(self.plan),
                "generate_draft": self.content + '\n<!-- GENERATION_META {"used_experiences":["EV_SYNTHETIC"]} -->',
                "repair_tailored_resume": (self.content, deepcopy(review)),
                "repair_cover_letter": (self.content, deepcopy(review)),
                "provider_response_telemetry": {},
            }.items():
                stack.enter_context(patch.object(main, name, return_value=value))
            return main.generate_document(GenerateRequest(application_id=application.id, document_type=document_type), session, None)

    def test_nonresume_generation_and_saved_read_ignore_structured_resume_plan(self):
        self.ckb[0]["source_text"] = "Used LanternLedger in the imaginary workshop."
        self.content = "Dear hiring team\n\nI sorted cardboard tokens."
        raw = {"status": "fail", "results": [{"criteria_id": "cover_letter", "issues": [
            {"type": "internal_inconsistency", "description": "Key Skills omits LanternLedger."}]}]}
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                document = self.generate_synthetic_document(session, "cover_letter", raw)
                self.assertTrue(json.loads(document.structured_content_json)["selected_evidence"])
                generated = json.loads(document.reviewer_json)
                self.assertEqual(generated["status"], "fail")
                self.assertTrue(generated["results"][0]["issues"][0]["blocks_release"])
                for kind in ("cover_letter", "selection_criteria"):
                    with self.subTest(kind=kind):
                        document.document_type = kind
                        reread = main.saved_document_review(document)
                        self.assertEqual(reread["status"], generated["status"])
                        self.assertTrue(reread["results"][0]["issues"][0]["blocks_release"])
        finally:
            engine.dispose()

    def test_real_generation_persists_source_context_used_on_read(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                document = self.generate_synthetic_document(session)
                document_id = document.id
                self.assertEqual(json.loads(document.reviewer_json)["status"], "pass")
            with Session(engine) as session:
                document = session.get(GeneratedDocument, document_id)
                self.assertEqual(json.loads(document.trace_json)["inputs"]["ckb"], self.ckb)
                self.assertEqual(json.loads(document.structured_content_json), self.plan)
                review = main.saved_document_review(document)
                self.assertEqual(review["status"], "pass")
                issue = review["results"][0]["issues"][0]
                self.assertEqual(issue["classification_reason"], "verbatim_same_role_source")
                self.assertFalse(issue["blocks_release"])
        finally:
            engine.dispose()

    def test_missing_saved_context_reblocks_previously_passing_review(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                document = self.generate_synthetic_document(session)
                original = json.loads(document.reviewer_json)
                self.assertEqual(original["status"], "pass")
                for missing in ("ckb", "plan"):
                    with self.subTest(missing=missing):
                        snapshot = GeneratedDocument.model_validate(document.model_dump())
                        if missing == "ckb":
                            trace = json.loads(snapshot.trace_json)
                            del trace["inputs"]["ckb"]
                            snapshot.trace_json = json.dumps(trace)
                        else:
                            snapshot.structured_content_json = "{}"
                        review = main.saved_document_review(snapshot)
                        self.assertEqual(review["status"], "fail")
                        self.assertTrue(review["results"][0]["issues"][0]["blocks_release"])
                        issue = review["results"][0]["issues"][0]
                        self.assertEqual(issue["display_level"], "warning")
                        self.assertEqual(issue["description"], "Source context unavailable — cannot re-verify")
                        self.assertEqual(review["factual_status"], "pending")
                        self.assertEqual(review["quality_status"], "pending")
                        self.assertEqual(json.loads(document.reviewer_json), original)
        finally:
            engine.dispose()

    def test_missing_source_warning_does_not_hide_contact_error(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                document = self.generate_synthetic_document(session)
                raw = json.loads(document.reviewer_json)
                raw["results"].append({"criteria_id": "delivery_hard_checks", "issues": [
                    {"type": "canonical_phone_conflict", "description": "The synthetic phone differs from Profile."}]})
                document.reviewer_json = json.dumps(raw)
                document.trace_json = "{}"
                result = main.saved_document_review(document)
                issues = [i for row in result["results"] for i in row["issues"]]
                self.assertTrue(any(i.get("display_level") == "warning" and i["blocks_release"] for i in issues))
                phone = next(i for i in issues if i["type"] == "canonical_phone_conflict")
                self.assertEqual(phone["display_level"], "error")
                self.assertTrue(phone["blocks_release"])
        finally:
            engine.dispose()


class SkillsDirectionTests(unittest.TestCase):
    def setUp(self):
        self.ckb = [{"evidence_id": "EV_TOOL_SYNTHETIC", "source_text": "Used NebulaIndex to file paper-star tokens."}]
        self.plan = {"selected_evidence": [{"evidence_id": "EV_TOOL_SYNTHETIC"}]}

    def classify(self, text, **context):
        return normalise_finding({"type": "internal_inconsistency", "description": text}, **context)

    def test_listed_tool_missing_from_body_or_source_always_blocks(self):
        for verb in ("lists", "includes", "contains", "claims"):
            for absent in ("does not appear in", "is not found in", "is absent from"):
                for scope in ("Work Experience", "body", "CKB"):
                    with self.subTest(verb=verb, absent=absent, scope=scope):
                        text = f"Technical Skills {verb} AstralLedger, but AstralLedger {absent} {scope} and is absent from CKB."
                        self.assertTrue(self.classify(text, ckb=self.ckb, plan=self.plan)["blocks_release"])

    def test_supported_tool_omitted_from_skills_is_advisory(self):
        for text in ("Technical Skills omits NebulaIndex, which appears in Work Experience.",
                     "NebulaIndex appears in Work Experience but is missing from Technical Skills.",
                     "The skills list does not include NebulaIndex already used in the body."):
            with self.subTest(text=text):
                finding = self.classify(text, ckb=self.ckb, plan=self.plan)
                self.assertEqual(finding["severity"], "advisory")
                self.assertFalse(finding["blocks_release"])

    def test_both_directions_in_one_finding_block(self):
        text = ("Technical Skills omits NebulaIndex, which appears in Work Experience; "
                "it lists AstralLedger, but AstralLedger is absent from the body and CKB.")
        self.assertTrue(self.classify(text, ckb=self.ckb, plan=self.plan)["blocks_release"])

    def test_mixed_direction_absence_variants_block(self):
        for absent in ("does not appear under", "does not appear", "not found in", "not found", "absent from"):
            for scope in ("Work Experience", "body", "CKB"):
                with self.subTest(absent=absent, scope=scope):
                    text = ("Technical Skills omits NebulaIndex, which appears in Work Experience; "
                            f"it contains AstralLedger, but AstralLedger {absent} {scope}.")
                    self.assertTrue(self.classify(text, ckb=self.ckb, plan=self.plan)["blocks_release"])

    def test_tool_name_is_not_shortened_and_ambiguous_tool_list_blocks(self):
        for tools in ("NebulaIndex.js", "NebulaIndex, AstralLedger"):
            with self.subTest(tools=tools):
                text = f"Technical Skills omits {tools}, which appears in the body."
                self.assertTrue(self.classify(text, ckb=self.ckb, plan=self.plan)["blocks_release"])
        ckb = [{**self.ckb[0], "source_text": "Used NebulaIndex.js."}]
        self.assertFalse(self.classify("Technical Skills omits NebulaIndex.js, which appears in the body.",
                                       ckb=ckb, plan=self.plan)["blocks_release"])

    def test_conflicting_skills_assertion_does_not_need_a_known_absence_phrase(self):
        for subject in ("Technical Skills", "it"):
            for absent in ("is not listed in", "is not mentioned in", "does not occur in"):
                with self.subTest(subject=subject, absent=absent):
                    text = ("Technical Skills omits NebulaIndex, which appears in the body; "
                            f"{subject} includes AstralLedger, but AstralLedger {absent} Work Experience.")
                    self.assertTrue(self.classify(text, ckb=self.ckb, plan=self.plan)["blocks_release"])

    def test_selected_source_requires_full_tool_name_boundaries(self):
        for source in ("Used NebulaIndex++.", "Used NebulaIndex.js.", "Used NebulaIndex#.", "Used NebulaIndex/Pro."):
            with self.subTest(source=source):
                ckb = [{**self.ckb[0], "source_text": source}]
                self.assertTrue(self.classify("Technical Skills omits NebulaIndex, which appears in the body.",
                                              ckb=ckb, plan=self.plan)["blocks_release"])

    def test_omission_requires_selected_source_when_ckb_is_supplied(self):
        text = "Technical Skills omits NebulaIndex, which appears in the body."
        for ckb, plan in (([], self.plan), (self.ckb, {"selected_evidence": []}),
                          ([{**self.ckb[0], "source_text": "Used AstralLedger."}], self.plan)):
            with self.subTest(ckb=ckb, plan=plan):
                self.assertTrue(self.classify(text, ckb=ckb, plan=plan)["blocks_release"])

    def test_unextractable_tool_stays_blocking(self):
        self.assertTrue(self.classify("Technical Skills omits a tool already used in the body.",
                                      ckb=self.ckb, plan=self.plan)["blocks_release"])
