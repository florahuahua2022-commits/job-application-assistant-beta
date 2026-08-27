import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.auth import get_current_user
from app.application_requirements import empty_application_requirements, parse_application_requirements
from app.application_decision import decision_inputs
from app.main import app, auto_polish_cover_letter, auto_polish_tailored_resume, build_resume_content_check, enforce_profile_contact, organisation_is_named
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, Resume
from app import backup


class SubmissionRecordTests(unittest.TestCase):
    def test_resume_content_check_matches_source_and_flags_unsupported_edit(self):
        resume = Resume(
            source_text="Alex Morgan\n0400 000 000\nalex@example.com\nProject Officer\nExample Agency\nPrepared monthly reports and project registers.",
            experiences_json='[{"role_title":"Project Director","organization":"Example Agency","responsibility":"Prepared monthly reports and project registers."}]',
        )
        profile = ApplicantProfile(
            first_name="Alex", last_name="Morgan", phone="0400 000 000", email="alex@example.com",
        )

        result = build_resume_content_check(resume, profile)

        statuses = {item.field: item.status for item in result.items}
        self.assertEqual(statuses["profile.full_name"], "matched")
        self.assertEqual(statuses["experiences.1.organization"], "matched")
        self.assertEqual(statuses["experiences.1.role_title"], "review")
        self.assertFalse(result.ready)

    def test_resume_content_check_reports_missing_structured_experience(self):
        resume = Resume(source_text="Alex Morgan\nalex@example.com\n0400 000 000", experiences_json="[]")
        profile = ApplicantProfile(
            first_name="Alex", last_name="Morgan", phone="0400 000 000", email="alex@example.com",
        )

        result = build_resume_content_check(resume, profile)

        self.assertEqual(result.missing_count, 1)
        self.assertEqual(result.items[-1].field, "experiences")

    def test_auto_polish_matches_generic_salutation_and_signoff(self):
        polished = auto_polish_cover_letter(
            "Dear Hiring Manager,\n\nEvidence.\n\nYours sincerely,\nAlex Morgan",
            None,
        )

        self.assertIn("Yours faithfully", polished)
        self.assertNotIn("Yours sincerely", polished)

    def test_auto_polish_replaces_generic_application_opening(self):
        polished = auto_polish_cover_letter(
            "I am writing to apply for the Project Administrator position.",
            None,
        )

        self.assertEqual(
            polished,
            "Please accept my application for the Project Administrator position.",
        )

    def test_auto_polish_replaces_email_style_cover_letter_heading(self):
        polished = auto_polish_cover_letter(
            "9 August 2026\nRE: Project Administrator\nDear Hiring Manager,",
            None,
        )

        self.assertIn("Application for Project Administrator", polished)
        self.assertNotIn("RE:", polished)

    def test_resume_polish_standardises_sections_and_reference_wording(self):
        polished = auto_polish_tailored_resume(
            "Alex Morgan\nProfessional Profile\nExperienced coordinator.\nCore Capabilities\n- Reporting\nEmployment History\nProject Officer\nReferences available on request."
        )

        self.assertIn("## Professional Summary", polished)
        self.assertIn("## Key Skills", polished)
        self.assertIn("## Work Experience", polished)
        self.assertIn("## References\nAvailable upon request", polished)

    def test_online_user_cannot_list_or_update_another_users_application(self):
        owner_id = uuid4()
        other_user_id = uuid4()
        with Session(self.engine) as session:
            owned = JobApplication(
                user_id=owner_id,
                company="Private Employer",
                position_title="Private Role",
                job_description="Private description",
            )
            session.add(owned)
            session.commit()
            session.refresh(owned)
            owned_id = owned.id

        app.dependency_overrides[get_current_user] = lambda: other_user_id

        listed = self.client.get("/applications")
        updated = self.client.patch(
            f"/applications/{owned_id}",
            json={"company": "Changed Employer"},
        )

        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(owned_id, [item["id"] for item in listed.json()])
        self.assertEqual(updated.status_code, 404)

    def test_auto_polish_uses_notice_period_without_inventing_date(self):
        profile = ApplicantProfile(
            first_name="Alex", last_name="Morgan", phone="0400000000",
            email="applicant@example.com", availability_notice="one_month",
        )

        polished = auto_polish_cover_letter(
            "I am available to commence mid-September. I hold permanent residency.",
            profile,
        )

        self.assertIn("I am available following one month's notice.", polished)
        self.assertNotIn("mid-September", polished)

    def test_organisation_match_accepts_common_acronym(self):
        self.assertTrue(organisation_is_named(
            "Minerals Research Institute of Western Australia",
            "I am applying for the Program Coordinator role with MRIWA.",
        ))

    def test_generated_cover_contact_is_forced_to_saved_profile(self):
        profile = ApplicantProfile(
            first_name="Alex",
            last_name="Morgan",
            phone="0400 000 000",
            email="correct@example.com",
        )

        corrected = enforce_profile_contact(
            "4 August 2026\nDear Hiring Manager\nPhone: 0499 999 999\nold@example.com",
            profile,
            "cover_letter",
        )

        self.assertIn("0400 000 000", corrected)
        self.assertIn("correct@example.com", corrected)
        self.assertNotIn("0499 999 999", corrected)
        self.assertNotIn("old@example.com", corrected)

    def test_cover_contact_separates_email_from_location(self):
        profile = ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400 000 000", email="alex@example.com")
        corrected = enforce_profile_contact(
            "Yours faithfully\nAlex Morgan\nEmail: alex@example.com WA, Australia",
            profile,
            "cover_letter",
        )
        self.assertIn("Email: alex@example.com\nWA, Australia", corrected)

    def test_missing_contact_is_added_to_generated_resume(self):
        profile = ApplicantProfile(
            first_name="Alex",
            last_name="Morgan",
            phone="0400 000 000",
            email="correct@example.com",
        )

        corrected = enforce_profile_contact("Alex Morgan\nProfessional Summary", profile, "tailored_resume")

        self.assertTrue(corrected.startswith("Phone: 0400 000 000\nEmail: correct@example.com"))

    def test_missing_canonical_name_is_added_without_changing_contact_enforcement(self):
        profile = ApplicantProfile(first_name="HUA", last_name="ZHONG", phone="0400 000 000", email="hua@example.com")

        corrected = enforce_profile_contact("## Professional Summary\nGrounded experience.", profile, "tailored_resume")

        self.assertTrue(corrected.startswith("HUA ZHONG\nPhone: 0400 000 000\nEmail: hua@example.com"))

    def test_blank_canonical_name_is_not_fabricated(self):
        profile = ApplicantProfile(first_name="", last_name="", phone="0400 000 000", email="hua@example.com")

        corrected = enforce_profile_contact("## Professional Summary\nGrounded experience.", profile, "tailored_resume")

        self.assertTrue(corrected.startswith("Phone: 0400 000 000\nEmail: hua@example.com"))

    def setUp(self):
        self.backup_directory = TemporaryDirectory()
        self.backup_patch = patch.object(backup, "BACKUP_DIR", Path(self.backup_directory.name))
        self.backup_patch.start()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        with Session(self.engine) as session:
            application = JobApplication(
                company="Example Agency",
                position_title="Project Officer",
                job_description="Coordinate projects.",
            )
            session.add(application)
            session.commit()
            session.refresh(application)
            self.application_id = application.id
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.backup_patch.stop()
        self.backup_directory.cleanup()

    def _confirm_quality_contract(self, session, required=("resume", "cover_letter")):
        application = session.get(JobApplication, self.application_id)
        requirements = empty_application_requirements("Confirmed test requirements", source="user_supplied")
        requirements["review_status"] = "confirmed"
        for name, document in requirements["documents"].items():
            document.update(requirement="not_required", format="not_applicable")
        for name in required:
            requirements["documents"][name].update(requirement="required", format="standalone")
        application.application_requirements_json = json.dumps(requirements)
        application.job_model_json = "{}"
        profile = session.exec(select(ApplicantProfile).order_by(ApplicantProfile.id)).first()
        if not profile:
            profile = ApplicantProfile(
                first_name="Alex", last_name="Morgan", phone="0400000000", email="applicant@example.com"
            )
            session.add(profile)
        resume = session.exec(select(Resume).order_by(Resume.updated_at.desc())).first()
        if not resume:
            resume = Resume(source_text="Alex Morgan", ckb_json="[]")
            session.add(resume)
        session.flush()
        application.application_decision_json = json.dumps({
            "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
            "inputs": decision_inputs({}, requirements, json.loads(resume.ckb_json or "[]"), profile),
            "requirements": [], "questions": [], "blocking_issues": [],
        })
        session.add(application)
        return application

    def test_records_confirmation_and_marks_application_applied(self):
        with Session(self.engine) as session:
            session.get(JobApplication, self.application_id).status = "ready_to_apply"; session.commit()
        response = self.client.patch(
            f"/applications/{self.application_id}/submission",
            json={"submission_reference": "19837293"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "applied")
        self.assertEqual(payload["submission_reference"], "19837293")
        self.assertIsNotNone(payload["submitted_at"])

    def test_accepts_blank_optional_confirmation_reference(self):
        with Session(self.engine) as session:
            session.get(JobApplication, self.application_id).status = "ready_to_apply"; session.commit()
        response = self.client.patch(
            f"/applications/{self.application_id}/submission",
            json={"submission_reference": "   "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "applied")
        self.assertIsNone(response.json()["submission_reference"])
        self.assertIsNotNone(response.json()["submitted_at"])

    def test_updates_application_pipeline_status(self):
        response = self.client.patch(
            f"/applications/{self.application_id}/status",
            json={"status": "ready_to_apply"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["message"], "Complete the release checklist before marking this application Ready.")

    def test_archives_own_application(self):
        response = self.client.patch(f"/applications/{self.application_id}/archive", json={"action": "archive"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["archived_at"])

    def test_restores_own_application(self):
        self.client.patch(f"/applications/{self.application_id}/archive", json={"action": "archive"})
        response = self.client.patch(f"/applications/{self.application_id}/archive", json={"action": "restore"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["archived_at"])

    def test_archive_preserves_workflow_status(self):
        with Session(self.engine) as session:
            session.get(JobApplication, self.application_id).status = "applied"
            session.commit()

        response = self.client.patch(f"/applications/{self.application_id}/archive", json={"action": "archive"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "applied")

    def test_cannot_archive_another_users_application(self):
        app.dependency_overrides[get_current_user] = lambda: uuid4()

        response = self.client.patch(f"/applications/{self.application_id}/archive", json={"action": "archive"})

        self.assertEqual(response.status_code, 404)

    def test_updates_saved_job_details_without_losing_jd(self):
        response = self.client.patch(
            f"/applications/{self.application_id}",
            json={
                "company": "Metrowest",
                "position_title": "Projects Administrator",
                "job_url": "https://example.com/job/123",
                "job_description": "Coordinate purchase orders, invoices, reporting and project documents.",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["company"], "Metrowest")
        self.assertEqual(payload["job_url"], "https://example.com/job/123")
        self.assertIn("purchase orders", payload["job_description"])

    def test_created_application_persists_unconfirmed_application_requirements(self):
        response = self.client.post("/applications", json={
            "company": "Example Department",
            "position_title": "Policy Officer",
            "job_description": "Submit your CV and a cover letter addressing the following three criteria, maximum two pages.",
        })

        self.assertEqual(response.status_code, 200)
        requirements = json.loads(response.json()["application_requirements_json"])
        self.assertEqual(requirements["review_status"], "needs_confirmation")
        self.assertEqual(requirements["source"], "deterministic_parser")
        self.assertEqual(requirements["documents"]["selection_criteria"]["format"], "embedded_in_cover_letter")

    def test_parse_ad_response_serialises_application_requirements_without_routing(self):
        raw_text = """Position title: Project Officer
Organisation: Example Department
How to apply
Submit your CV and a cover letter addressing the following three criteria, maximum two pages.
1. Project delivery.
2. Stakeholder engagement.
3. Written communication.
This role coordinates projects, prepares reports and supports public-sector stakeholders.
"""

        response = self.client.post("/applications/parse-ad", json={"raw_text": raw_text})

        self.assertEqual(response.status_code, 200)
        requirements = response.json()["application_requirements"]
        self.assertEqual(requirements["documents"]["cover_letter"]["limit"]["unit"], "pages")
        self.assertEqual(requirements["documents"]["selection_criteria"]["criteria_count"], 3)
        self.assertEqual(requirements["review_status"], "needs_confirmation")

    def test_application_requirements_confirm_preserves_parsed_content(self):
        parsed = parse_application_requirements("Submit your CV and a cover letter with a maximum 2 pages.")
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.application_requirements_json = json.dumps(parsed)
            session.commit()

        response = self.client.patch(
            f"/applications/{self.application_id}/application-requirements",
            json={"action": "confirm"},
        )

        self.assertEqual(response.status_code, 200)
        confirmed = response.json()["requirements"]
        self.assertEqual(confirmed["review_status"], "confirmed")
        for field in ("documents", "additional_documents", "source", "source_text", "source_excerpt", "warnings"):
            self.assertEqual(confirmed[field], parsed[field])

    def test_application_requirements_correction_is_user_overridden_and_preserves_provenance(self):
        parsed = parse_application_requirements("Submit your CV. A cover letter is optional.")
        corrected_documents = json.loads(json.dumps(parsed["documents"]))
        corrected_documents["cover_letter"].update(requirement="required", format="standalone")
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.application_requirements_json = json.dumps(parsed)
            session.commit()

        response = self.client.patch(
            f"/applications/{self.application_id}/application-requirements",
            json={"action": "correct", "documents": corrected_documents, "additional_documents": ["portfolio"]},
        )

        self.assertEqual(response.status_code, 200)
        corrected = response.json()["requirements"]
        self.assertEqual(corrected["review_status"], "user_overridden")
        self.assertEqual(corrected["source"], parsed["source"])
        self.assertEqual(corrected["source_text"], parsed["source_text"])
        self.assertEqual(corrected["source_excerpt"], parsed["source_excerpt"])
        self.assertEqual(corrected["warnings"], parsed["warnings"])
        self.assertEqual(corrected["additional_documents"], ["portfolio"])

    def test_invalid_application_requirements_correction_is_rejected_without_database_change(self):
        parsed = parse_application_requirements("Submit your CV and cover letter.")
        invalid_documents = json.loads(json.dumps(parsed["documents"]))
        invalid_documents["selection_criteria"].update(requirement="not_required", format="standalone")
        original = json.dumps(parsed)
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.application_requirements_json = original
            session.commit()

        response = self.client.patch(
            f"/applications/{self.application_id}/application-requirements",
            json={"action": "correct", "documents": invalid_documents},
        )

        self.assertEqual(response.status_code, 422)
        with Session(self.engine) as session:
            self.assertEqual(session.get(JobApplication, self.application_id).application_requirements_json, original)

    def test_frontend_cannot_overwrite_application_requirements_provenance(self):
        response = self.client.patch(
            f"/applications/{self.application_id}/application-requirements",
            json={"action": "confirm", "source": "user_supplied"},
        )

        self.assertEqual(response.status_code, 422)

    def test_confirmed_requirements_survive_unrelated_and_full_form_unchanged_updates(self):
        parsed = parse_application_requirements("Coordinate projects.")
        parsed["review_status"] = "confirmed"
        stored = json.dumps(parsed)
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.application_requirements_json = stored
            session.commit()

        unrelated = self.client.patch(
            f"/applications/{self.application_id}",
            json={"company": "Renamed Example Agency", "job_url": "https://example.com/new"},
        )
        unchanged = self.client.patch(
            f"/applications/{self.application_id}",
            json={"job_description": "  Coordinate   projects. \r\n", "selection_criteria": ""},
        )

        self.assertEqual(unrelated.status_code, 200)
        self.assertEqual(unchanged.status_code, 200)
        self.assertEqual(json.loads(unchanged.json()["application_requirements_json"])["review_status"], "confirmed")

    def test_user_overridden_requirements_survive_unrelated_update(self):
        model = empty_application_requirements("Coordinate projects.")
        model["review_status"] = "user_overridden"
        model["documents"]["resume"].update(requirement="optional", format="standalone")
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.application_requirements_json = json.dumps(model)
            session.commit()

        response = self.client.patch(
            f"/applications/{self.application_id}",
            json={"position_title": "Senior Project Officer", "deadline": "2026-12-01"},
        )

        self.assertEqual(response.status_code, 200)
        retained = json.loads(response.json()["application_requirements_json"])
        self.assertEqual(retained["review_status"], "user_overridden")
        self.assertEqual(retained["documents"]["resume"]["requirement"], "optional")

    def test_material_job_description_or_selection_criteria_change_requires_confirmation(self):
        for changed_payload in (
            {"job_description": "Coordinate projects and prepare cabinet submissions."},
            {"selection_criteria": "Submit a separate response to the selection criteria."},
        ):
            with self.subTest(changed_payload=changed_payload), Session(self.engine) as session:
                application = session.get(JobApplication, self.application_id)
                model = empty_application_requirements("Original source")
                model["review_status"] = "user_overridden"
                application.application_requirements_json = json.dumps(model)
                session.commit()

            response = self.client.patch(f"/applications/{self.application_id}", json=changed_payload)

            self.assertEqual(response.status_code, 200)
            reparsed = json.loads(response.json()["application_requirements_json"])
            self.assertEqual(reparsed["review_status"], "needs_confirmation")
            self.assertEqual(reparsed["source"], "deterministic_parser")

    def test_legacy_requirements_get_and_confirm_preserve_legacy_source(self):
        response = self.client.get(f"/applications/{self.application_id}/application-requirements")
        legacy = response.json()["requirements"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(legacy["source"], "legacy_inference")
        self.assertEqual(legacy["review_status"], "needs_confirmation")

        confirmed_response = self.client.patch(
            f"/applications/{self.application_id}/application-requirements", json={"action": "confirm"}
        )
        confirmed = confirmed_response.json()["requirements"]
        self.assertEqual(confirmed["source"], "legacy_inference")
        self.assertEqual(confirmed["review_status"], "confirmed")

    def test_unknown_and_all_limit_units_survive_correction_round_trip(self):
        for unit, value in (("pages", 2), ("words", 500), ("characters", 3000)):
            with self.subTest(unit=unit):
                current = empty_application_requirements("Application instructions unavailable.")
                documents = json.loads(json.dumps(current["documents"]))
                documents["cover_letter"]["limit"] = {
                    "value": value, "unit": unit, "scope": "document",
                    "constraint": "maximum", "source_text": f"maximum {value} {unit}",
                }
                with Session(self.engine) as session:
                    application = session.get(JobApplication, self.application_id)
                    application.application_requirements_json = json.dumps(current)
                    session.commit()

                saved = self.client.patch(
                    f"/applications/{self.application_id}/application-requirements",
                    json={"action": "correct", "documents": documents},
                ).json()["requirements"]

                self.assertEqual(saved["documents"]["resume"]["requirement"], "unknown")
                self.assertEqual(saved["documents"]["cover_letter"]["limit"]["unit"], unit)
                loaded = self.client.get(f"/applications/{self.application_id}/application-requirements").json()["requirements"]
                self.assertEqual(loaded["documents"]["cover_letter"]["limit"], documents["cover_letter"]["limit"])

    def test_application_requirements_endpoints_enforce_ownership(self):
        owner_id = uuid4()
        other_user_id = uuid4()
        with Session(self.engine) as session:
            owned = JobApplication(
                user_id=owner_id, company="Private", position_title="Private Role",
                job_description="Private instructions",
            )
            session.add(owned)
            session.commit()
            session.refresh(owned)
            owned_id = owned.id
        app.dependency_overrides[get_current_user] = lambda: other_user_id

        self.assertEqual(self.client.get(f"/applications/{owned_id}/application-requirements").status_code, 404)
        self.assertEqual(self.client.patch(
            f"/applications/{owned_id}/application-requirements", json={"action": "confirm"}
        ).status_code, 404)

    def test_rejects_blank_required_job_details(self):
        response = self.client.patch(
            f"/applications/{self.application_id}",
            json={"company": "   "},
        )

        self.assertEqual(response.status_code, 400)

    def test_applied_status_records_date_without_confirmation_reference(self):
        with Session(self.engine) as session:
            session.get(JobApplication, self.application_id).status = "ready_to_apply"; session.commit()
        response = self.client.patch(
            f"/applications/{self.application_id}/status",
            json={"status": "applied"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "applied")
        self.assertIsNotNone(response.json()["submitted_at"])

    def test_backup_restores_previous_application_data(self):
        created = self.client.post("/backups")
        filename = created.json()["filename"]
        self.client.patch(
            f"/applications/{self.application_id}/status",
            json={"status": "ready_to_apply"},
        )

        restored = self.client.post(
            f"/backups/{filename}/restore",
            json={"confirm": True},
        )
        applications = self.client.get("/applications").json()

        self.assertEqual(created.status_code, 200)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(applications[0]["status"], "draft")

    def test_backup_restore_requires_explicit_confirmation(self):
        filename = self.client.post("/backups").json()["filename"]

        response = self.client.post(
            f"/backups/{filename}/restore",
            json={"confirm": False},
        )

        self.assertEqual(response.status_code, 400)

    def test_saves_and_reloads_local_applicant_profile(self):
        payload = {
            "title": "Ms",
            "first_name": "Hua",
            "last_name": "Zhong",
            "phone": "0400000000",
            "email": "applicant@example.com",
            "work_rights": "permanent_resident",
            "availability_notice": "two_weeks",
            "referees": [
                {
                    "organisation": "Example Agency",
                    "name": "Example Manager",
                    "position_title": "Manager",
                    "phone": "0400000000",
                    "relationship": "Direct Manager",
                    "email": "manager@example.com",
                }
            ],
        }

        saved = self.client.put("/profile", json=payload)
        loaded = self.client.get("/profile")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["first_name"], "Hua")
        self.assertEqual(loaded.json()["availability_notice"], "two_weeks")
        self.assertEqual(len(loaded.json()["referees"]), 1)

    def test_quality_check_passes_consistent_application_pack(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.position_title = "Senior Project Officer - Level 5"
            session.add(ApplicantProfile(
                first_name="Alex",
                last_name="Morgan",
                phone="0400000000",
                email="applicant@example.com",
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan 0400000000 applicant@example.com", reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Alex Morgan\nApplication for Senior Project Officer\n0400000000 applicant@example.com", reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content="Evidence-based responses."),
            ])
            self._confirm_quality_contract(session)
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])

    def test_quality_check_summarises_remaining_resume_source_gaps(self):
        with Session(self.engine) as session:
            session.add(ApplicantProfile(
                first_name="Alex", last_name="Morgan", phone="0400000000", email="applicant@example.com"
            ))
            session.add_all([
                GeneratedDocument(
                    application_id=self.application_id,
                    document_type="tailored_resume",
                    content="Alex Morgan 0400000000 applicant@example.com",
                    reviewer_json='{"status":"fail","generation_status":"needs_ckb_update","remaining_issues":[{"claim":"Advanced Excel"},{"claim":"10+ years"}],"results":[]}',
                ),
                GeneratedDocument(
                    application_id=self.application_id,
                    document_type="cover_letter",
                    content="Application for Project Officer at Example Agency 0400000000 applicant@example.com",
                ),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")
        source_gap_issues = [item for item in response.json()["issues"] if item["code"] == "resume_needs_ckb_update"]

        self.assertEqual(len(source_gap_issues), 1)
        self.assertIn("Advanced Excel", source_gap_issues[0]["message"])

    def test_editing_document_clears_stale_reviewer_findings(self):
        with Session(self.engine) as session:
            document = GeneratedDocument(
                application_id=self.application_id,
                document_type="cover_letter",
                content="Original generated draft",
                reviewer_json='{"status":"fail","results":[{"issues":[{"type":"unsupported_claim","description":"Old finding"}]}]}',
            )
            session.add(document)
            session.commit()
            session.refresh(document)
            document_id = document.id

        response = self.client.patch(
            f"/documents/{document_id}",
            json={"content": "Applicant-reviewed draft"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reviewer_json"], "{}")

    def test_editing_selection_criteria_clears_saved_confirmations_and_blocks_submission(self):
        with Session(self.engine) as session:
            application = self._confirm_quality_contract(session, required=("resume", "cover_letter", "selection_criteria"))
            application.job_url = "https://example.com/apply"
            application.selection_plan_json = '{"items":[{"criteria_id":"C1","evidence_status":"weak"}]}'
            application.selection_confirmations_json = '["C1"]'
            document = GeneratedDocument(
                application_id=self.application_id,
                document_type="selection_criteria",
                content="Original response.",
            )
            session.add(application)
            session.add(document)
            session.commit()
            session.refresh(document)
            document_id = document.id

        edited = self.client.patch(
            f"/documents/{document_id}",
            json={"content": "Manually edited response."},
        )
        after_edit = self.client.post(f"/applications/{self.application_id}/prepare-submission")

        self.assertEqual(edited.status_code, 200)
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            self.assertEqual(application.selection_confirmations_json, "[]")
        self.assertEqual(after_edit.status_code, 409)
        self.assertFalse(after_edit.json()["detail"]["checklist"]["checks"]["selection_confirmations"]["ready"])

    def test_editing_non_selection_document_preserves_saved_confirmations(self):
        for document_type in ("tailored_resume", "cover_letter"):
            with self.subTest(document_type=document_type), Session(self.engine) as session:
                application = session.get(JobApplication, self.application_id)
                application.selection_confirmations_json = '["C1"]'
                document = GeneratedDocument(
                    application_id=self.application_id,
                    document_type=document_type,
                    content="Original content.",
                )
                session.add(application)
                session.add(document)
                session.commit()
                session.refresh(document)
                document_id = document.id

            response = self.client.patch(
                f"/documents/{document_id}",
                json={"content": "Manually edited content."},
            )

            self.assertEqual(response.status_code, 200)
            with Session(self.engine) as session:
                application = session.get(JobApplication, self.application_id)
                self.assertEqual(application.selection_confirmations_json, '["C1"]')

    def test_private_job_does_not_require_selection_criteria(self):
        with Session(self.engine) as session:
            session.add(ApplicantProfile(
                first_name="Alex", last_name="Morgan", phone="0400000000", email="applicant@example.com"
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan 0400000000 applicant@example.com", reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Alex Morgan\nApplication for Project Officer at Example Agency\n0400000000 applicant@example.com " + "evidence " * 230, reviewer_json='{"status":"pass"}'),
            ])
            self._confirm_quality_contract(session)
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertTrue(response.json()["ready"])
        self.assertNotIn("missing_document", [issue["code"] for issue in response.json()["issues"]])

    def test_selection_criteria_is_required_when_job_supplies_criteria(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.selection_criteria = "Demonstrated project coordination experience."
            self._confirm_quality_contract(session, required=("resume", "cover_letter", "selection_criteria"))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency"),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertFalse(response.json()["ready"])
        self.assertTrue(any(issue["code"] == "missing_document" and issue["document_type"] == "selection_criteria" for issue in response.json()["issues"]))

    def test_manual_edit_requires_a_new_document_review(self):
        with Session(self.engine) as session:
            self._confirm_quality_contract(session)
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan 0400000000 applicant@example.com", reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Alex Morgan Project Officer Example Agency 0400000000 applicant@example.com", reviewer_json='{"status":"pass"}'),
            ])
            session.commit()
            cover = session.exec(select(GeneratedDocument).where(GeneratedDocument.document_type == "cover_letter")).first()
            cover_id = cover.id
        self.client.patch(f"/documents/{cover_id}", json={"content": "Alex Morgan Project Officer Example Agency 0400000000 applicant@example.com edited"})
        issues = self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]
        self.assertTrue(any(item["code"] == "document_review_required" and item["document_type"] == "cover_letter" for item in issues))

    def test_quality_check_blocks_stale_decision_and_incomplete_requirements(self):
        with Session(self.engine) as session:
            application = self._confirm_quality_contract(session)
            requirements = json.loads(application.application_requirements_json)
            requirements["completeness"] = "incomplete"
            application.application_requirements_json = json.dumps(requirements)
            session.commit()
        codes = [item["code"] for item in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertIn("employer_requirements_incomplete", codes)
        self.assertIn("application_decision_stale", codes)
        payload = self.client.get(f"/applications/{self.application_id}/quality-check").json()
        self.assertTrue(all(item["blocks_release"] for item in payload["issues"] if item["severity"] == "error"))

    def test_quality_check_blocks_missing_name_email_and_phone(self):
        with Session(self.engine) as session:
            self._confirm_quality_contract(session)
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Professional Summary", reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency", reviewer_json='{"status":"pass"}'),
            ])
            session.commit()
        codes = [item["code"] for item in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertIn("name_mismatch", codes)
        self.assertIn("email_mismatch", codes)
        self.assertIn("phone_mismatch", codes)

    def test_quality_check_blocks_final_evidence_outside_each_document_plan(self):
        with Session(self.engine) as session:
            self._confirm_quality_contract(session)
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan 0400000000 applicant@example.com", reviewer_json='{"status":"pass"}', structured_content_json='{"selected_evidence":[{"evidence_id":"E1"}]}', used_experiences_json='["E2"]'),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Alex Morgan Project Officer Example Agency 0400000000 applicant@example.com", reviewer_json='{"status":"pass"}', structured_content_json='{"selected_evidence":[{"evidence_id":"E1"}]}', used_experiences_json='["E2"]'),
            ])
            session.commit()
        codes = [item["code"] for item in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertIn("tailored_resume_unselected_evidence", codes)
        self.assertIn("cover_letter_unselected_evidence", codes)

    def test_quality_check_blocks_selection_evidence_outside_persisted_applied_plan(self):
        with Session(self.engine) as session:
            self._confirm_quality_contract(session, required=("selection_criteria",))
            bundle = {
                "selection_plan": {"items": [{"criteria_id": "C1", "matched_evidence": ["E1"]}]},
                "responses": [{"criteria_id": "C1", "evidence_used": ["E2"]}],
            }
            session.add(GeneratedDocument(
                application_id=self.application_id, document_type="selection_criteria",
                content="Criterion response.", reviewer_json='{"status":"pass"}',
                structured_content_json=json.dumps(bundle), used_experiences_json='["E2"]',
            ))
            session.commit()
        codes = [item["code"] for item in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertIn("selection_criteria_unselected_evidence", codes)

    def test_private_unknown_requirements_do_not_invent_government_documents(self):
        with Session(self.engine) as session:
            application = self._confirm_quality_contract(session, required=())
            requirements = json.loads(application.application_requirements_json)
            for document in requirements["documents"].values():
                document.update(requirement="unknown", format="unknown")
            application.application_requirements_json = json.dumps(requirements)
            profile = session.exec(select(ApplicantProfile).order_by(ApplicantProfile.id)).first()
            resume = session.exec(select(Resume).order_by(Resume.updated_at.desc())).first()
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs({}, requirements, json.loads(resume.ckb_json), profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.commit()
        payload = self.client.get(f"/applications/{self.application_id}/quality-check").json()
        self.assertFalse(payload["ready"])
        self.assertIn("application_requirements_unknown", [item["code"] for item in payload["issues"]])
        self.assertNotIn("missing_document", [item["code"] for item in payload["issues"]])

    def test_quality_check_warns_about_generic_wording_and_us_spelling(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="An organized administrator"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency. I am confident I can contribute. " + "evidence " * 230),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        codes = [issue["code"] for issue in response.json()["issues"]]
        self.assertIn("generic_ai_wording", codes)
        self.assertIn("american_spelling", codes)

    def test_quality_check_warns_about_unsupported_project_comparison(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(
                    application_id=self.application_id,
                    document_type="cover_letter",
                    content=(
                        "Application for Project Officer at Example Agency. "
                        "I supported projects with a value and complexity comparable to your projects."
                    ),
                ),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertIn("unsupported_project_comparison", [issue["code"] for issue in response.json()["issues"]])

    def test_quality_check_warns_when_job_duty_is_presented_as_direct_experience(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(
                    application_id=self.application_id,
                    document_type="cover_letter",
                    content=(
                        "Application for Project Officer at Example Agency. "
                        "My attention to detail will translate directly to managing purchase orders."
                    ),
                ),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertIn("jd_duty_as_experience", [issue["code"] for issue in response.json()["issues"]])

    def test_quality_check_ignores_phone_spacing_and_country_code_format(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.position_title = "Project Officer"
            session.add(ApplicantProfile(
                first_name="Alex",
                last_name="Morgan",
                phone="0400 000 000",
                email="applicant@example.com",
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan +61 400 000 000 applicant@example.com"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer\n0400-000-000 applicant@example.com"),
                GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content="Evidence-based responses."),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("phone_mismatch", [issue["code"] for issue in response.json()["issues"]])

    def test_quality_check_blocks_wrong_position_title(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.position_title = "Senior Project Officer"
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for an unrelated role"),
                GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content="Responses"),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ready"])
        self.assertIn("position_title_mismatch", [issue["code"] for issue in response.json()["issues"]])

    def test_quality_check_warns_when_advertised_company_is_missing_from_cover_letter(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with the client organisation"),
                GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content="Responses"),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        issues = response.json()["issues"]
        self.assertIn("advertised_company_missing", [issue["code"] for issue in issues])
        self.assertEqual(next(issue["severity"] for issue in issues if issue["code"] == "advertised_company_missing"), "warning")

    def test_selection_criteria_does_not_require_current_job_identity(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.selection_criteria = "Demonstrated stakeholder communication."
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with Example Agency"),
                GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content="I prepared reports and liaised with stakeholders."),
            ])
            session.commit()

        issues = self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]
        selection_identity_issues = [
            issue for issue in issues
            if issue.get("document_type") == "selection_criteria"
            and issue["code"] in {"position_title_mismatch", "advertised_company_missing"}
        ]
        self.assertEqual(selection_identity_issues, [])

    def test_cross_application_identity_pair_blocks_cover_letter_and_selection_criteria(self):
        for document_type in ("cover_letter", "selection_criteria"):
            with self.subTest(document_type=document_type), Session(self.engine) as session:
                application = session.get(JobApplication, self.application_id)
                application.selection_criteria = "Criteria required."
                other = JobApplication(
                    company="Transwa Rail Services",
                    position_title="Transport Planning Officer",
                    job_description="Plan regional rail services.",
                )
                session.add(other)
                session.add_all([
                    GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                    GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content=(
                        "Application for Project Officer with Example Agency."
                        if document_type != "cover_letter" else
                        "Application for Project Officer with Example Agency. Transwa Rail Services Transport Planning Officer."
                    )),
                    GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content=(
                        "Transwa Rail Services Transport Planning Officer experience."
                        if document_type == "selection_criteria" else "Evidence-based response."
                    )),
                ])
                session.commit()

            issues = self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]
            leak_issues = [issue for issue in issues if issue["code"] == "cross_application_content_leak"]
            self.assertTrue(any(issue["document_type"] == document_type and issue["severity"] == "error" for issue in leak_issues))

    def test_cross_application_guard_ignores_single_organisation_mentions(self):
        with Session(self.engine) as session:
            session.add(JobApplication(
                company="Former Community Services",
                position_title="Program Coordinator",
                job_description="Coordinate programs.",
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with Example Agency. Previously employed by Former Community Services."),
            ])
            session.commit()

        codes = [issue["code"] for issue in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertNotIn("cross_application_content_leak", codes)

    def test_cross_application_guard_allows_pair_supported_by_master_resume(self):
        with Session(self.engine) as session:
            session.add(Resume(
                source_text="Program Coordinator, Former Community Services. Coordinated community programs.",
                ckb_json="[]",
            ))
            session.add(JobApplication(
                company="Former Community Services",
                position_title="Program Coordinator",
                job_description="Coordinate programs.",
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with Example Agency. As Program Coordinator at Former Community Services, I prepared reports."),
            ])
            session.commit()

        codes = [issue["code"] for issue in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertNotIn("cross_application_content_leak", codes)

    def test_cross_application_guard_allows_pair_named_in_current_jd(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.job_description = "Work with Transwa Rail Services and its Transport Planning Officer."
            session.add(application)
            session.add(JobApplication(
                company="Transwa Rail Services",
                position_title="Transport Planning Officer",
                job_description="Plan regional rail services.",
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with Example Agency. Liaise with Transwa Rail Services and the Transport Planning Officer."),
            ])
            session.commit()

        codes = [issue["code"] for issue in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertNotIn("cross_application_content_leak", codes)

    def test_cross_application_guard_ignores_equivalent_or_weak_organisation_names(self):
        cases = (
            ("The Example Agency Pty Ltd", "Senior Project Officer"),
            ("Health", "Project Officer"),
        )
        for company, title in cases:
            with self.subTest(company=company), Session(self.engine) as session:
                session.add(JobApplication(company=company, position_title=title, job_description="Other job."))
                session.add_all([
                    GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                    GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content=f"Application for Project Officer with Example Agency. {company} {title}."),
                ])
                session.commit()

            codes = [issue["code"] for issue in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
            self.assertNotIn("cross_application_content_leak", codes)

    def test_cross_application_guard_does_not_run_on_tailored_resume(self):
        with Session(self.engine) as session:
            session.add(JobApplication(
                company="Former Community Services",
                position_title="Program Coordinator",
                job_description="Coordinate programs.",
            ))
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Program Coordinator at Former Community Services."),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with Example Agency."),
            ])
            session.commit()

        codes = [issue["code"] for issue in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertNotIn("cross_application_content_leak", codes)

    def test_quality_check_warns_about_self_deprecating_wording_in_generated_prose(self):
        for document_type in ("cover_letter", "selection_criteria"):
            with self.subTest(document_type=document_type), Session(self.engine) as session:
                application = session.get(JobApplication, self.application_id)
                application.selection_criteria = "Criteria required."
                session.add_all([
                    GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                    GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content=(
                        "Application for Project Officer with Example Agency. Although I have not held the identical role, I prepared reports."
                        if document_type == "cover_letter" else "Application for Project Officer with Example Agency."
                    )),
                    GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content=(
                        "While I have not worked in that system, I prepared reports."
                        if document_type == "selection_criteria" else "Evidence-based response."
                    )),
                ])
                session.commit()

            issues = self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]
            warnings = [issue for issue in issues if issue["code"] == "self_deprecating_wording"]
            self.assertTrue(any(issue["document_type"] == document_type and issue["severity"] == "warning" for issue in warnings))

    def test_positive_transferable_wording_is_not_self_deprecating(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer with Example Agency. My reporting experience provides transferable evidence for this requirement."),
            ])
            session.commit()

        codes = [issue["code"] for issue in self.client.get(f"/applications/{self.application_id}/quality-check").json()["issues"]]
        self.assertNotIn("self_deprecating_wording", codes)

    def test_quality_check_warns_about_speculative_employer_relationship(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(
                    application_id=self.application_id,
                    document_type="cover_letter",
                    content="Application for Project Officer at Example Agency. Example Agency may be recruiting on behalf of its clients.",
                ),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertIn("speculative_employer_relationship", [issue["code"] for issue in response.json()["issues"]])

    def test_quality_check_warns_about_generic_salutation_with_sincere_signoff(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(
                    application_id=self.application_id,
                    document_type="cover_letter",
                    content="Dear Hiring Manager\nApplication for Project Officer at Example Agency.\nYours sincerely",
                ),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertIn("salutation_signoff_mismatch", [issue["code"] for issue in response.json()["issues"]])


if __name__ == "__main__":
    unittest.main()
