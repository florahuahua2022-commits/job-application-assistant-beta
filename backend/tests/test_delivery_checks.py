import unittest
from datetime import date
from types import SimpleNamespace

from app.applicant_profile import availability_issues
from app.ats_verification import extract_artifact, verify_document_export
from app.delivery_checks import aggregate_experience, delivery_issues, profile_missing_fields
from app.exporter import create_docx, create_pdf
from app.job_model import build_job_model
from app.release_state import details_fingerprint
from app.selection_logic import hard_validate_response
from app.source_aware_parsing import _numbered_criteria


class DeliveryChecksTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(first_name="Flora", last_name="Zhong", email="flora@example.com", phone="0400123456")

    def test_five_wrapped_criteria_keep_boundaries_in_both_entries(self):
        text = "Essential Criteria\nApplicants must address the following criteria.\n" + "\n".join(
            f"{i}. Demonstrated ability to coordinate area {i}\nand prepare written reports." for i in range(1, 6))
        text += "\nDesirable Criteria\n1. A relevant qualification.\nHow to apply\nSubmit a CV."
        attached = _numbered_criteria(text)
        self.assertEqual(len(attached), 5)
        model = build_job_model("Government role", text)
        essential = [c for c in model["criteria"] if c["criteria_type"] == "essential"]
        self.assertEqual(len(essential), 5)
        self.assertEqual([c["criteria_text"] for c in essential], list(attached.values()))
        self.assertTrue(all("and prepare written reports." in c["criteria_text"] for c in essential))
        self.assertTrue(all(c["source_end"] > c["source_start"] for c in essential))

    def test_aggregate_scope_overlap_and_unknown_dates(self):
        def evidence(id, start, end, kind="experience"):
            return {"evidence_id": id, "evidence_type": kind, "source_group_id": id,
                    "time_period": {"start": start, "end": end}, "source_text": "Part time work"}
        records = [evidence("a", "Jan 2010", "Jan 2020"), evidence("b", "Jan 2015", "Jan 2021"),
                   evidence("v", "Jan 1990", "Jan 2025", "volunteer")]
        result = aggregate_experience(records, date(2026, 9, 8))
        self.assertEqual(result["years"], 10)
        self.assertEqual(result["excluded_evidence_ids"], ["v"])
        self.assertEqual(len(result["merged_intervals"]), 1)
        self.assertFalse(delivery_issues(result["allowed_claim"], "tailored_resume", aggregate=result))
        for claim in ("15 years of experience", "10+ years of finance experience", "over ten years' experience"):
            self.assertIn("aggregate_claim_unverified", {x["code"] for x in delivery_issues(claim, "cover_letter", aggregate=result)})
        records.append(evidence("unknown", "2019", "2024"))
        self.assertIsNone(aggregate_experience(records)["allowed_claim"])

    def test_identity_and_availability_all_document_types(self):
        for kind in ("tailored_resume", "cover_letter", "selection_criteria"):
            bad = "Hua Zhong\nFlora Zhong\n0400123456 | flora@example.com"
            self.assertIn("canonical_name_conflict", {i["code"] for i in delivery_issues(bad, kind, self.profile)})
            good = "Flora Zhong\n0400123456 | flora@example.com"
            self.assertEqual(delivery_issues(good, kind, self.profile), [])
        self.assertTrue(profile_missing_fields(None))
        self.assertTrue(availability_issues("I am available from 11 September 2026.", "one_month"))
        self.assertFalse(availability_issues("I am available following one month's notice.", "one_month"))
        self.assertTrue(availability_issues("I'm an Australian permanent resident and will be available for Perth-based work from 11 September 2026.", "one_month"))

    def test_finance_duties_cannot_move_to_support_role(self):
        evidence = [{"source_section": "Work Experience > Department of Communities > Finance Officer",
                     "source_text": "Used Dayforce; processed journals and reconciliation."},
                    {"source_section": "Work Experience > Mable > Support Worker", "source_text": "Supported clients with daily living."},
                    {"source_section": "Work Experience > My Support > Support Worker", "source_text": "Supported appointments."}]
        for employer in ("Mable", "My Support"):
            bad = f"## Work Experience\n### Support Worker\n{employer}\n- Processed journals and reconciliation."
            self.assertIn("cross_experience_attribution", {i["code"] for i in delivery_issues(bad, "tailored_resume", ckb=evidence)})
        good = "## Work Experience\n### Finance Officer\nDepartment of Communities\n- Processed journals and reconciliation.\n### Support Worker\nMable\n- Supported daily living.\n## Technical Skills\nDayforce"
        self.assertFalse(delivery_issues(good, "tailored_resume", ckb=evidence))

    def test_placeholders_and_signature_body_block_both_formats(self):
        good = "Flora Zhong\n0400123456 | flora@example.com\n\nDear Hiring Manager,\n\nI coordinated reporting.\n\nYours faithfully,\nFlora Zhong"
        for render, fmt in ((create_docx, "docx"), (create_pdf, "pdf")):
            for bad in (good + "\n- More body text.", "Unable to provide a response due to insufficient evidence."):
                self.assertFalse(verify_document_export(bad, render(bad, "Cover Letter"), fmt)["ready"])
            artifact = render(good, "Cover Letter")
            self.assertTrue(verify_document_export(good, artifact, fmt)["ready"])
            extracted, _ = extract_artifact(artifact, fmt)
            self.assertIn("Flora Zhong", extracted)
        response = {"final_response": "Unable to provide a response.", "star": dict.fromkeys(("situation", "task", "action", "result"), "")}
        self.assertFalse(hard_validate_response(response, {})["valid"])

    def test_work_rights_change_invalidates_confirmation(self):
        app = SimpleNamespace(company="Agency", position_title="Officer", job_url="")
        before = details_fingerprint(app, self.profile)
        self.profile.work_rights = "citizen"
        self.assertNotEqual(before, details_fingerprint(app, self.profile))


if __name__ == "__main__":
    unittest.main()
