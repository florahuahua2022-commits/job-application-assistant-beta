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
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "application_requirements" / "cases.json"


class ApplicationRequirementsParserTests(unittest.TestCase):
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
