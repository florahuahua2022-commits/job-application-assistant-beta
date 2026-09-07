import json
import unittest
from io import BytesIO
from unittest.mock import patch

from docx import Document
from pypdf import PdfReader
from pydantic import ValidationError

from app.applicant_profile import availability_issues, polish_availability, AVAILABILITY_WORDING
from app.ats_verification import verify_document_export
from app.career_modern import TOKENS
from app.ckb import build_career_knowledge_base
from app.cover_letter_plan import build_cover_letter_plan
from app.exporter import create_docx, create_pdf
from app.job_model import build_job_model, match_advertised_tags
from app.ingest import extract_resume_experiences
from app.main import auto_polish_tailored_resume
from app.models import ApplicantProfilePayload
from app.resume_plan import build_resume_curation_plan, evaluate_resume_quality


SAMPLE = """Alex Morgan
Perth WA | alex@example.com | 0400000000

## Professional Summary
Administrative officer supporting payroll records and stakeholder correspondence.
## Key Skills
- Records coordination
## Work Experience
### Finance Officer
Example Department with a deliberately long organisational name | Jan 2024 - Present
- Prepared payroll records using Dayforce for 30 staff.
- Coordinated correspondence with 12 suppliers.
## Certifications
## Technical Skills
- Dayforce
"""


