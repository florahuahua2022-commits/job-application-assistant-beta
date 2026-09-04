import json
import unittest

from app.ckb import EVIDENCE_TYPES, build_career_knowledge_base, career_knowledge_base_is_current, stable_evidence_id, validate_career_knowledge_base


class CareerKnowledgeBaseTests(unittest.TestCase):
    def test_builds_versioned_experience_with_stable_provenance(self):
        source = "Project Officer\nExample Agency\nJanuary 2022 - Present\nPrepared monthly reports."
        experiences = json.dumps([{
            "role_title": "Project Officer",
            "organization": "Example Agency",
            "responsibility": "Prepared monthly reports.",
            "context": "Employment dates: January 2022 - Present",
            "source_text": source,
            "time_period_text": "January 2022 - Present",
        }])

        first = build_career_knowledge_base(source, experiences)[0]
        second = build_career_knowledge_base(source, experiences)[0]

        self.assertEqual(first["schema_version"], "1.1")
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertEqual(first["source_text"], source)
        self.assertEqual(first["time_period"], {"start": "January 2022", "end": "Present"})
        self.assertEqual(first["fact_verification"], "explicit")
        self.assertEqual(validate_career_knowledge_base([first]), [])

    def test_supports_all_frozen_evidence_types(self):
        expected = {"experience", "project", "volunteer", "education", "qualification", "award", "publication"}
        self.assertEqual(EVIDENCE_TYPES, expected)

    def test_currentness_uses_date_status_not_an_empty_period_heuristic(self):
        base = {"schema_version": "1.1", "evidence_type": "experience", "time_period": {"start": None, "end": None}}
        self.assertFalse(career_knowledge_base_is_current([base]))
        for status in ("verified", "uncertain", "not_provided"):
            with self.subTest(status=status):
                self.assertTrue(career_knowledge_base_is_current([{**base, "time_period_status": status}]))

    def test_coarse_old_schema_is_refreshed_and_multiline_role_duties_stay_atomic(self):
        source = """Project Officer
Example Agency
January 2022 - Present
Prepared monthly project reports.
Coordinated meetings with external stakeholders.
Maintained the project risk register.
"""
        self.assertFalse(career_knowledge_base_is_current([{
            "schema_version": "1.0", "evidence_type": "experience", "time_period_status": "verified",
            "source_text": source, "role_title": "Project Officer", "organization": "Example Agency",
            "action": "Prepared reports, coordinated meetings and maintained the risk register.",
        }]))
        items = build_career_knowledge_base(source, json.dumps([{
            "role_title": "Project Officer", "organization": "Example Agency",
            "responsibility": "Prepared reports; coordinated meetings; maintained the risk register.",
            "source_text": source, "time_period_text": "January 2022 - Present",
        }]))

        self.assertEqual(len(items), 3)
        self.assertEqual(len({item["evidence_id"] for item in items}), 3)
        self.assertTrue(all(item["schema_version"] == "1.1" for item in items))

    def test_extracts_non_employment_detail_evidence(self):
        source = """Education
Bachelor of Business, Example University, 2021
Qualifications
Certificate IV in Project Management Practice, 2022
Awards
Employee Recognition Award, 2023
Publications
Project Delivery Review, 2024
"""

        evidence = build_career_knowledge_base(source)

        self.assertEqual(
            {item["evidence_type"] for item in evidence},
            {"education", "qualification", "award", "publication"},
        )
        self.assertTrue(all(item["detail"] for item in evidence))
        self.assertTrue(all(item["source_text"] in source for item in evidence))

    def test_validation_rejects_unverified_or_unknown_evidence(self):
        item = {
            "schema_version": "1.0", "evidence_id": stable_evidence_id("experience", "Evidence"),
            "evidence_type": "skill", "source_section": "Skills", "source_text": "Evidence",
            "time_period": {"start": None, "end": None}, "evidence_quality": "low",
            "fact_verification": "inferred",
        }

        errors = validate_career_knowledge_base([item])

        self.assertTrue(any("unsupported evidence_type" in error for error in errors))
        self.assertTrue(any("not explicitly verified" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
