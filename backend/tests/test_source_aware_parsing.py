import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app
from app.models import JobApplication
from app.source_aware_parsing import build_source_aware_models


def source(source_id, source_type, text="", extraction_status="extracted"):
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        acquisition_status="uploaded" if source_type != "primary_advertisement" else "fetched",
        extraction_status=extraction_status,
        extracted_text=text,
    )


def application(advertisement):
    return SimpleNamespace(
        job_description=advertisement,
        selection_criteria=None,
        position_title="Project Officer",
        company="Transwa",
    )


JDF = """Selection Criteria
1. Demonstrated communication skills.
2. Experience delivering transport projects.
3. Knowledge of safety frameworks.
4. Ability to prepare written reports.
5. Demonstrated stakeholder engagement.
Qualifications
Relevant qualifications are desirable.
"""


class SourceAwareParsingTests(unittest.TestCase):
    def test_transwa_references_resolve_exact_formal_jdf_criteria(self):
        ad = "Submit a maximum two page cover letter addressing criteria 1, 2 and 3 in the attached JDF."
        requirements, model = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad), source("jdf", "job_description_attachment", JDF),
        ])

        selection = requirements["documents"]["selection_criteria"]
        self.assertEqual((requirements["documents"]["cover_letter"]["requirement"], selection["format"]), ("required", "embedded_in_cover_letter"))
        self.assertEqual(selection["criteria_references"], ["1", "2", "3"])
        self.assertEqual([item["source_reference"] for item in model["criteria"]], ["1", "2", "3"])
        self.assertTrue(all(item["source_id"] == "jdf" for item in model["criteria"]))

    def test_transwa_numbered_materials_and_missing_jdf_are_truthful(self):
        ad = (
            "Please provide:\n1. A comprehensive CV with two work related referees.\n"
            "2. A Cover Letter addressing selection criteria 1, 2 and 3 as highlighted in the attached JDF."
        )
        requirements, model = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad),
            source("jdf", "job_description_attachment", "", "not_attempted"),
        ])
        documents = requirements["documents"]
        self.assertEqual((documents["resume"]["requirement"], documents["cover_letter"]["requirement"]), ("required", "required"))
        self.assertEqual(documents["selection_criteria"]["criteria_references"], ["1", "2", "3"])
        self.assertEqual(requirements["completeness"], "incomplete")
        self.assertTrue(requirements["warnings"])
        self.assertFalse(any(item.get("source_id") == "jdf" for item in model["criteria"]))

    def test_nonconsecutive_and_range_references_are_exact(self):
        for wording, expected in (("criteria 1, 3 and 5", ["1", "3", "5"]), ("criteria 2-4", ["2", "3", "4"])):
            requirements, model = build_source_aware_models(application(wording), [
                source("ad", "primary_advertisement", wording), source("jdf", "job_description_attachment", JDF),
            ])
            self.assertEqual(requirements["documents"]["selection_criteria"]["criteria_references"], expected)
            self.assertEqual([item["source_reference"] for item in model["criteria"]], expected)

    def test_missing_reference_is_incomplete_and_not_substituted(self):
        ad = "Address criteria 1, 2 and 6 in the attached JDF."
        requirements, model = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad), source("jdf", "job_description_attachment", JDF),
        ])

        self.assertEqual(requirements["completeness"], "incomplete")
        self.assertTrue(any("criteria 6" in warning for warning in requirements["warnings"]))
        self.assertEqual([item["source_reference"] for item in model["criteria"]], ["1", "2"])

    def test_partial_jdf_is_excluded_truthfully(self):
        ad = "Address criteria 1 and 2 in the attached JDF."
        requirements, model = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad),
            source("jdf", "job_description_attachment", JDF, "partial"),
        ])

        self.assertEqual(requirements["completeness"], "incomplete")
        self.assertTrue(any("partial" in warning for warning in requirements["warnings"]))
        self.assertFalse(any(item.get("source_id") == "jdf" for item in model["criteria"]))

    def test_instruction_source_updates_requirements_without_becoming_criteria(self):
        ad = "See the Candidate Information Pack for how to apply."
        instructions = "Submit a maximum two page cover letter addressing criteria 1, 2 and 3 in the attached JDF."
        requirements, model = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad),
            source("pack", "application_instruction_attachment", instructions),
            source("jdf", "job_description_attachment", JDF),
        ])

        self.assertEqual(requirements["documents"]["cover_letter"]["limit"]["value"], 2)
        self.assertEqual([item["source_reference"] for item in model["criteria"]], ["1", "2", "3"])
        self.assertFalse(any("maximum two page" in item["criteria_text"].lower() for item in model["criteria"]))

    def test_separate_attachment_notice_does_not_override_embedded_format_across_sources(self):
        ad = "Submit your CV and a maximum two-page cover letter addressing the following three criteria."
        requirements, _ = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad),
            source("pack", "application_instruction_attachment", "No separate Selection Criteria attachment is required."),
        ])
        selection = requirements["documents"]["selection_criteria"]
        self.assertEqual((selection["requirement"], selection["format"], selection["criteria_count"]), ("not_required", "embedded_in_cover_letter", 3))

    def test_mandatory_forms_are_not_semantic_inputs(self):
        ad = "Coordinate projects and prepare reports."
        _, model = build_source_aware_models(application(ad), [
            source("ad", "primary_advertisement", ad),
            source("form", "mandatory_form", "Selection Criteria\n1. Consent to a police check."),
        ])
        self.assertFalse(any("consent" in item["criteria_text"].lower() for item in model["criteria"]))


class SourceAwareIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def create_application(self, ad):
        response = self.client.post("/applications", json={
            "company": "Transwa", "position_title": "Project Officer", "job_description": ad,
        })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def upload_jdf(self, application_id, target_id, text):
        return self.client.post(
            f"/applications/{application_id}/sources/upload",
            data={"expected_source_type": "job_description_attachment", "target_source_id": target_id},
            files={"file": ("JDF.txt", text.encode(), "text/plain")},
        )

    def get_application(self, application_id):
        return next(item for item in self.client.get("/applications").json() if item["id"] == application_id)

    def test_manual_upload_rebuilds_models_and_invalidates_stale_derivatives(self):
        ad = "Submit a two page cover letter addressing criteria 1 and 2 in the attached JDF."
        created = self.create_application(ad)
        sources = self.client.get(f"/applications/{created['id']}/sources").json()
        target = next(item for item in sources if item["source_type"] == "job_description_attachment")
        with Session(self.engine) as session:
            stored = session.get(JobApplication, created["id"])
            stored.evidence_matches_json = '{"matches":[1]}'
            stored.selection_plan_json = '{"items":[1]}'
            stored.selection_confirmations_json = '["C1"]'
            session.add(stored); session.commit()

        response = self.upload_jdf(created["id"], target["source_id"], JDF)
        self.assertEqual(response.status_code, 200)
        updated = self.get_application(created["id"])
        requirements = json.loads(updated["application_requirements_json"])
        model = json.loads(updated["job_model_json"])
        self.assertEqual(requirements["documents"]["selection_criteria"]["criteria_references"], ["1", "2"])
        self.assertEqual([item["source_reference"] for item in model["criteria"]], ["1", "2"])
        self.assertEqual((updated["evidence_matches_json"], updated["selection_plan_json"], updated["selection_confirmations_json"]), ("{}", "{}", "[]"))

    def test_creation_persists_unresolved_attached_jdf_warning(self):
        created = self.create_application("Address criteria 1, 2 and 3 in the attached JDF.")
        requirements = self.client.get(f"/applications/{created['id']}/application-requirements").json()["requirements"]
        self.assertEqual(requirements["review_status"], "needs_confirmation")
        self.assertEqual(requirements["completeness"], "incomplete")
        self.assertEqual(requirements["documents"]["selection_criteria"]["criteria_references"], ["1", "2", "3"])
        self.assertTrue(any("not completely extracted" in warning for warning in requirements["warnings"]))

    def test_replacing_source_with_changed_text_rebuilds_and_preserves_source_id(self):
        ad = "Address criterion 1 in the attached JDF."
        created = self.create_application(ad)
        sources = self.client.get(f"/applications/{created['id']}/sources").json()
        target = next(item for item in sources if item["source_type"] == "job_description_attachment")
        first = self.upload_jdf(created["id"], target["source_id"], JDF)
        changed = JDF.replace("Demonstrated communication skills.", "Demonstrated written communication capability.")
        second = self.upload_jdf(created["id"], target["source_id"], changed)

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        replaced = next(item for item in second.json() if item["source_id"] == target["source_id"])
        self.assertIn("written communication capability", replaced["extracted_text"])
        model = json.loads(self.get_application(created["id"])["job_model_json"])
        self.assertEqual(model["criteria"][0]["criteria_text"], "Demonstrated written communication capability.")

    def test_same_extracted_text_does_not_invalidate_derivatives(self):
        created = self.create_application("Address criterion 1 in the attached JDF.")
        target = next(item for item in self.client.get(f"/applications/{created['id']}/sources").json() if item["source_type"] == "job_description_attachment")
        self.upload_jdf(created["id"], target["source_id"], JDF)
        with Session(self.engine) as session:
            stored = session.get(JobApplication, created["id"])
            stored.evidence_matches_json = '{"matches":[1]}'
            stored.selection_plan_json = '{"items":[1]}'
            stored.selection_confirmations_json = '["C1"]'
            session.add(stored); session.commit()

        response = self.upload_jdf(created["id"], target["source_id"], JDF)

        self.assertEqual(response.status_code, 200)
        unchanged = self.get_application(created["id"])
        self.assertEqual((unchanged["evidence_matches_json"], unchanged["selection_plan_json"], unchanged["selection_confirmations_json"]), ('{"matches":[1]}', '{"items":[1]}', '["C1"]'))

    def test_automatic_acquisition_rebuilds_from_extracted_source(self):
        ad = "Address criterion 1 in the attached JDF."
        created = self.create_application(ad)

        def acquire(sources):
            target = next(item for item in sources if item.source_type == "job_description_attachment")
            target.acquisition_status = "fetched"
            target.extraction_status = "extracted"
            target.extracted_text = JDF
            target.content_sha256 = "a" * 64

        with patch("app.main.acquire_sources", side_effect=acquire):
            response = self.client.post(f"/applications/{created['id']}/sources/acquire")

        self.assertEqual(response.status_code, 200)
        model = json.loads(self.get_application(created["id"])["job_model_json"])
        self.assertEqual(model["criteria"][0]["source_reference"], "1")

    def test_acquisition_without_semantic_change_preserves_review_and_derivatives(self):
        created = self.create_application("Coordinate projects and prepare reports.")
        requirements = json.loads(created["application_requirements_json"])
        requirements["review_status"] = "confirmed"
        with Session(self.engine) as session:
            stored = session.get(JobApplication, created["id"])
            stored.application_requirements_json = json.dumps(requirements)
            stored.evidence_matches_json = '{"matches":[1]}'
            stored.selection_plan_json = '{"items":[1]}'
            stored.selection_confirmations_json = '["C1"]'
            session.add(stored); session.commit()

        with patch("app.main.acquire_sources", return_value=None):
            response = self.client.post(f"/applications/{created['id']}/sources/acquire")

        self.assertEqual(response.status_code, 200)
        unchanged = self.get_application(created["id"])
        self.assertEqual(json.loads(unchanged["application_requirements_json"])["review_status"], "confirmed")
        self.assertEqual((unchanged["evidence_matches_json"], unchanged["selection_plan_json"], unchanged["selection_confirmations_json"]), ('{"matches":[1]}', '{"items":[1]}', '["C1"]'))

    def test_ordinary_application_remains_compatible(self):
        created = self.create_application("Coordinate projects and prepare reports.")
        self.assertEqual(len(self.client.get(f"/applications/{created['id']}/sources").json()), 1)
        self.assertNotEqual(json.loads(created["job_model_json"])["criteria"], [])


if __name__ == "__main__":
    unittest.main()
