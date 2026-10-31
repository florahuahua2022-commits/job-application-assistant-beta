"""Entirely invented tool names and prose for clause-level review regressions."""
import unittest

from app.reviewer_core import normalise_document_review, normalise_finding, normalise_saved_review


class SkillsClauseReviewTests(unittest.TestCase):
    def setUp(self):
        self.ckb = [
            {"evidence_id": "SYN_B", "source_text": "Used PrismArchive for paper-moon bookings."},
            {"evidence_id": "SYN_C", "source_text": "Used CometShelf for cardboard telescope logs."},
        ]
        self.plan = {"selected_evidence": [{"evidence_id": "SYN_B"}, {"evidence_id": "SYN_C"}]}

    def classify(self, text, ckb=None):
        return normalise_finding({"type": "internal_inconsistency", "description": text},
                                 ckb=self.ckb if ckb is None else ckb, plan=self.plan)

    def test_existing_list_description_and_supported_omission_is_advisory(self):
        for qualifier in ("only", "just", "solely"):
            with self.subTest(qualifier=qualifier):
                text = (f"Technical Skills lists {qualifier} OrbitCanvas and omits PrismArchive and "
                        "records systems, which are supported by CKB evidence.")
                result = self.classify(text)
                self.assertEqual(result["severity"], "advisory")
                self.assertFalse(result["blocks_release"])

    def test_same_structure_with_unselected_tool_blocks(self):
        text = ("Technical Skills lists only OrbitCanvas and omits PrismArchive and records systems, "
                "which are supported by CKB evidence.")
        self.assertTrue(self.classify(text, ckb=[self.ckb[1]])["blocks_release"])

    def test_later_reverse_clause_blocks_even_with_lists_only(self):
        text = ("Technical Skills lists only OrbitCanvas and omits PrismArchive, and also lists "
                "VoidConsole which does not appear in Work Experience.")
        self.assertTrue(self.classify(text)["blocks_release"])

    def test_each_omitted_proper_name_requires_its_own_selected_support(self):
        text = ("Technical Skills lists only OrbitCanvas and omits PrismArchive and CometShelf and "
                "records systems, which are supported by CKB evidence.")
        self.assertFalse(self.classify(text)["blocks_release"])
        self.assertTrue(self.classify(text, ckb=[self.ckb[0]])["blocks_release"])
        other = [{**self.ckb[1], "evidence_id": "NOT_SELECTED"}, self.ckb[0]]
        self.assertTrue(self.classify(text, ckb=other)["blocks_release"])

    def test_no_ckb_is_blocking_for_resume_and_other_document_types(self):
        issue = {"type": "internal_inconsistency", "description": "Technical Skills omits PrismArchive."}
        self.assertTrue(normalise_finding(issue, ckb=None, plan=self.plan)["blocks_release"])
        for document_type in ("tailored_resume", "cover_letter", "selection_criteria"):
            with self.subTest(document_type=document_type):
                result = normalise_document_review({"issues": [issue]}, document_type, ckb=None, plan=self.plan)
                self.assertEqual(result["status"], "fail")

    def test_reverse_vocabulary_still_blocks(self):
        for verb in ("lists", "includes", "contains", "claims"):
            for negative in ("is not found in", "is absent from", "is not in"):
                for scope in ("Work Experience", "body", "CKB", "source"):
                    with self.subTest(verb=verb, negative=negative, scope=scope):
                        text = f"Technical Skills {verb} VoidConsole, which {negative} {scope}."
                        self.assertTrue(self.classify(text)["blocks_release"])

    def test_saved_supported_omission_with_missing_context_is_pending_warning(self):
        review = normalise_document_review({"issues": [{"type": "internal_inconsistency",
            "description": "Technical Skills omits PrismArchive, which is supported by evidence."}]},
            "tailored_resume", ckb=self.ckb, plan=self.plan)
        self.assertEqual(review["status"], "pass")
        result = normalise_saved_review(review)
        issue = result["results"][0]["issues"][0]
        self.assertTrue(issue["blocks_release"])
        self.assertEqual(issue["display_level"], "warning")
        self.assertEqual(result["factual_status"], "pending")
        self.assertEqual(result["quality_status"], "pending")

    def test_reverse_claim_in_a_following_sentence_or_semicolon_still_blocks(self):
        for separator in (". ", "; "):
            with self.subTest(separator=separator):
                text = ("Technical Skills omits PrismArchive" + separator +
                        "It also lists VoidConsole" + separator +
                        "VoidConsole does not appear in Work Experience.")
                self.assertTrue(self.classify(text)["blocks_release"])

    def test_mixed_case_tool_is_not_treated_as_generic_category(self):
        text = "Technical Skills omits PrismArchive and meteorShelf, which are supported by CKB evidence."
        self.assertTrue(self.classify(text)["blocks_release"])
        supported = self.ckb + [{"evidence_id": "SYN_B", "source_text": "Used meteorShelf."}]
        self.assertFalse(self.classify(text, ckb=supported)["blocks_release"])

    def test_omission_subject_must_be_the_skills_section(self):
        for text in ("Technical Skills lists PrismArchive, but Work Experience omits PrismArchive.",
                     "Technical Skills lists only OrbitCanvas; the body does not include PrismArchive.",
                     "Technical Skills lists OrbitCanvas; Work Experience lists CometShelf and omits PrismArchive."):
            with self.subTest(text=text):
                self.assertTrue(self.classify(text)["blocks_release"])

    def test_old_unverified_flag_cannot_bypass_required_tool_source(self):
        for kind in ("internal_inconsistency", "generation_under_utilized"):
            with self.subTest(kind=kind):
                review = {"status": "pass", "results": [{"issues": [{"type": kind,
                    "description": "Technical Skills omits PrismArchive.", "grounding_status": "unverified"}]}]}
                result = normalise_saved_review(review, ckb=None, plan=self.plan)
                self.assertEqual(result["status"], "fail")
                self.assertTrue(result["results"][0]["issues"][0]["blocks_release"])
