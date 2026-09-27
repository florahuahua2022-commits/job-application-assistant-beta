import json
import unittest

from app.ckb import EVIDENCE_TYPES, build_career_knowledge_base, career_knowledge_base_is_current, stable_evidence_id, validate_career_knowledge_base
from app.experience_identity import source_fingerprint
from app.experience_identity import experience_candidate_id
from app.ingest import extract_resume_experiences


class CareerKnowledgeBaseTests(unittest.TestCase):
    def test_user_edited_responsibility_replaces_stale_source_fields_atomically(self):
        old_block = (
            "E-commerce Operations\nCore Color\n2022\n"
            "Processed supplier orders through a CRM system.\nPrepared promotion plans."
        )
        new_action = "Led digital marketing strategy, SEO research and an influencer network."
        ckb = build_career_knowledge_base(
            old_block.replace("Processed supplier orders through a CRM system.", new_action),
            json.dumps([{
                "role_title": "E-commerce Operations",
                "organization": "Core Color",
                "time_period_text": "2022",
                "responsibility": new_action,
                "source_text": old_block,
                "source_paragraph": old_block,
            }]),
        )

        self.assertEqual(len(ckb), 1)
        self.assertEqual(ckb[0]["action"], new_action)
        self.assertIn(new_action, ckb[0]["source_text"])
        self.assertIn(new_action, ckb[0]["source_paragraph"])
        self.assertNotIn("Processed supplier orders", ckb[0]["source_text"])
        self.assertNotIn("Processed supplier orders", ckb[0]["source_paragraph"])

    def test_multiline_edit_cannot_restore_old_source_paragraph_after_splitting(self):
        old_block = "E-commerce Operations\nCore Color\n2022\nProcessed supplier orders through a CRM system."
        actions = [
            "Led digital marketing strategy and SEO research across online channels.",
            "Developed brand content and advertising for product launches and events.",
            "Coordinated social media content schedules and membership programmes.",
            "Built and managed an influencer network and its budget.",
        ]
        ckb = build_career_knowledge_base("\n".join(actions), json.dumps([{
            "role_title": "E-commerce Operations",
            "organization": "Core Color",
            "time_period_text": "2022",
            "responsibility": "\n".join(actions),
            "source_text": old_block,
        }]))

        self.assertEqual([item["action"] for item in ckb], actions)
        self.assertTrue(all(item["action"] in item["source_paragraph"] for item in ckb))
        self.assertTrue(all("Processed supplier orders" not in item["source_paragraph"] for item in ckb))

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

        self.assertEqual(first["schema_version"], "2.1")
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertEqual(first["source_text"], source)
        self.assertEqual(first["time_period"], {"start": "January 2022", "end": "Present"})
        self.assertEqual(first["fact_verification"], "explicit")
        self.assertEqual(validate_career_knowledge_base([first]), [])

    def test_supports_all_frozen_evidence_types(self):
        expected = {"experience", "project", "volunteer", "education", "qualification", "award", "publication", "skill"}
        self.assertEqual(EVIDENCE_TYPES, expected)

    def test_currentness_uses_date_status_not_an_empty_period_heuristic(self):
        base = {"schema_version": "2.1", "evidence_type": "experience", "source_group_id": "role", "time_period": {"start": None, "end": None}}
        self.assertFalse(career_knowledge_base_is_current([base]))
        for status in ("verified", "uncertain", "not_provided"):
            with self.subTest(status=status):
                self.assertTrue(career_knowledge_base_is_current([{**base, "time_period_status": status}]))

    def test_currentness_rejects_half_updated_experience_evidence(self):
        self.assertFalse(career_knowledge_base_is_current([{
            "schema_version": "2.1",
            "evidence_type": "experience",
            "source_group_id": "core-color",
            "time_period_status": "verified",
            "time_period": {"start": "2022", "end": None},
            "source_text": "Processed supplier orders through a CRM system.",
            "source_paragraph": "Processed supplier orders through a CRM system.",
            "action": "Led digital marketing strategy and SEO research.",
        }]))

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
        self.assertTrue(all(item["schema_version"] == "2.1" for item in items))

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

    def test_excluded_experience_cannot_reenter_ckb_through_skills(self):
        source = """WORK EXPERIENCE
Core Color
E-commerce Operations
2022
Processed supplier orders through a CRM system.
Self-employed - Amazon e-commerce business
Self-employed e-commerce operator
2019 - 2022
Operated an independent Amazon e-commerce business.
SKILLS
Microsoft Office
E-commerce: Amazon e-commerce operations; CRM-based supplier order processing; marketing promotion plans
University of Adelaide research systems
Kenya regional stakeholder engagement
"""
        experiences = json.dumps([{
            "organization": "Core Color", "role_title": "E-commerce Operations",
            "time_period_text": "2022", "responsibility": "Processed supplier orders through a CRM system.",
        }])
        exclusions = json.dumps([{
            "status": "excluded_by_user", "organization": "Self-employed - Amazon e-commerce business",
            "role_title": "Self-employed e-commerce operator", "time_period_text": "2019 - 2022",
        }, {
            "status": "excluded_by_user", "organization": "University of Adelaide",
            "role_title": "Research Assistant", "time_period_text": "2018",
        }, {
            "status": "excluded_by_user", "organization": "China Communications Construction Company - Kenya Branch",
            "role_title": "Project Administrator", "time_period_text": "2016 - 2017",
        }])

        evidence = build_career_knowledge_base(source, experiences, exclusions)

        self.assertFalse(any("amazon" in item["source_text"].casefold() for item in evidence))
        self.assertFalse(any("university" in item["source_text"].casefold() for item in evidence))
        core_color = next(item for item in evidence if "Core Color" in item["source_section"])
        self.assertEqual(core_color["action"], "Processed supplier orders through a CRM system.")
        self.assertTrue(any(item["source_text"] == "Microsoft Office" for item in evidence))
        self.assertTrue(any(item["source_text"] == "Kenya regional stakeholder engagement" for item in evidence))

    def test_source_fingerprint_exclusion_survives_parsed_identity_drift(self):
        block = "Alpha Logistics Pty Ltd\nWarehouse Supervisor\nMarch 2019 - July 2021\nManaged dispatch."
        anchored_excerpt = "Alpha Logistics Pty Ltd\nWarehouse Supervisor\nMarch 2019 - July 2021"
        experiences = json.dumps([{
            "organization": "Alpha Logistics Pty Ltd", "role_title": "Warehouse Supervisor",
            "time_period_text": "March 2019 - July 2021", "responsibility": "Managed dispatch.",
            "source_text": block,
        }])
        exclusions = json.dumps([{
            "status": "excluded_by_user", "organization": "Warehouse Supervisor",
            "role_title": "Alpha Logistics Pty Ltd", "time_period_text": "March 2019 - July 2021",
            "anchor_version": "source_fingerprint_v1", "source_fingerprint": source_fingerprint(anchored_excerpt),
            "source_excerpt": anchored_excerpt,
        }])

        evidence = build_career_knowledge_base(block, experiences, exclusions)

        self.assertEqual(evidence, [])

    def test_parser_version_anchor_drift_matrix_keeps_source_exclusions_effective(self):
        dash = "–"
        samples = [
            f"Alpha Logistics Pty Ltd\nWarehouse Supervisor\nMarch 2019 {dash} July 2021\nManaged dispatch.",
            f"Senior Coordinator\nDepartment of Health and\nHuman Services, Victoria\n2015 {dash} 2018\nCoordinated reporting.",
            f"Retail Assistant\nBunnings Warehouse\nJoondalup, WA\nJan 2020 {dash} Dec 2020\nAssisted customers.",
            f"Office Administrator, Chevron Australia Pty Ltd | Feb 2016 {dash} Aug 2019\nProvided support.",
            f"Finance Officer {dash} Woolworths Group | 2021 {dash} 2023\nReconciled reports.",
            f"Independent Consultant\n2022 {dash} Present\nDelivered freelance services.",
            f"Sichuan Trading Company\n2013 {dash} 2015\nHandled documentation.",
            f"2017 {dash} 2019\nProject Engineer\nGlobal Construction\nSolutions Ltd\nOversaw logistics.",
        ]
        expected_drift = [False, True, True, True, True, False, True, True]

        def legacy_anchor(source):
            lines = source.splitlines()
            date_index = next(i for i, line in enumerate(lines) if any(char.isdigit() for char in line) and dash in line)
            period = lines[date_index]
            previous = lines[max(0, date_index - 2):date_index]
            if len(previous) == 2:
                first, second = previous
                role_words = ("officer", "assistant", "administrator", "coordinator", "manager", "consultant", "engineer")
                first_role = any(word in first.casefold() for word in role_words)
                second_role = any(word in second.casefold() for word in role_words)
                if first_role != second_role:
                    return (first, second, period) if first_role else (second, first, period)
                company_words = ("pty", "ltd", "company", "department", "university", "group")
                first_company = any(word in first.casefold() for word in company_words)
                second_company = any(word in second.casefold() for word in company_words)
                if first_company != second_company:
                    return (second, first, period) if first_company else (first, second, period)
                return first, second, period
            if previous:
                return previous[-1], "", period
            return "", "", period

        for index, (source, drift_expected) in enumerate(zip(samples, expected_drift), start=1):
            with self.subTest(sample=index):
                new_item = extract_resume_experiences("Work Experience\n" + source + "\nEducation")[0]
                old_role, old_org, old_period = legacy_anchor(source)
                old_id = experience_candidate_id(old_org, old_role, old_period)
                new_id = experience_candidate_id(
                    new_item["organization"], new_item["role_title"], new_item["time_period_text"]
                )
                self.assertEqual(old_id != new_id, drift_expected)
                source_exclusion = json.dumps([{
                    "status": "excluded_by_user", "organization": old_org, "role_title": old_role,
                    "time_period_text": old_period, "anchor_version": "source_fingerprint_v1",
                    "source_fingerprint": source_fingerprint(new_item["source_text"]),
                    "source_excerpt": new_item["source_text"], "source_occurrence": 1,
                }])
                self.assertEqual(build_career_knowledge_base(source, json.dumps([new_item]), source_exclusion), [])

    def test_validation_rejects_unverified_or_unknown_evidence(self):
        item = {
            "schema_version": "1.0", "evidence_id": stable_evidence_id("experience", "Evidence"),
            "evidence_type": "invented_type", "source_section": "Skills", "source_text": "Evidence",
            "time_period": {"start": None, "end": None}, "evidence_quality": "low",
            "fact_verification": "inferred",
        }

        errors = validate_career_knowledge_base([item])

        self.assertTrue(any("unsupported evidence_type" in error for error in errors))
        self.assertTrue(any("not explicitly verified" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
