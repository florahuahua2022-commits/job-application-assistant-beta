import json
import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.application_decision import decision_inputs
from app.ai import AIServiceError
from app.application_requirements import empty_application_requirements
from app.ckb import build_career_knowledge_base
from app.database import get_session
from app.main import app, get_or_refresh_current_ckb
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, JobSource, Resume
from app.outcome_learning import build_submission_snapshot
from app.release_state import details_fingerprint, fingerprint, generation_inputs_fingerprint, pack_fingerprint
from app.resume_plan import build_resume_curation_plan, validate_resume_content


class RealUserRegressionTests(unittest.TestCase):
    def test_bennco_stale_ckb_refreshes_once_and_enforces_all_five_periods(self):
        roles = [
            ("Finance Administration Officer", "Department of Communities – Disability Services", "Feb 2026", "Present"),
            ("Executive Assistant to Board Member", "Avaintec", "Nov 2017", "Jan 2019"),
            ("Project Administration Officer", "CCCC Kenya", "Jan 2016", "Aug 2017"),
            ("Project Administration Officer", "Chevron CDB Project", "Aug 2012", "Dec 2015"),
            ("Project Assistant", "Pratt & Whitney", "Oct 2007", "Aug 2012"),
        ]
        authoritative = [{
            "role_title": role, "organization": employer, "responsibility": f"Distinct grounded duty {index}.",
            "source_section": f"Work Experience > {employer} > {role}",
            "source_text": f"{role}\n{employer}\n{start} – {end}\nDistinct grounded duty {index}.",
        } for index, (role, employer, start, end) in enumerate(roles)]
        source_text = "Work Experience\n" + "\n".join(item["source_text"] for item in authoritative)
        experiences = json.loads(json.dumps(authoritative))
        for index, item in enumerate(experiences):
            if index in {1, 2}:
                item["time_period_text"] = f"{roles[index][2]} – {roles[index][3]}"
            else:
                item["source_text"] = f"{item['role_title']}\n{item['organization']}\n{item['responsibility']}"
        stale = build_career_knowledge_base(source_text, json.dumps(experiences))
        self.assertEqual(
            [item["time_period_status"] for item in stale],
            ["not_provided", "verified", "verified", "not_provided", "not_provided"],
        )

        with Session(self.engine) as session:
            resume = Resume(source_text=source_text, experiences_json=json.dumps(experiences), ckb_json=json.dumps(stale))
            application = JobApplication(company="Bennco", position_title="Office Administrator", job_description="Administration", application_decision_json='{"status":"ready"}')
            session.add_all([resume, application]); session.commit(); session.refresh(resume); session.refresh(application)
            refreshed, status = get_or_refresh_current_ckb(session, resume, None)
            self.assertEqual(application.application_decision_json, "{}")
            application.application_decision_json = '{"status":"sentinel"}'
            session.add(application); session.commit()
            reused, second_status = get_or_refresh_current_ckb(session, resume, None)
            session.refresh(application)

        self.assertEqual((status, second_status), ("refreshed_stale", "reused_current"))
        self.assertEqual(refreshed, reused)
        self.assertEqual(application.application_decision_json, '{"status":"sentinel"}')
        self.assertEqual(
            [(item["time_period"]["start"], item["time_period"]["end"], item["time_period_status"]) for item in refreshed],
            [(start, end, "verified") for _, _, start, end in roles],
        )
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": [item["evidence_id"] for item in refreshed], "match_type": "direct", "coverage": "strong"}]}
        plan = build_resume_curation_plan({"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}, matches, refreshed)
        self.assertEqual([item["display_period"] for item in plan["roles"]], [f"{start} - {end}" for _, _, start, end in roles])

        headers = [f"### {role}\n{employer}" for role, employer, _, _ in roles]
        incomplete = "## Professional Summary\nGrounded.\n## Key Skills\nAdministration.\n## Work Experience\n" + "\n".join(
            f"{header}\n{start} – {end}" if index == 2 else header
            for index, (header, (_, _, start, end)) in enumerate(zip(headers, roles))
        )
        missing = validate_resume_content(incomplete, plan, [item["evidence_id"] for item in refreshed])
        self.assertEqual([item["code"] for item in missing["issues"]].count("missing_role_period"), 4)
        for missing_index in range(len(roles)):
            individually_incomplete = "## Professional Summary\nGrounded.\n## Key Skills\nAdministration.\n## Work Experience\n" + "\n".join(
                header if index == missing_index else f"{header}\n{start} – {end}"
                for index, (header, (_, _, start, end)) in enumerate(zip(headers, roles))
            )
            result = validate_resume_content(individually_incomplete, plan, [item["evidence_id"] for item in refreshed])
            self.assertIn("missing_role_period", [item["code"] for item in result["issues"]])
        complete = "## Professional Summary\nGrounded.\n## Key Skills\nAdministration.\n## Work Experience\n" + "\n".join(
            f"{header}\n{start} – {end}" for header, (_, _, start, end) in zip(headers, roles)
        )
        self.assertTrue(validate_resume_content(complete, plan, [item["evidence_id"] for item in refreshed])["valid"])

    def test_current_empty_date_states_are_reused_without_rebuild(self):
        for status in ("uncertain", "not_provided"):
            with self.subTest(status=status), Session(self.engine) as session:
                ckb = [{"evidence_type": "experience", "time_period": {"start": None, "end": None}, "time_period_status": status}]
                resume = Resume(source_text="Authoritative source", experiences_json="[]", ckb_json=json.dumps(ckb))
                session.add(resume); session.commit(); session.refresh(resume)
                with patch("app.main.serialise_ckb", side_effect=AssertionError("current CKB must not rebuild")):
                    result, current_status = get_or_refresh_current_ckb(session, resume, None)
                self.assertEqual((result, current_status), (ckb, "reused_current"))

    def test_persisted_resume_integrity_blocks_release_despite_pack_and_ats_pass(self):
        application_id = self.seed(required=("resume",))
        roles = [
            ("Finance Administration Officer", "Department of Communities – Disability Services", "Feb 2026 – Present"),
            ("Executive Assistant to Board Member", "Avaintec", "Nov 2017 – Jan 2019"),
            ("Project Administration Officer", "China Communications Construction Company – Kenya Branch", "Jan 2016 – Aug 2017"),
            ("Project Administration Officer", "Chevron CDB Project", "Aug 2012 – Dec 2015"),
            ("Project Assistant", "Pratt & Whitney", "Oct 2007 – Aug 2012"),
        ]
        plan = {
            "schema_version": "1.1", "required_sections": ["Professional Summary", "Key Skills", "Work Experience"],
            "selected_evidence": [], "roles": [{
                "role_marker": role, "employer_marker": employer, "display_period": period,
                "chronology_order": index, "include_role_header": True,
            } for index, (role, employer, period) in enumerate(roles)],
        }
        prefix = "Alex Morgan\n0400000000 | alex@example.com\n## Professional Summary\nGrounded.\n## Key Skills\nAdministration.\n## Work Experience\n"
        incomplete = prefix + "\n".join(
            f"**{role}**\n{employer}" + (f"\n{period}" if index in {1, 2} else "")
            for index, (role, employer, period) in enumerate(roles)
        )
        complete = prefix + "\n".join(f"**{role}**\n{employer}\n{period}" for role, employer, period in roles)

        def persist(content):
            with Session(self.engine) as session:
                application = session.get(JobApplication, application_id)
                profile = session.exec(select(ApplicantProfile)).first()
                document = session.exec(select(GeneratedDocument).where(GeneratedDocument.application_id == application_id)).first()
                if not document:
                    document = GeneratedDocument(
                        application_id=application_id, document_type="tailored_resume", reviewer_json='{"status":"pass"}',
                        structured_content_json=json.dumps(plan), used_experiences_json="[]", content=content,
                    )
                    session.add(document); session.flush()
                else:
                    document.content = content
                pack = {"status": "pass", "blocks_release": False, "skipped": True, "skip_reason": "No comparison candidates.", "results": []}
                application.release_state_json = json.dumps({
                    "schema_version": "1.0",
                    "details_confirmation": {"fingerprint": details_fingerprint(application, profile)},
                    "pack_review": {"fingerprint": pack_fingerprint(application, profile, {"tailored_resume": document}), "result": pack},
                    "ats": {"document_id": document.id, "content_sha256": fingerprint(content), "format": "docx", "template": "classic", "result": {"status": "pass", "ready": True}},
                })
                session.add_all([application, document]); session.commit()

        persist(incomplete)
        final = self.client.get(f"/applications/{application_id}/quality-check").json()
        release = self.client.get(f"/applications/{application_id}/release-checklist").json()
        blocked = self.client.post(f"/applications/{application_id}/prepare-submission")

        self.assertEqual([item["code"] for item in final["issues"]].count("missing_role_period"), 3)
        self.assertFalse(final["ready"])
        self.assertTrue(release["checks"]["pack_review"]["ready"])
        self.assertTrue(release["checks"]["ats"]["ready"])
        self.assertFalse(release["ready"])
        self.assertNotEqual(release["status"], "ready_to_apply")
        self.assertEqual(blocked.status_code, 409)

        persist(complete)
        final = self.client.get(f"/applications/{application_id}/quality-check").json()
        release = self.client.get(f"/applications/{application_id}/release-checklist").json()
        self.assertTrue(final["ready"], final["issues"])
        self.assertTrue(release["ready"])
        self.assertEqual(release["status"], "ready_to_apply")
        self.assertEqual(self.client.post(f"/applications/{application_id}/prepare-submission").status_code, 200)

    def test_post_repair_resume_must_retain_authoritative_employment_block(self):
        plan = {
            "schema_version": "1.1", "required_sections": ["Professional Summary", "Key Skills", "Work Experience"],
            "selected_evidence": [], "roles": [{
                "employer_marker": "Avaintec", "role_marker": "Executive Assistant to Board Member",
                "display_period": "Nov 2017 - Jan 2019", "chronology_order": 0, "include_role_header": True,
            }],
        }
        valid = """## Professional Summary
Grounded support.
## Key Skills
Administration
## Work Experience
**Executive Assistant to Board Member**
**Avaintec**
Nov 2017 – Jan 2019"""
        missing = """## Professional Summary
Grounded support.
## Key Skills
Administration
## Work Experience
Other grounded work."""
        missing_date = valid.replace("Nov 2017 – Jan 2019", "")
        for repaired, expected_status, expected_error in (
            (valid, 200, ""),
            (missing, 502, "missing the required role header"),
            (missing_date, 502, "missing the authoritative employment period"),
        ):
            with self.subTest(expected_status=expected_status):
                application_id = self.seed(required=("resume",))
                with Session(self.engine) as session:
                    application = session.get(JobApplication, application_id)
                    profile = session.exec(select(ApplicantProfile)).first()
                    requirements = json.loads(application.application_requirements_json)
                    requirements["source"] = "source_aware_parser"
                    application.application_requirements_json = json.dumps(requirements)
                    application.application_decision_json = json.dumps({
                        "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                        "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                        "requirements": [], "questions": [], "blocking_issues": [],
                    })
                    session.add(application); session.commit()
                with patch("app.main.match_evidence_batch", return_value={"schema_version": "1.0", "matches": [], "unused_evidence": []}), patch(
                    "app.main.build_resume_curation_plan", return_value=plan
                ), patch("app.main.generate_draft", return_value=valid), patch(
                    "app.main.repair_tailored_resume", return_value=(repaired, {"status": "pass", "results": []})
                ):
                    response = self.client.post("/generate", json={"application_id": application_id, "document_type": "tailored_resume"})

                self.assertEqual(response.status_code, expected_status, response.text)
                if expected_status == 200:
                    self.assertEqual(json.loads(response.json()["trace_json"])["runtime"]["ckb_status"], "reused_current")
                if expected_status == 502:
                    self.assertIn(expected_error, response.json()["detail"])

    def test_resume_name_is_restored_after_automatic_repair(self):
        application_id = self.seed(required=("resume",))
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            profile = session.exec(select(ApplicantProfile)).first()
            requirements = json.loads(application.application_requirements_json)
            requirements["source"] = "source_aware_parser"
            application.application_requirements_json = json.dumps(requirements)
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.add(application); session.commit()
        draft = "## Professional Summary\nGrounded support.\n## Key Skills\nAdministration\n## Work Experience\nGrounded support."
        with patch("app.main.match_evidence_batch", return_value={"schema_version": "1.0", "matches": [], "unused_evidence": []}), patch(
            "app.main.generate_draft", return_value=draft
        ), patch("app.main.repair_tailored_resume", return_value=(draft, {"status": "pass", "results": []})):
            response = self.client.post("/generate", json={"application_id": application_id, "document_type": "tailored_resume"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Alex Morgan", response.json()["content"])
        self.assertIn("0400000000", response.json()["content"])
        self.assertIn("alex@example.com", response.json()["content"])

    def test_reviewer_provider_failure_keeps_same_draft_for_safe_retry(self):
        application_id = self.seed(required=("cover_letter",))
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            profile = session.exec(select(ApplicantProfile)).first()
            requirements = json.loads(application.application_requirements_json)
            requirements["source"] = "source_aware_parser"
            application.application_requirements_json = json.dumps(requirements)
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.add(application); session.commit()
        draft = "Application for Office Administrator\n\n" + "Grounded administration support. " * 40
        with patch("app.main.match_evidence_batch", return_value={"schema_version": "1.0", "matches": [], "unused_evidence": []}), patch(
            "app.main.generate_draft", return_value=draft
        ), patch("app.main.repair_cover_letter", side_effect=AIServiceError("Unterminated string at line 144")):
            failed = self.client.post("/generate", json={"application_id": application_id, "document_type": "cover_letter"})

        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertNotIn("Unterminated", failed.json()["detail"]["message"])
        document_id = failed.json()["detail"]["document_id"]
        documents = self.client.get(f"/applications/{application_id}/documents").json()
        self.assertEqual([item["id"] for item in documents], [document_id])
        self.assertEqual(json.loads(documents[0]["reviewer_json"])["status"], "provider_failed")

        with patch("app.main.review_cover_letter", return_value={"status": "pass", "results": []}):
            retried = self.client.post(f"/documents/{document_id}/review")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["id"], document_id)
        self.assertEqual(json.loads(retried.json()["reviewer_json"])["status"], "pass")

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def sessions():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def requirements(*required):
        result = empty_application_requirements("User-confirmed requirements", "user_supplied")
        result["review_status"] = "confirmed"
        for document in result["documents"].values():
            document.update(requirement="not_required", format="not_applicable")
        for name in required:
            result["documents"][name].update(requirement="required", format="standalone")
        return result

    def seed(self, required=("resume", "cover_letter")):
        with Session(self.engine) as session:
            profile = ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com")
            resume = Resume(source_text="Alex Morgan\nOffice administration experience.", ckb_json="[]")
            requirements = self.requirements(*required)
            application = JobApplication(
                company="SRG Global", position_title="Office Administrator", job_url="https://example.com/apply",
                job_description="Office administration and procurement support.",
                application_requirements_json=json.dumps(requirements), job_model_json='{"schema_version":"1.0","criteria":[],"limit_scope":"unspecified"}',
            )
            session.add_all([profile, resume, application]); session.flush()
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.add(application); session.commit(); session.refresh(application)
            return application.id

    def test_not_required_selection_criteria_is_rejected_and_cannot_block_final_check(self):
        application_id = self.seed()
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            application.selection_plan_json = '{"items":[{"criteria_id":"C1","evidence_status":"weak"}]}'
            session.add_all([
                GeneratedDocument(application_id=application_id, document_type="tailored_resume", content="Alex Morgan\n## Professional Summary\nAdmin\n## Key Skills\nAdmin\n## Work Experience\nAdmin", reviewer_json='{"status":"pass"}', structured_content_json='{"schema_version":"1.1","roles":[]}'),
                GeneratedDocument(application_id=application_id, document_type="cover_letter", content="Application for Office Administrator at SRG Global. " + "evidence " * 220, reviewer_json='{"status":"pass"}'),
                GeneratedDocument(application_id=application_id, document_type="selection_criteria", content="Unnecessary", reviewer_json='{"status":"fail","generation_status":"needs_ckb_update","results":[]}'),
            ])
            session.commit()

        rejected = self.client.post("/generate", json={"application_id": application_id, "document_type": "selection_criteria"})
        final = self.client.get(f"/applications/{application_id}/quality-check").json()
        release = self.client.get(f"/applications/{application_id}/release-checklist").json()
        exported = self.client.get(f"/applications/{application_id}/export-pack?format=docx&template=classic")

        self.assertEqual(rejected.status_code, 409)
        self.assertNotIn("selection_criteria", final["checked_documents"])
        self.assertFalse(any(item.get("document_type") == "selection_criteria" for item in final["issues"]))
        self.assertTrue(release["checks"]["selection_confirmations"]["ready"])
        self.assertNotIn("selection_criteria", [item["document_type"] for item in self.client.get(f"/applications/{application_id}/documents").json()])
        self.assertEqual(exported.status_code, 200)
        with ZipFile(BytesIO(exported.content)) as archive:
            self.assertFalse(any("Selection_Criteria" in name for name in archive.namelist()))

    def test_job_edit_updates_primary_source_and_makes_old_documents_historical(self):
        application_id = self.seed()
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            session.add(JobSource(
                application_id=application_id, source_id="primary", source_type="primary_advertisement",
                title="Job advertisement", label="Primary advertisement", acquisition_status="fetched",
                extraction_status="extracted", classification_confidence="high", extracted_text=application.job_description,
            ))
            session.add(GeneratedDocument(application_id=application_id, document_type="tailored_resume", content="Old CV", reviewer_json='{"status":"pass"}'))
            session.commit()

        updated = self.client.patch(f"/applications/{application_id}", json={"job_description": "Changed procurement and workforce administration duties."})
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            profile = session.exec(select(ApplicantProfile)).first()
            requirements = self.requirements("resume", "cover_letter")
            application.application_requirements_json = json.dumps(requirements)
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs(json.loads(application.job_model_json), requirements, [], profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.commit()
        documents = self.client.get(f"/applications/{application_id}/documents").json()
        final = self.client.get(f"/applications/{application_id}/quality-check").json()
        with Session(self.engine) as session:
            source = session.exec(select(JobSource).where(JobSource.application_id == application_id)).first()

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(documents, [])
        self.assertIn("Changed procurement", source.extracted_text)
        self.assertIn("stale_generated_document", [item["code"] for item in final["issues"]])
        with Session(self.engine) as session:
            application = session.get(JobApplication, application_id)
            resume = session.exec(select(Resume)).first()
            profile = session.exec(select(ApplicantProfile)).first()
            session.add(GeneratedDocument(
                application_id=application_id, document_type="tailored_resume", content="Current CV",
                reviewer_json='{"status":"pass"}',
                trace_json=json.dumps({"input_fingerprint": generation_inputs_fingerprint(application, resume, profile)}),
            ))
            session.commit()
            self.assertEqual(len(session.exec(select(GeneratedDocument).where(GeneratedDocument.application_id == application_id)).all()), 2)
        self.assertEqual(len(self.client.get(f"/applications/{application_id}/documents").json()), 1)

    def test_manual_cover_letter_edit_can_be_re_reviewed(self):
        application_id = self.seed(required=("cover_letter",))
        with Session(self.engine) as session:
            document = GeneratedDocument(
                application_id=application_id, document_type="cover_letter", content="Edited grounded letter.",
                reviewer_json="{}", structured_content_json='{"priorities":["administration"]}',
            )
            session.add(document); session.commit(); session.refresh(document); document_id = document.id

        with patch("app.main.review_cover_letter", return_value={"status": "pass", "results": []}):
            response = self.client.post(f"/documents/{document_id}/review")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.json()["reviewer_json"])["status"], "pass")

    def test_manual_selection_criteria_edit_preserves_plan_for_re_review(self):
        application_id = self.seed(required=("selection_criteria",))
        plan = {"items": [{"criteria_id": "C1", "criteria_text": "Communication", "matched_evidence": [], "allocated_word_limit": 100}]}
        bundle = {
            "selection_plan": plan,
            "responses": [{"criteria_id": "C1", "final_response": "Original.", "evidence_used": [], "star": {"situation": "", "task": "", "action": "", "result": ""}}],
        }
        with Session(self.engine) as session:
            document = GeneratedDocument(
                application_id=application_id, document_type="selection_criteria",
                content="## Communication\n\nEdited grounded response.", reviewer_json="{}",
                structured_content_json=json.dumps(bundle),
            )
            session.add(document); session.commit(); session.refresh(document); document_id = document.id

        with patch("app.main.review_selection_criteria_batch", return_value={"status": "pass", "results": []}):
            response = self.client.post(f"/documents/{document_id}/review")

        self.assertEqual(response.status_code, 200)
        structured = json.loads(response.json()["structured_content_json"])
        self.assertEqual(structured["responses"][0]["final_response"], "Edited grounded response.")

    def test_submission_snapshot_excludes_unrequired_selection_criteria(self):
        application = JobApplication(company="SRG Global", position_title="Office Administrator", job_description="JD")
        documents = [
            GeneratedDocument(id=1, application_id=1, document_type="tailored_resume", content="CV"),
            GeneratedDocument(id=2, application_id=1, document_type="cover_letter", content="Letter"),
            GeneratedDocument(id=3, application_id=1, document_type="selection_criteria", content="Historical SC"),
        ]
        snapshot = build_submission_snapshot(application, documents, [], "Australia", [], documents[0].created_at, ("tailored_resume", "cover_letter"))
        self.assertNotIn("selection_criteria", snapshot["documents"])


if __name__ == "__main__":
    unittest.main()
