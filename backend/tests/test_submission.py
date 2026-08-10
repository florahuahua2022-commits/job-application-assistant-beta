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
from app.main import app, auto_polish_cover_letter, auto_polish_tailored_resume, build_resume_content_check, document_release_blockers, enforce_profile_contact, generation_context_fingerprint, organisation_is_named
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
            availability_confirmed=True,
        )

        polished = auto_polish_cover_letter(
            "I am available to commence mid-September. I hold permanent residency.",
            profile,
        )

        self.assertIn("I am available following one month's notice.", polished)
        self.assertNotIn("mid-September", polished)

    def test_release_blocker_rejects_unconfirmed_residency_and_notice_period(self):
        profile = ApplicantProfile(
            first_name="Alex", last_name="Morgan", phone="0400000000",
            email="applicant@example.com", work_rights="not_specified",
            availability_notice="not_specified",
        )
        application = JobApplication(
            company="DTMI", position_title="Project Officer",
            job_description="Project Officer job description",
        )
        document = GeneratedDocument(
            application_id=1,
            document_type="cover_letter",
            content=(
                "Application for Project Officer at DTMI. "
                "I hold permanent residency and am available following one month's notice."
            ),
            reviewer_json='{"status":"pass","results":[]}',
        )

        blockers = document_release_blockers(document, application, profile)

        self.assertTrue(any("work rights" in blocker for blocker in blockers))
        self.assertTrue(any("availability" in blocker for blocker in blockers))

    def test_release_blocker_rejects_failed_reviewer_and_wrong_title(self):
        application = JobApplication(
            company="DTMI", position_title="Project Officer",
            job_description="Project Officer job description",
        )
        document = GeneratedDocument(
            application_id=1,
            document_type="cover_letter",
            content="Application for an unrelated role.",
            reviewer_json='{"status":"fail","results":[]}',
        )

        blockers = document_release_blockers(document, application, None)

        self.assertTrue(any("reviewer" in blocker for blocker in blockers))
        self.assertTrue(any("position title" in blocker for blocker in blockers))

    def test_generation_context_fingerprint_changes_with_each_fact_source(self):
        application = JobApplication(company="DTMI", position_title="Project Officer", job_description="Original JD")
        resume = Resume(source_text="Original resume", experiences_json="[]", ckb_json="[]")
        profile = ApplicantProfile(
            first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com",
        )
        original = generation_context_fingerprint(application, resume, profile)

        application.job_description = "Changed JD"
        changed_job = generation_context_fingerprint(application, resume, profile)
        application.job_description = "Original JD"
        resume.source_text = "Changed resume"
        changed_resume = generation_context_fingerprint(application, resume, profile)
        resume.source_text = "Original resume"
        profile.phone = "0411111111"
        changed_profile = generation_context_fingerprint(application, resume, profile)

        self.assertEqual(len(original), 64)
        self.assertEqual(len({original, changed_job, changed_resume, changed_profile}), 4)

    def test_release_blocker_rejects_stale_generation_context(self):
        application = JobApplication(company="DTMI", position_title="Project Officer", job_description="JD")
        document = GeneratedDocument(
            application_id=1, document_type="cover_letter",
            content="Application for Project Officer", reviewer_json='{"status":"pass"}',
            context_fingerprint="old-context",
        )

        blockers = document_release_blockers(document, application, None, "current-context")

        self.assertTrue(any("changed after" in blocker for blocker in blockers))

    def test_release_blocker_rejects_fact_boundary_issue_even_when_reviewer_passed(self):
        application = JobApplication(company="DTMI", position_title="Project Officer", job_description="JD")
        document = GeneratedDocument(
            application_id=1,
            document_type="cover_letter",
            content="Application for Project Officer. What draws me to this position is public service.",
            reviewer_json='{"status":"pass","results":[]}',
        )

        blockers = document_release_blockers(
            document,
            application,
            ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com"),
            evidence_text="Project support experience.",
        )

        self.assertTrue(any("personal motivation" in blocker for blocker in blockers))

    def test_release_blocker_rejects_dtmi_multi_phrase_jd_copy(self):
        application = JobApplication(
            company="DTMI",
            position_title="Project Officer",
            job_description=(
                "Maintain project documentation, contracts and technical records. "
                "Manage competing priorities across multiple concurrent projects."
            ),
        )
        document = GeneratedDocument(
            application_id=1,
            document_type="cover_letter",
            content=(
                "Application for Project Officer. I maintained project documentation, contracts and technical records "
                "while managing competing priorities across multiple concurrent projects."
            ),
            reviewer_json='{"status":"pass","results":[]}',
        )

        blockers = document_release_blockers(document, application, None)

        self.assertTrue(any("distinct JD phrases" in blocker for blocker in blockers))

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

    def test_missing_contact_is_added_to_generated_resume(self):
        profile = ApplicantProfile(
            first_name="Alex",
            last_name="Morgan",
            phone="0400 000 000",
            email="correct@example.com",
        )

        corrected = enforce_profile_contact("Alex Morgan\nProfessional Summary", profile, "tailored_resume")

        self.assertTrue(corrected.startswith("Phone: 0400 000 000\nEmail: correct@example.com"))

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
            session.add_all([
                application,
                Resume(title="Master Resume", source_text="Alex Morgan\nProject support experience.", experiences_json="[]", ckb_json="[]"),
            ])
            session.commit()
            session.refresh(application)
            self.application_id = application.id
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.backup_patch.stop()
        self.backup_directory.cleanup()

    def test_records_confirmation_and_marks_application_applied(self):
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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready_to_apply")

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

    def test_rejects_blank_required_job_details(self):
        response = self.client.patch(
            f"/applications/{self.application_id}",
            json={"company": "   "},
        )

        self.assertEqual(response.status_code, 400)

    def test_applied_status_records_date_without_confirmation_reference(self):
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
            "work_rights_confirmed": True,
            "availability_notice": "two_weeks",
            "availability_confirmed": True,
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
        self.assertTrue(loaded.json()["work_rights_confirmed"])
        self.assertTrue(loaded.json()["availability_confirmed"])
        self.assertEqual(len(loaded.json()["referees"]), 1)

    def test_profile_save_does_not_confirm_sensitive_values_implicitly(self):
        payload = {
            "first_name": "Hua",
            "last_name": "Zhong",
            "phone": "0400000000",
            "email": "applicant@example.com",
            "work_rights": "permanent_resident",
            "availability_notice": "one_month",
            "motivation": "I want to contribute to public services.",
            "referees": [],
        }

        response = self.client.put("/profile", json=payload)

        self.assertEqual(response.status_code, 200)
        saved = response.json()
        self.assertEqual(saved["work_rights"], "not_specified")
        self.assertFalse(saved["work_rights_confirmed"])
        self.assertEqual(saved["availability_notice"], "not_specified")
        self.assertFalse(saved["availability_confirmed"])
        self.assertIsNone(saved["motivation"])
        self.assertFalse(saved["motivation_confirmed"])

    def test_quality_check_passes_consistent_application_pack(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.position_title = "Senior Project Officer - Level 5"
            profile = ApplicantProfile(
                first_name="Alex",
                last_name="Morgan",
                phone="0400000000",
                email="applicant@example.com",
            )
            session.add(profile)
            session.flush()
            resume = session.exec(select(Resume)).first()
            context = generation_context_fingerprint(application, resume, profile)
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan 0400000000 applicant@example.com", reviewer_json='{"status":"pass","results":[]}', context_fingerprint=context),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Senior Project Officer\n0400000000 applicant@example.com", reviewer_json='{"status":"pass","results":[]}', context_fingerprint=context),
                GeneratedDocument(application_id=self.application_id, document_type="selection_criteria", content="Evidence-based responses.", reviewer_json='{"status":"pass","results":[]}', context_fingerprint=context),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])

    def test_private_job_does_not_require_selection_criteria(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            profile = ApplicantProfile(
                first_name="Alex", last_name="Morgan", phone="0400000000", email="applicant@example.com"
            )
            session.add(profile)
            session.flush()
            resume = session.exec(select(Resume)).first()
            context = generation_context_fingerprint(application, resume, profile)
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Alex Morgan 0400000000 applicant@example.com", reviewer_json='{"status":"pass","results":[]}', context_fingerprint=context),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency\n0400000000 applicant@example.com " + "evidence " * 230, reviewer_json='{"status":"pass","results":[]}', context_fingerprint=context),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertTrue(response.json()["ready"])
        self.assertNotIn("missing_document", [issue["code"] for issue in response.json()["issues"]])

    def test_quality_check_fails_closed_when_reviewer_is_unavailable(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency"),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertFalse(response.json()["ready"])
        unavailable = [issue for issue in response.json()["issues"] if issue["code"] == "reviewer_unavailable"]
        self.assertEqual(len(unavailable), 2)
        self.assertTrue(all(issue["severity"] == "error" for issue in unavailable))

    def test_quality_check_reports_invalid_reviewer_as_system_failure(self):
        with Session(self.engine) as session:
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume", reviewer_json="not-json"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency", reviewer_json='{"status":"pass","results":[]}'),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        invalid = [issue for issue in response.json()["issues"] if issue["code"] == "reviewer_invalid_result"]
        self.assertEqual(len(invalid), 1)
        self.assertIn("No factual conclusion has been assumed", invalid[0]["message"])

    def test_final_check_gates_single_pack_and_prepare_then_edit_invalidates_it(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.job_url = "https://example.com/apply"
            resume = session.exec(select(Resume)).first()
            context = generation_context_fingerprint(application, resume, None)
            cv = GeneratedDocument(
                application_id=self.application_id,
                document_type="tailored_resume",
                content="## Professional Summary\nProject support.\n## Key Skills\nReporting\n## Work Experience\nProject support.",
                reviewer_json='{"status":"pass","results":[]}',
                context_fingerprint=context,
            )
            cover = GeneratedDocument(
                application_id=self.application_id,
                document_type="cover_letter",
                content="Application for Project Officer at Example Agency. " + "Project support evidence. " * 80,
                reviewer_json='{"status":"pass","results":[]}',
                context_fingerprint=context,
            )
            session.add_all([application, cv, cover])
            session.commit()
            session.refresh(cv)
            cv_id = cv.id

        before_check = self.client.get(f"/documents/{cv_id}/export?format=docx")
        check = self.client.get(f"/applications/{self.application_id}/quality-check")
        single = self.client.get(f"/documents/{cv_id}/export?format=docx")
        pack = self.client.get(f"/applications/{self.application_id}/export-pack?format=docx")
        prepare = self.client.post(f"/applications/{self.application_id}/prepare-submission")

        self.assertEqual(before_check.status_code, 409)
        self.assertIn("Run Final Check", before_check.json()["detail"])
        self.assertTrue(check.json()["ready"])
        self.assertEqual(single.status_code, 200)
        self.assertEqual(pack.status_code, 200)
        self.assertEqual(prepare.status_code, 200)

        edited = self.client.patch(f"/documents/{cv_id}", json={"content": "Edited project support."})
        stale_single = self.client.get(f"/documents/{cv_id}/export?format=pdf")
        stale_pack = self.client.get(f"/applications/{self.application_id}/export-pack?format=pdf")
        stale_prepare = self.client.post(f"/applications/{self.application_id}/prepare-submission")

        self.assertEqual(edited.status_code, 200)
        self.assertEqual(stale_single.status_code, 409)
        self.assertIn("changed after the last Final Check", stale_single.json()["detail"])
        self.assertEqual(stale_pack.status_code, 409)
        self.assertEqual(stale_prepare.status_code, 409)

    def test_job_change_marks_existing_document_stale_and_blocks_export(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            resume = session.exec(select(Resume)).first()
            profile = ApplicantProfile(
                first_name="Alex", last_name="Morgan", phone="0400000000", email="applicant@example.com",
            )
            session.add(profile)
            session.flush()
            context = generation_context_fingerprint(application, resume, profile)
            document = GeneratedDocument(
                application_id=self.application_id,
                document_type="cover_letter",
                content="Application for Project Officer at Example Agency",
                reviewer_json='{"status":"pass","results":[]}',
                context_fingerprint=context,
            )
            session.add(document)
            session.commit()
            session.refresh(document)
            document_id = document.id

        updated = self.client.patch(
            f"/applications/{self.application_id}",
            json={"job_description": "Changed project coordination duties."},
        )
        quality = self.client.get(f"/applications/{self.application_id}/quality-check")
        exported = self.client.get(f"/documents/{document_id}/export?format=docx")

        self.assertEqual(updated.status_code, 200)
        self.assertIn("stale_generation_context", [issue["code"] for issue in quality.json()["issues"]])
        self.assertEqual(exported.status_code, 409)
        self.assertIn("changed after", exported.json()["detail"])

    def test_selection_criteria_is_required_when_job_supplies_criteria(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.selection_criteria = "Demonstrated project coordination experience."
            session.add(application)
            session.add_all([
                GeneratedDocument(application_id=self.application_id, document_type="tailored_resume", content="Resume"),
                GeneratedDocument(application_id=self.application_id, document_type="cover_letter", content="Application for Project Officer at Example Agency"),
            ])
            session.commit()

        response = self.client.get(f"/applications/{self.application_id}/quality-check")

        self.assertFalse(response.json()["ready"])
        self.assertTrue(any(issue["code"] == "missing_document" and issue["document_type"] == "selection_criteria" for issue in response.json()["issues"]))

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
