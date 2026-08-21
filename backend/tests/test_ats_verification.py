import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.application_decision import decision_inputs
from app.application_requirements import empty_application_requirements
from app.ats_verification import verify_resume_artifact
from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, QualityCheckResponse, Resume
from app.release_state import pack_fingerprint


CONTENT = """Alex Morgan
alex@example.com | 0400 000 000
## Professional Summary
**Project Reporting** specialist.
## Key Skills
- Reporting
## Work Experience
### Finance Officer | Current Agency | Jan 2024 - Present
- Prepared reports.
### Project Administrator | Older Agency | Jan 2020 - Dec 2023
- Coordinated projects.
"""
PLAN = {
    "schema_version": "1.1", "maximum_pages": 2,
    "required_sections": ["Professional Summary", "Key Skills", "Work Experience"],
    "roles": [
        {"source_section": "Work Experience > Current Agency > Finance Officer", "include_role_header": True, "is_current": True, "selected_evidence_ids": ["E1"]},
        {"source_section": "Work Experience > Older Agency > Project Administrator", "include_role_header": True, "is_current": False, "selected_evidence_ids": ["E2"]},
    ],
    "selected_evidence": [{"evidence_id": "E1"}, {"evidence_id": "E2"}],
}
CKB = [
    {"evidence_id": "E1", "source_section": "Work Experience > Current Agency > Finance Officer", "source_text": "Prepared reports.", "competency_tags": ["monthly reporting"]},
    {"evidence_id": "E2", "source_section": "Work Experience > Older Agency > Project Administrator", "source_text": "Coordinated projects.", "competency_tags": []},
]
JOB = {"criteria": [{"criteria_id": "C1", "key_competencies": ["reporting"]}]}
DECISION = {"requirements": [{"criteria_id": "C1", "evidence_classification": "verified_match", "matched_evidence": ["E1"]}]}
PROFILE = SimpleNamespace(first_name="Alex", last_name="Morgan", email="alex@example.com", phone="0400000000")