class QualityV3Tests(unittest.TestCase):
    def test_year_and_iso_ranges_keep_dated_duties(self):
        for period in ["2019 - 2026", "2019-01 - 2026-06", "2019-01-01 - 2026-06-30"]:
            source = f"Work Experience\nFinance Officer\nExample Agency\n{period}\nPrepared 2023-2024 budget reports.\nMaintained CRM records."
            parsed = extract_resume_experiences(source)
            self.assertEqual(len(parsed), 1)
            ckb = build_career_knowledge_base(source, json.dumps(parsed))
            self.assertEqual(len(ckb), 2)
            self.assertIn("2023-2024 budget reports", ckb[0]["action"])

    def test_all_availability_values_and_missing_profile(self):
        for value, wording in AVAILABILITY_WORDING.items():
            if value == "not_specified":
                continue
            with self.subTest(value=value):
                self.assertEqual(availability_issues(wording + ".", value), [])
                self.assertEqual(availability_issues(wording + ".")[0]["type"], "unsupported_availability_claim")
                self.assertNotIn(wording, polish_availability(wording + "."))
                wrong = "one_month" if value != "one_month" else "two_weeks"
                self.assertEqual(availability_issues(wording + ".", wrong)[0]["type"], "availability_conflict")

    def test_start_date_synonyms_and_unrelated_dates(self):
        self.assertTrue(availability_issues("I am available immediately following one month's notice.", "immediate"))
        self.assertTrue(availability_issues("I am not available immediately.", "immediate"))
        for claim in ["I can start on 7 September 2026.", "I am available to commence promptly.",
                      "My notice period is two weeks.", "My start date is negotiable."]:
            with self.subTest(claim=claim):
                self.assertTrue(availability_issues(claim))
                self.assertEqual(polish_availability(claim).strip(), "")
        for nonclaim in ["Jan 2020 - Dec 2024", "References available upon request.", "I am available for an interview.", "Coordinated rosters, confirming availability with participants."]:
            self.assertEqual(availability_issues(nonclaim), [])

    def test_profile_enum_rejects_unknown_value(self):
        with self.assertRaises(ValidationError):
            ApplicantProfilePayload(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com", availability_notice="tomorrow")

    def test_role_group_keeps_all_duties_and_sources(self):
        source = "Officer\nAgency\nJan 2024 - Present\nPrepared Dayforce payroll.\nCoordinated 12 supplier accounts.\nManaged CRM contact records."
        ckb = build_career_knowledge_base(source, json.dumps([{"role_title": "Officer", "organization": "Agency", "source_text": source}]))
        self.assertEqual(len(ckb), 3)
        self.assertEqual(len({item["source_group_id"] for item in ckb}), 1)
        self.assertTrue(all(item["source_paragraph"] == source for item in ckb))
        matches = {"matches": [{"criteria_id": "C", "match_type": "direct", "matched_evidence": [item["evidence_id"] for item in ckb]}]}
        plan = build_resume_curation_plan({"criteria": []}, matches, ckb)
        self.assertEqual(len(plan["source_groups"]), 1)
        self.assertEqual(evaluate_resume_quality("Short but specific.", plan)["status"], "pass")

    def test_word_threshold_never_changes_quality(self):
        plan = {"target_words": 650, "selected_evidence": [{"evidence_type": "experience", "evidence_thin": True}]}
        self.assertEqual({evaluate_resume_quality("word " * n, plan)["status"] for n in [451, 454, 455, 459, 650]}, {"pass"})

    def test_generic_source_blocks_even_if_output_is_long(self):
        plan = {"source_groups": [{"source_section": "Officer at Agency", "source_detail": "Responsible for daily administrative work."}]}
        for content in ["Short CV.", "word " * 650]:
            finding = evaluate_resume_quality(content, plan)["issues"][0]
            self.assertEqual(finding["type"], "insufficient_source_detail")
            self.assertTrue(finding["blocks_release"])

    def test_cross_role_repetition_requires_unused_distinct_facts(self):
        groups = [{"source_section": f"Work > Agency {i} > Officer {i}", "source_detail": detail} for i, detail in enumerate([
            "Reconciled Dayforce payroll for thirty overseas employees.", "Scheduled twelve supplier inspections across regional schools.", "Maintained CRM registrations for community volunteering programmes."
        ])]
        content = "## Work Experience\n" + "\n".join(f"### Officer {i}\nAgency {i}\n- Provided administrative support for team operations." for i in range(3))
        issues = evaluate_resume_quality(content, {"source_groups": groups})["issues"]
        self.assertIn("generation_under_utilized", [issue["type"] for issue in issues])
        distinct = "## Work Experience\n" + "\n".join(f"### Officer {i}\nAgency {i}\n- Provided {g['source_detail']}" for i, g in enumerate(groups))
        self.assertFalse(any(issue.get("blocks_release") for issue in evaluate_resume_quality(distinct, {"source_groups": groups})["issues"]))

    def test_recruiter_identity_does_not_invent_government_department(self):
        model = build_job_model("Advertiser: Randstad\nRandstad is recruiting on behalf of several government departments.\nSkills: reporting", company="WA gov")
        identity = model["job_identity"]
        self.assertEqual(identity["organisation_display_name"], "Randstad")
        self.assertEqual(identity["hiring_organisation"], "")
        self.assertEqual(len(identity["name_candidates"]), 2)
        self.assertIn("several government departments", identity["recruitment_relationship"])

    def test_skill_tags_do_not_upgrade_vendor_coordination(self):
        model = build_job_model("Skill tags: Project Delivery, Project Management, Contract Management, Data Analysis")
        tags = model["advertised_skill_tags"]
        self.assertEqual(len(tags), 4)
        matches = {"matches": [{"criteria_id": tag["criteria_id"], "match_type": "direct", "matched_evidence": ["E"]} for tag in tags]}
        mapped = match_advertised_tags(model, matches, [{"evidence_id": "E", "source_text": "Coordinated vendor correspondence."}])
        contract = next(item for item in mapped if item["text"] == "Contract Management")
        self.assertEqual(contract["match_type"], "adjacent")
        self.assertFalse(contract["allowed_in_key_skills"])

    def test_latest_direct_case_compared_and_complementary_cases_selected(self):
        model = {"criteria": [{"criteria_id": f"C{i}", "criteria_text": f"Requirement {i}", "criteria_type": "essential"} for i in range(3)]}
        ckb = [{"evidence_id": f"E{i}", "source_section": f"Work > Role {i}", "source_text": f"Prepared evidence {i}", "time_period": {"end": "Present" if i == 0 else "2020"}} for i in range(4)]
        matches = {"matches": [{"criteria_id": f"C{i}", "match_type": "direct", "matched_evidence": [f"E{i}"] + (["E3"] if i == 0 else [])} for i in range(3)]}
        plan = build_cover_letter_plan(model, matches, ckb)
        self.assertEqual([item["evidence_id"] for item in plan["selected_evidence"]], ["E0", "E1", "E2"])
        self.assertEqual(len(plan["candidate_comparison"]), 4)
        self.assertTrue(all(item["reason"] for item in plan["candidate_comparison"]))

    def test_skills_deduplicate_and_references_default_off(self):
        text = "## Key Skills\n- Excel\n- Records management\n## Technical Skills\n- EXCEL\n- Records Management System\n## References\nAvailable upon request"
        output = auto_polish_tailored_resume(text)
        self.assertEqual(output.casefold().count("excel"), 1)
        self.assertIn("Records Management System", output)
        self.assertNotIn("References", output)
        self.assertIn("References", auto_polish_tailored_resume(text, include_references=True))

    def test_career_modern_formats_preserve_text_and_empty_sections_removed(self):
        texts = []
        for format, create in [("docx", create_docx), ("pdf", create_pdf)]:
            payload = create(SAMPLE, "Tailored Resume", "career_modern")
            self.assertTrue(verify_document_export(SAMPLE, payload, format)["ready"])
            if format == "docx":
                doc = Document(BytesIO(payload))
                text = "\n".join(p.text for p in doc.paragraphs)
                self.assertAlmostEqual(doc.sections[0].page_width.pt, 595.3, delta=.1)
                self.assertEqual(doc.core_properties.comments, "Template tokens: " + TOKENS["version"])
            else:
                pdf = PdfReader(BytesIO(payload))
                text = "\n".join(p.extract_text() for p in pdf.pages)
                self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), 595.3, delta=.1)
            self.assertNotIn("CERTIFICATIONS", text)
            texts.append(text)
        for token in ["Alex Morgan", "Finance Officer", "Jan 2024 - Present", "Dayforce", "12 suppliers"]:
            self.assertTrue(all(token in text for text in texts))
        self.assertTrue(all("WORK EXPERIENCE" in text for text in texts))

    def test_export_detects_missing_content(self):
        payload = create_pdf(SAMPLE.replace("12 suppliers", "suppliers"), "Tailored Resume", "career_modern")
        self.assertFalse(verify_document_export(SAMPLE, payload, "pdf")["ready"])
        reordered = create_pdf("Alex Morgan\n- Second duty\n- First duty", "Tailored Resume", "career_modern")
        self.assertFalse(verify_document_export("Alex Morgan\n- First duty\n- Second duty", reordered, "pdf")["text_order_preserved"])

    def test_export_handles_missing_font_and_long_metadata(self):
        with patch("app.career_modern.Path.exists", return_value=False):
            payload = create_pdf(SAMPLE, "Tailored Resume", "career_modern")
        self.assertTrue(verify_document_export(SAMPLE, payload, "pdf")["ready"])


if __name__ == "__main__":
    unittest.main()
