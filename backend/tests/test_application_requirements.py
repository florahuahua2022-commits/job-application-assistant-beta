import copy
import json
from pathlib import Path
import unittest

from app.application_requirements import (
    empty_application_requirements,
    legacy_application_requirements,
    load_application_requirements,
    normalise_requirements_source,
    parse_application_requirements,
    requirements_source_changed,
    validate_application_requirements,
    correct_application_requirements,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "application_requirements" / "cases.json"


class ApplicationRequirementsParserTests(unittest.TestCase):
    def test_document_basis_distinguishes_employer_text_user_choice_and_legacy_default(self):
        explicit = parse_application_requirements("Submit your CV and cover letter.")
        self.assertEqual(explicit["documents"]["resume"]["basis"], "employer_explicit")
        ambiguous = parse_application_requirements("Join our administration team.")
        documents = copy.deepcopy(ambiguous["documents"])
        documents["resume"].update(requirement="required", format="standalone")
        documents["cover_letter"].update(requirement="required", format="standalone")
        documents["selection_criteria"].update(requirement="not_required", format="not_applicable")
        corrected = correct_application_requirements(ambiguous, documents, [])
        self.assertEqual(corrected["documents"]["resume"]["basis"], "user_confirmed")
        self.assertNotIn("Submission document requirements could not be determined from the supplied text.", corrected["warnings"])

    def test_legacy_confirmed_material_unknown_loads_as_unresolved(self):
        contradictory = empty_application_requirements("Ambiguous")
        contradictory["review_status"] = "confirmed"
        loaded = load_application_requirements(json.dumps(contradictory))
        self.assertEqual(loaded["review_status"], "needs_confirmation")

    def test_fixture_matrix(self):
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                result = parse_application_requirements(case["text"])
                expected = case["expected"]
                documents = result["documents"]
                self.assertEqual(result["review_status"], "needs_confirmation")
                self.assertEqual(result["source"], "deterministic_parser")
                self.assertEqual(result["source_text"], case["text"])
                self.assertEqual(validate_application_requirements(result), [])
                if "resume" in expected:
                    self.assertEqual(documents["resume"]["requirement"], expected["resume"])
                if "cover_letter" in expected:
                    self.assertEqual(documents["cover_letter"]["requirement"], expected["cover_letter"])
                if "selection_requirement" in expected:
                    self.assertEqual(documents["selection_criteria"]["requirement"], expected["selection_requirement"])
                if "selection_format" in expected:
                    self.assertEqual(documents["selection_criteria"]["format"], expected["selection_format"])
                if "criteria_count" in expected:
                    self.assertEqual(documents["selection_criteria"]["criteria_count"], expected["criteria_count"])
                if "limit_unit" in expected:
                    limit = documents["selection_criteria"]["limit"]
                    self.assertEqual((limit["unit"], limit["scope"], limit["value"]), (expected["limit_unit"], expected["limit_scope"], expected["limit_value"]))
                if "cover_limit_unit" in expected:
                    limit = documents["cover_letter"]["limit"]
                    self.assertEqual((limit["unit"], limit["value"]), (expected["cover_limit_unit"], expected["cover_limit_value"]))
                if expected.get("warning"):
                    self.assertTrue(result["warnings"])

    def test_criteria_text_alone_does_not_require_standalone_document(self):
        result = parse_application_requirements("Selection criteria\n1. Communication skills.\n2. Project delivery experience.")
        self.assertEqual(result["documents"]["selection_criteria"]["requirement"], "unknown")
        self.assertNotEqual(result["documents"]["selection_criteria"]["format"], "standalone")

    def test_page_limit_is_preserved_without_word_conversion(self):
        result = parse_application_requirements("Submit a cover letter with a maximum 2 pages.")
        limit = result["documents"]["cover_letter"]["limit"]
        self.assertEqual(limit, {"value": 2, "unit": "pages", "scope": "document", "constraint": "maximum", "source_text": "maximum 2 pages"})

    def test_lgirs_embedded_criteria_and_hyphenated_limit(self):
        result = parse_application_requirements(
            "Submit your CV and a maximum two-page cover letter addressing the following three criteria.\n"
            "No separate Selection Criteria attachment is required."
        )
        documents = result["documents"]
        self.assertEqual(documents["resume"]["requirement"], "required")
        self.assertEqual((documents["cover_letter"]["requirement"], documents["cover_letter"]["limit"]["value"]), ("required", 2))
        self.assertEqual((documents["selection_criteria"]["requirement"], documents["selection_criteria"]["format"], documents["selection_criteria"]["criteria_count"]), ("not_required", "embedded_in_cover_letter", 3))

    def test_hyphenated_numeric_and_word_page_limits(self):
        for wording, value in (("maximum one-page cover letter", 1), ("maximum two-page cover letter", 2), ("maximum 1-page cover letter", 1), ("maximum 2-page cover letter", 2)):
            with self.subTest(wording=wording):
                limit = parse_application_requirements(f"Submit a {wording}.")["documents"]["cover_letter"]["limit"]
                self.assertEqual((limit["value"], limit["unit"], limit["scope"], limit["constraint"]), (value, "pages", "document", "maximum"))
                self.assertEqual(limit["source_text"], wording.removesuffix(" cover letter"))

    def test_wa_police_standalone_criteria_and_one_page_response(self):
        result = parse_application_requirements(
            "Submit a separate Selection Criteria document addressing the following two criteria. Maximum one-page response."
        )
        selection = result["documents"]["selection_criteria"]
        self.assertEqual((selection["requirement"], selection["format"], selection["criteria_count"]), ("required", "standalone", 2))
        self.assertEqual((selection["limit"]["value"], selection["limit"]["unit"]), (1, "pages"))

    def test_numbered_materials_are_required_but_not_criteria_count(self):
        result = parse_application_requirements(
            "Please provide:\n"
            "1. A comprehensive CV with two work related referees.\n"
            "2. A Cover Letter addressing selection criteria 1, 2 and 3 as highlighted in the attached JDF."
        )
        documents = result["documents"]
        self.assertEqual((documents["resume"]["requirement"], documents["cover_letter"]["requirement"]), ("required", "required"))
        self.assertEqual(documents["selection_criteria"]["criteria_references"], ["1", "2", "3"])
        self.assertEqual(documents["selection_criteria"]["criteria_count"], 3)

    def test_reference_count_uses_exact_nonconsecutive_and_range_values(self):
        for wording, expected in (("criteria 1, 3 and 5", ["1", "3", "5"]), ("criteria 2-4", ["2", "3", "4"]), ("criterion 4", ["4"])):
            with self.subTest(wording=wording):
                selection = parse_application_requirements(f"Address {wording} in the attached JDF.")["documents"]["selection_criteria"]
                self.assertEqual(selection["criteria_references"], expected)
                self.assertEqual(selection["criteria_count"], len(expected))

    def test_private_requirements_and_ambiguous_instructions_remain_safe(self):
        private = parse_application_requirements("The role requires project delivery experience, communication skills and commercial judgement.")
        ambiguous = parse_application_requirements("Application instructions will be provided later.")
        self.assertEqual(private["documents"]["selection_criteria"]["requirement"], "unknown")
        self.assertEqual(ambiguous["review_status"], "needs_confirmation")
        self.assertTrue(ambiguous["warnings"])


class ApplicationRequirementsValidationTests(unittest.TestCase):
    def test_unknown_values_are_valid_and_not_guessed(self):
        self.assertEqual(validate_application_requirements(empty_application_requirements()), [])

    def test_rejects_invalid_enums_limits_counts_and_contradictions(self):
        cases = []
        invalid_enum = empty_application_requirements()
        invalid_enum["documents"]["resume"]["requirement"] = "mandatory"
        cases.append(invalid_enum)
        invalid_limit = empty_application_requirements()
        invalid_limit["documents"]["cover_letter"]["limit"] = {"value": 0, "unit": "lines", "scope": "item", "constraint": "hard"}
        cases.append(invalid_limit)
        invalid_count = empty_application_requirements()
        invalid_count["documents"]["selection_criteria"]["criteria_count"] = -1
        cases.append(invalid_count)
        contradictory = empty_application_requirements()
        contradictory["documents"]["selection_criteria"].update(requirement="not_required", format="standalone")
        cases.append(contradictory)
        embedded_without_cover = empty_application_requirements()
        embedded_without_cover["documents"]["cover_letter"].update(requirement="not_required", format="not_applicable")
        embedded_without_cover["documents"]["selection_criteria"].update(requirement="not_required", format="embedded_in_cover_letter")
        cases.append(embedded_without_cover)
        for model in cases:
            with self.subTest(model=model):
                self.assertTrue(validate_application_requirements(model))

    def test_rejects_unknown_schema_version(self):
        model = empty_application_requirements()
        model["schema_version"] = "99.0"
        self.assertIn("Unsupported Application Requirements schema version.", validate_application_requirements(model))

    def test_rejects_invalid_metadata_lists_sources_and_document_specific_formats(self):
        cases = []
        invalid_metadata = empty_application_requirements()
        invalid_metadata.update(source=None, source_text=[], source_excerpt=1, warnings="warning", additional_documents=[1])
        cases.append(invalid_metadata)
        invalid_resume_format = empty_application_requirements()
        invalid_resume_format["documents"]["resume"]["format"] = "embedded_in_cover_letter"
        cases.append(invalid_resume_format)
        invalid_cover_format = empty_application_requirements()
        invalid_cover_format["documents"]["cover_letter"]["format"] = "embedded_in_resume"
        cases.append(invalid_cover_format)
        embedded_without_resume = empty_application_requirements()
        embedded_without_resume["documents"]["resume"].update(requirement="not_required", format="not_applicable")
        embedded_without_resume["documents"]["selection_criteria"].update(requirement="not_required", format="embedded_in_resume")
        cases.append(embedded_without_resume)
        missing_limit_source = empty_application_requirements()
        missing_limit_source["documents"]["cover_letter"]["limit"] = {
            "value": 2, "unit": "pages", "scope": "document", "constraint": "maximum"
        }
        cases.append(missing_limit_source)
        unsupported_nested_fields = empty_application_requirements()
        unsupported_nested_fields["documents"]["resume"]["source"] = "client supplied"
        unsupported_nested_fields["documents"]["cover_letter"]["limit"] = {
            "value": 2, "unit": "pages", "scope": "document", "constraint": "maximum",
            "source_text": "two pages", "derived_words": 900,
        }
        unsupported_nested_fields["documents"]["unknown_document"] = {}
        cases.append(unsupported_nested_fields)
        for model in cases:
            with self.subTest(model=model):
                self.assertTrue(validate_application_requirements(model))

    def test_material_comparison_ignores_only_whitespace_and_line_endings(self):
        self.assertEqual(normalise_requirements_source(" A\r\n  B "), "A B")
        self.assertFalse(requirements_source_changed("A\nB", " Criterion ", " A  B ", "Criterion"))
        self.assertTrue(requirements_source_changed("A B", None, "A changed B", None))


class LegacyApplicationRequirementsTests(unittest.TestCase):
    def test_legacy_application_with_criteria_matches_old_pack_without_confirmation(self):
        result = legacy_application_requirements("1. Communication\n2. Planning")
        self.assertEqual(result["source"], "legacy_inference")
        self.assertEqual(result["review_status"], "needs_confirmation")
        self.assertEqual(result["documents"]["resume"]["requirement"], "required")
        self.assertEqual(result["documents"]["cover_letter"]["requirement"], "required")
        self.assertEqual(result["documents"]["selection_criteria"]["requirement"], "required")
        self.assertTrue(result["warnings"])

    def test_malformed_or_invalid_json_safely_uses_legacy_fallback(self):
        malformed = load_application_requirements("{broken", None)
        invalid = copy.deepcopy(empty_application_requirements())
        invalid["review_status"] = "approved"
        recovered = load_application_requirements(json.dumps(invalid), "Criterion")
        self.assertEqual(malformed["source"], "legacy_inference")
        self.assertEqual(malformed["documents"]["selection_criteria"]["requirement"], "not_required")
        self.assertEqual(recovered["source"], "legacy_inference")
        self.assertEqual(recovered["documents"]["selection_criteria"]["requirement"], "required")


if __name__ == "__main__":
    unittest.main()