class AtsVerificationUnitTests(unittest.TestCase):
    def check(self, format="docx", content=CONTENT, **kwargs):
        return verify_resume_artifact(content, format, "classic", kwargs.get("plan", PLAN), kwargs.get("profile", PROFILE), CKB, kwargs.get("job", JOB), kwargs.get("decision", DECISION), kwargs.get("market"), kwargs.get("page_size"))

    def test_valid_docx_and_pdf_are_ready_and_page_contract_is_format_specific(self):
        docx = self.check("docx")
        pdf = self.check("pdf", market="AU")
        self.assertTrue(docx["ready"]); self.assertTrue(pdf["ready"])
        self.assertEqual(next(x for x in docx["checks"] if x["code"] == "page_count")["state"], "not_applicable")
        self.assertEqual(pdf["artifact"]["page_size"], "595 x 842 pt")
        self.assertEqual(pdf["artifact"]["expected_page_size"], "A4")

    def test_empty_pdf_text_layer_blocks_without_ocr(self):
        with patch("app.ats_verification.extract_artifact", return_value=("", {"page_count": 1, "page_size": "612 x 792 pt"})):
            result = self.check("pdf")
        self.assertFalse(result["ready"])
        self.assertEqual(next(x for x in result["checks"] if x["code"] == "pdf_text_layer")["state"], "fail")

    def test_corrupt_artifact_blocks(self):
        with patch("app.ats_verification.extract_artifact", side_effect=ValueError("corrupt")):
            result = self.check()
        self.assertFalse(result["ready"])
        self.assertEqual(result["checks"][0]["state"], "unavailable")

    def test_missing_identity_and_required_heading_block(self):
        result = self.check(content=CONTENT.replace("Alex Morgan", "Applicant").replace("alex@example.com", "").replace("0400 000 000", "").replace("## Key Skills", ""))
        failed = {x["code"] for x in result["checks"] if x["blocking"]}
        self.assertTrue({"applicant_name", "email", "phone", "required_heading"} <= failed)

    def test_missing_current_role_blocks_but_ambiguous_marker_warns(self):
        missing = self.check(content=CONTENT.replace("### Finance Officer | Current Agency | Jan 2024 - Present", "### Current Position"))
        self.assertFalse(missing["ready"])
        ambiguous_plan = {**PLAN, "roles": [{"source_section": "Work", "include_role_header": True, "is_current": True, "selected_evidence_ids": []}]}
        ambiguous = self.check(plan=ambiguous_plan)
        self.assertTrue(ambiguous["ready"])
        self.assertEqual(next(x for x in ambiguous["checks"] if x["code"] == "role_header")["state"], "warning")

    def test_reversed_reliable_roles_block(self):
        first, second = CONTENT.index("### Finance"), CONTENT.index("### Project")
        reversed_roles = CONTENT[:first] + CONTENT[second:] + "\n" + CONTENT[first:second]
        result = self.check(content=reversed_roles)
        self.assertEqual(next(x for x in result["checks"] if x["code"] == "role_chronology")["state"], "fail")

    def test_markdown_removal_and_typographic_date_dashes_are_retained(self):
        result = self.check(content=CONTENT.replace("Jan 2024 - Present", "Jan 2024–Present"))
        self.assertTrue(result["ready"])
        self.assertGreaterEqual(result["artifact"]["content_retention_ratio"], .95)

    def test_unicode_docx_survives_and_replacement_glyph_blocks(self):
        profile = SimpleNamespace(first_name="José", last_name="García", email="alex@example.com", phone="0400000000")
        unicode_content = CONTENT.replace("Alex Morgan", "José García")
        self.assertTrue(self.check(content=unicode_content, profile=profile)["ready"])
        with patch("app.ats_verification.extract_artifact", return_value=(CONTENT + "\n��", {"page_count": None, "page_size": None})):
            corrupt = self.check()
        self.assertFalse(corrupt["ready"])
        self.assertEqual(next(x for x in corrupt["checks"] if x["code"] == "corrupted_glyphs")["state"], "fail")

    def test_major_content_loss_and_pdf_page_limit_block(self):
        with patch("app.ats_verification.extract_artifact", return_value=("Alex Morgan alex@example.com 0400000000 Professional Summary Key Skills Work Experience Current Agency Finance Officer", {"page_count": 3, "page_size": "612 x 792 pt"})):
            result = self.check("pdf")
        failed = {x["code"] for x in result["checks"] if x["blocking"]}
        self.assertTrue({"major_content_loss", "page_count"} <= failed)

    def test_page_size_match_letter_a4_mismatch_and_unknown_market(self):
        letter_result = self.check("pdf", market="US")
        self.assertEqual(letter_result["artifact"]["page_size"], "612 x 792 pt")
        self.assertEqual(next(x for x in letter_result["checks"] if x["code"] == "page_size_match")["state"], "pass")
        with patch("app.ats_verification.create_pdf", side_effect=lambda content, title, template, market, page_size: __import__("app.exporter", fromlist=["create_pdf"]).create_pdf(content, title, template, page_size="Letter")):
            mismatch = self.check("pdf", market="AU")
        self.assertFalse(mismatch["ready"])
        self.assertEqual(next(x for x in mismatch["checks"] if x["code"] == "page_size_match")["state"], "fail")
        unknown = self.check("pdf")
        self.assertTrue(unknown["ready"])
        self.assertEqual(next(x for x in unknown["checks"] if x["code"] == "page_size_match")["state"], "warning")

    def test_keyword_states_are_advisory(self):
        covered = self.check()
        self.assertEqual(covered["keywords"][0]["status"], "covered")
        synonym_job = {"criteria": [{"criteria_id": "C1", "key_competencies": ["financial reporting"]}]}
        synonym = self.check(job=synonym_job, content=CONTENT.replace("Reporting", "Monthly reporting"))
        self.assertEqual(synonym["keywords"][0]["status"], "synonym_only")
        missing = self.check(content=CONTENT.replace("Reporting", "Updates"))
        self.assertEqual(missing["keywords"][0]["status"], "missing_but_supported")
        self.assertTrue(missing["ready"])
        gap = self.check(job={"criteria": [{"criteria_id": "C2", "key_competencies": ["leadership"]}]}, decision={"requirements": [{"criteria_id": "C2", "evidence_classification": "confirmed_gap", "matched_evidence": []}]})
        self.assertEqual(gap["keywords"][0]["status"], "missing_genuine_gap")
        self.assertIn("do not add", gap["keywords"][0]["message"].lower())
        repeated = self.check(content=CONTENT + " reporting" * 10)
        self.assertTrue(repeated["ready"])
        self.assertEqual(next(x for x in repeated["checks"] if x["code"] == "keyword_repetition")["state"], "warning")

    def test_unicode_lost_in_pdf_blocks(self):
        profile = SimpleNamespace(first_name="李", last_name="雷", email="alex@example.com", phone="0400000000")
        result = self.check("pdf", content=CONTENT.replace("Alex Morgan", "李雷"), profile=profile)
        self.assertFalse(result["ready"])
        self.assertEqual(next(x for x in result["checks"] if x["code"] == "applicant_name")["state"], "fail")


class AtsVerificationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_current_user] = lambda: None
        requirements = empty_application_requirements("Confirmed", source="user_supplied")
        requirements["review_status"] = "confirmed"
        requirements["documents"]["resume"].update(requirement="required", format="standalone", basis="user_confirmed")
        requirements["documents"]["cover_letter"].update(requirement="required", format="standalone", basis="user_confirmed")
        requirements["documents"]["selection_criteria"].update(requirement="not_required", format="not_applicable", basis="user_confirmed")
        with Session(self.engine) as session:
            profile = ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com")
            resume = Resume(source_text="Alex Morgan", ckb_json=json.dumps(CKB))
            application = JobApplication(company="Agency", position_title="Officer", job_description="Reporting", job_model_json=json.dumps(JOB), application_requirements_json=json.dumps(requirements))
            session.add_all([profile, resume, application]); session.flush()
            application.application_decision_json = json.dumps({"schema_version": "1.0", "status": "ready", "requirements": [], "questions": [], "inputs": decision_inputs(JOB, requirements, CKB, profile)})
            document = GeneratedDocument(application_id=application.id, document_type="tailored_resume", content=CONTENT, structured_content_json=json.dumps(PLAN), reviewer_json='{"status":"pass"}')
            cover = GeneratedDocument(application_id=application.id, document_type="cover_letter", content="not reviewed", reviewer_json="{}")
            session.add_all([application, document, cover]); session.commit(); session.refresh(document); session.refresh(cover)
            self.document_id, self.cover_id = document.id, cover.id
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear(); self.engine.dispose()

    def allow_release_prerequisites(self):
        with Session(self.engine) as session:
            application = session.exec(select(JobApplication)).first()
            profile = session.exec(select(ApplicantProfile)).first()
            documents = {item.document_type: item for item in session.exec(select(GeneratedDocument)).all()}
            application.release_state_json = json.dumps({"pack_review": {
                "fingerprint": pack_fingerprint(application, profile, documents),
                "result": {"status": "pass", "blocks_release": False, "skipped": True, "skip_reason": "No candidates", "results": []},
            }})
            session.add(application); session.commit()

    def test_resume_check_persists_exact_artifact_identity_without_mutating_document(self):
        self.allow_release_prerequisites()
        with Session(self.engine) as session:
            before = session.get(GeneratedDocument, self.document_id).model_dump()
        with patch("app.main.quality_check", return_value=QualityCheckResponse(ready=True, issues=[], checked_documents=["cover_letter", "tailored_resume"])):
            response = self.client.post(f"/documents/{self.document_id}/ats-check", json={"format": "docx", "template": "classic"})
        self.assertEqual(response.status_code, 200); self.assertTrue(response.json()["ready"])
        with Session(self.engine) as session:
            self.assertEqual(session.get(GeneratedDocument, self.document_id).model_dump(), before)
            state = json.loads(session.exec(select(JobApplication)).first().release_state_json)
            self.assertEqual(state["ats"]["document_id"], self.document_id)
            self.assertEqual(state["ats"]["format"], "docx")
            self.assertEqual(state["ats"]["template"], "classic")

    def test_non_resume_and_cleared_reviewer_are_rejected(self):
        self.assertEqual(self.client.post(f"/documents/{self.cover_id}/ats-check", json={"format": "docx"}).status_code, 422)
        with Session(self.engine) as session:
            document = session.get(GeneratedDocument, self.document_id); document.reviewer_json = "{}"; session.commit()
        self.assertEqual(self.client.post(f"/documents/{self.document_id}/ats-check", json={"format": "docx"}).status_code, 409)

    def test_current_pack_review_is_required_before_ats_verification(self):
        with patch("app.main.quality_check", return_value=QualityCheckResponse(ready=True, issues=[], checked_documents=["cover_letter", "tailored_resume"])):
            response = self.client.post(f"/documents/{self.document_id}/ats-check", json={"format": "docx"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Pack Review", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
