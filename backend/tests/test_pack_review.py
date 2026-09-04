import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import ai
from app.application_decision import decision_inputs
from app.application_requirements import empty_application_requirements
from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, Resume
from app.pack_quality import build_pack_review_payload


def review_package() -> dict:
    return {
        "schema_version": "1.0",
        "identity": {"applicant_name": "Alex Morgan", "company": "Example Agency", "position_title": "Officer"},
        "documents": {
            "tailored_resume": {"content": "Supported procurement administration."},
            "cover_letter": {"content": "Managed procurement processes."},
            "selection_criteria": {"responses": [{"criteria_id": "C1", "final_response": "Detailed example."}]},
        },
        "evidence_groups": [{
            "evidence_id": "E17", "source": {"source_text": "Supported procurement administration."},
            "framing": "direct", "uses": [
                {"document_type": "tailored_resume"},
                {"document_type": "cover_letter"},
                {"document_type": "selection_criteria", "criteria": [{"criteria_id": "C1"}]},
            ],
        }],
        "gap_candidates": [],
    }


class PackReviewUnitTests(unittest.TestCase):
    def test_payload_contains_only_shared_referenced_ckb_and_no_invented_passages(self):
        documents = {
            "tailored_resume": {
                "content": "Supported procurement administration.",
                "used_evidence_ids": ["E17", "RESUME_ONLY"],
                "structured": {"selected_evidence": [{"evidence_id": "E17", "evidence_framing": "direct"}]},
            },
            "cover_letter": {
                "content": "Procurement support.", "used_evidence_ids": ["E17"],
                "structured": {"selected_evidence": [{"evidence_id": "E17", "purpose": "differentiator"}]},
            },
        }
        ckb = [
            {"evidence_id": "E17", "source_text": "Supported procurement administration."},
            {"evidence_id": "RESUME_ONLY", "source_text": "Prepared reports."},
            {"evidence_id": "UNUSED", "source_text": "Led an unrelated program."},
        ]
        package = build_pack_review_payload(documents, ckb, {"requirements": []}, {})

        self.assertEqual([item["evidence_id"] for item in package["evidence_groups"]], ["E17"])
        self.assertNotIn("UNUSED", json.dumps(package))
        self.assertNotIn("RESUME_ONLY", json.dumps(package))
        self.assertTrue(all(use["passage_attribution"] == "not_structured" for use in package["evidence_groups"][0]["uses"]))
        with patch.object(ai, "_selection_provider_response", return_value='{"results":[]}') as provider:
            ai.review_application_pack(package)
        prompt = provider.call_args.args[0]
        self.assertNotIn("UNUSED", prompt)
        self.assertNotIn("RESUME_ONLY", prompt)

    def test_different_ids_with_similar_capability_create_no_candidate(self):
        documents = {
            "tailored_resume": {"content": "Reporting", "used_evidence_ids": ["E1"], "structured": {}},
            "selection_criteria": {"content": "Reporting", "used_evidence_ids": ["E2"], "structured": {"responses": []}},
        }
        self.assertIsNone(build_pack_review_payload(documents, [], {"requirements": []}, {}))

    def test_structured_gap_in_two_document_contexts_creates_candidate_without_inventing_disclosure(self):
        documents = {
            "cover_letter": {"content": "Omitted safely", "used_evidence_ids": [], "structured": {"evidence_gaps": ["C4"]}},
            "selection_criteria": {"used_evidence_ids": [], "structured": {
                "responses": [{"criteria_id": "C4", "final_response": "Transferable evidence."}],
                "selection_plan": {"items": [{"criteria_id": "C4", "matched_evidence": []}]},
            }},
        }
        decision = {"requirements": [{
            "criteria_id": "C4", "evidence_classification": "confirmed_gap", "disclosure_strategy": "none",
        }]}
        package = build_pack_review_payload(documents, [], decision, {})
        self.assertEqual(package["gap_candidates"][0]["disclosure_strategy"], "none")
        self.assertEqual(package["evidence_groups"], [])

    def test_responsibility_escalation_is_blocking_and_routed(self):
        raw = {"results": [{
            "document_type": "cover_letter", "criteria_id": None, "status": "fail", "issues": [{
                "type": "unsupported_inference", "evidence_id": "E17",
                "description": "Managed materially exceeds supported.",
                "evidence": "E17: Supported procurement administration.",
                "location": "Managed procurement processes",
                "recommended_action": "Cover Letter regeneration/repair",
            }],
        }]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
            result = ai.review_application_pack(review_package())
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["blocks_release"])
        self.assertEqual(result["results"][0]["document_type"], "cover_letter")
        self.assertEqual(result["results"][0]["status"], "fail")
        self.assertTrue(result["results"][0]["blocks_release"])
        self.assertEqual(result["results"][0]["issues"][0]["type"], "unsupported_inference")

    def test_provider_fail_without_issues_collapses_to_explainable_pass(self):
        raw = {"status": "fail", "blocks_release": True, "results": [{
            "document_type": "cover_letter", "status": "fail", "blocks_release": True, "issues": [],
        }]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
            result = ai.review_application_pack(review_package())
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["blocks_release"])
        self.assertEqual(result["results"][0]["status"], "pass")
        self.assertFalse(result["results"][0]["blocks_release"])
        self.assertEqual(result["results"][0]["issues"], [])

    def test_rejected_ungrounded_issue_leaves_no_phantom_failure(self):
        raw = {"results": [{"document_type": "cover_letter", "status": "fail", "issues": [{
            "type": "unsupported_inference", "evidence_id": "E17",
            "description": "Provider claimed a failure but supplied no exact source or location.",
            "recommended_action": "Cover Letter repair.",
        }]}]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
            result = ai.review_application_pack(review_package())
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["blocks_release"])
        self.assertEqual(result["results"][0], {
            "document_type": "cover_letter", "criteria_id": None, "status": "pass",
            "issues": [], "blocks_release": False,
        })

    def test_clean_provider_result_remains_pass_and_non_blocking(self):
        raw = {"results": [{
            "document_type": "tailored_resume", "status": "pass", "blocks_release": False, "issues": [],
        }]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
            result = ai.review_application_pack(review_package())
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["blocks_release"])
        self.assertEqual(result["results"][0]["status"], "pass")
        self.assertFalse(result["results"][0]["blocks_release"])

    def test_invalid_result_route_cannot_create_top_level_failure(self):
        raw = {"results": [{"document_type": "application_pack", "status": "fail", "issues": [{
            "type": "contradiction", "evidence_id": "E17", "description": "Invalid route.",
            "evidence": "E17: Supported procurement administration.", "location": "wording",
            "recommended_action": "Review.",
        }]}]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
            result = ai.review_application_pack(review_package())
        self.assertEqual(result["results"], [])
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["blocks_release"])

    def test_allowed_consistent_reuse_cases_pass(self):
        cases = (
            "Resume concise and SC adds grounded STAR depth",
            "Adjacent evidence remains transferable everywhere",
            "Unique strongest evidence is reused in all documents",
            "SC adds grounded context action and result",
            "Harmless paraphrase preserves factual authority",
        )
        for case in cases:
            with self.subTest(case=case), patch.object(ai, "_selection_provider_response", return_value='{"results":[]}'):
                self.assertEqual(ai.review_application_pack(review_package())["status"], "pass")

    def test_adjacent_direct_sc_copy_and_factual_contradiction_use_allowed_types(self):
        for issue_type, document_type, criteria_id in (
            ("evidence_mismatch", "cover_letter", None),
            ("requirement_omission", "selection_criteria", "C1"),
            ("contradiction", "tailored_resume", None),
        ):
            with self.subTest(issue_type=issue_type):
                raw = {"results": [{"document_type": document_type, "criteria_id": criteria_id, "issues": [{
                    "type": issue_type, "evidence_id": "E17", "description": "Material bounded inconsistency.",
                    "evidence": "E17: Supported procurement administration.", "location": "exact generated wording",
                    "recommended_action": "Use the existing document repair owner.",
                }]}]}
                with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
                    result = ai.review_application_pack(review_package())
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["results"][0]["issues"][0]["type"], issue_type)

    def test_prompt_excludes_unused_ckb_and_contains_false_positive_rules(self):
        package = review_package()
        with patch.object(ai, "_selection_provider_response", return_value='{"results":[]}') as provider:
            ai.review_application_pack(package)
        prompt = provider.call_args.args[0]
        self.assertIn("Evidence reuse is not itself an issue", prompt)
        self.assertIn("Cover Letter summarisation is expected", prompt)
        self.assertIn("Do not invent sentence-to-evidence attribution", prompt)
        self.assertIn("Supported procurement administration", prompt)
        self.assertNotIn("UNUSED CKB RECORD", prompt)

    def test_malformed_output_retries_once(self):
        with patch.object(ai, "_selection_provider_response", side_effect=["not json", '{"results":[]}']) as provider:
            result = ai.review_application_pack(review_package())
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result["telemetry"]["reviewer_retries"], 1)

    def test_ungrounded_finding_is_non_blocking_and_unknown_type_is_advisory(self):
        raw = {"results": [{"document_type": "cover_letter", "issues": [
            {"type": "contradiction", "evidence_id": "E17", "description": "No exact grounding."},
            {"type": "broad_pack_critique", "evidence_id": "E17", "description": "Unknown type.",
             "evidence": "E17: Supported procurement administration.", "location": "exact wording", "recommended_action": "Review."},
        ]}]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)):
            result = ai.review_application_pack(review_package())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["results"][0]["issues"]), 1)
        self.assertEqual(result["results"][0]["issues"][0]["severity"], "advisory")
        self.assertFalse(result["results"][0]["issues"][0]["blocks_release"])
        self.assertEqual(result["results"][0]["status"], "pass")
        self.assertFalse(result["results"][0]["blocks_release"])
        self.assertFalse(result["blocks_release"])


class PackReviewEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_current_user] = lambda: None
        with Session(self.engine) as session:
            profile = ApplicantProfile(first_name="Alex", last_name="Morgan", phone="0400000000", email="alex@example.com")
            resume = Resume(source_text="Alex Morgan", ckb_json=json.dumps([
                {"schema_version": "2.0", "evidence_id": "E1", "source_text": "Supported procurement administration."},
                {"schema_version": "2.0", "evidence_id": "E2", "source_text": "Prepared monthly reports."},
            ]))
            requirements = empty_application_requirements("Confirmed", source="user_supplied")
            requirements["review_status"] = "confirmed"
            requirements["documents"]["resume"].update(requirement="required", format="standalone")
            requirements["documents"]["cover_letter"].update(requirement="required", format="standalone")
            requirements["documents"]["selection_criteria"].update(requirement="not_required", format="not_applicable")
            application = JobApplication(
                company="Example Agency", position_title="Project Officer", job_description="Coordinate projects.",
                job_model_json="{}", application_requirements_json=json.dumps(requirements),
            )
            session.add_all([profile, resume, application]); session.flush()
            application.application_decision_json = json.dumps({
                "schema_version": "1.0", "status": "ready", "application_recommendation": "apply",
                "inputs": decision_inputs({}, requirements, json.loads(resume.ckb_json), profile),
                "requirements": [], "questions": [], "blocking_issues": [],
            })
            session.add_all([
                GeneratedDocument(
                    application_id=application.id, document_type="tailored_resume",
                    content="Alex Morgan 0400000000 alex@example.com\n## Professional Summary\nProof\n## Key Skills\nAdmin\n## Work Experience\nSupported procurement administration.",
                    reviewer_json='{"status":"pass"}',
                    structured_content_json='{"selected_evidence":[{"evidence_id":"E1"}]}',
                    used_experiences_json='["E1"]',
                ),
                GeneratedDocument(
                    application_id=application.id, document_type="cover_letter",
                    content="Alex Morgan 0400000000 alex@example.com\nApplication for Project Officer at Example Agency. " + "evidence " * 220,
                    reviewer_json='{"status":"pass"}',
                    structured_content_json='{"selected_evidence":[{"evidence_id":"E2"}]}',
                    used_experiences_json='["E2"]',
                ),
            ])
            session.commit(); self.application_id = application.id
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_no_candidates_skips_provider_and_persists_release_result_only(self):
        with Session(self.engine) as session:
            application_before = session.get(JobApplication, self.application_id).model_dump()
            documents_before = [item.model_dump() for item in session.exec(select(GeneratedDocument).order_by(GeneratedDocument.id)).all()]
        with patch.object(ai, "_selection_provider_response") as provider:
            response = self.client.post(f"/applications/{self.application_id}/pack-review")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["status"], "pass")
        self.assertFalse(payload["blocks_release"])
        self.assertEqual(payload["results"], [])
        self.assertTrue(payload["skip_reason"])
        provider.assert_not_called()
        with Session(self.engine) as session:
            application_after = session.get(JobApplication, self.application_id).model_dump()
            self.assertEqual({**application_after, "release_state_json": "{}"}, application_before)
            self.assertTrue(json.loads(application_after["release_state_json"])["pack_review"]["result"]["skipped"])
            self.assertEqual([item.model_dump() for item in session.exec(select(GeneratedDocument).order_by(GeneratedDocument.id)).all()], documents_before)

    def test_quality_check_never_calls_pack_provider(self):
        with patch.object(ai, "_selection_provider_response") as provider:
            response = self.client.get(f"/applications/{self.application_id}/quality-check")
        self.assertEqual(response.status_code, 200)
        provider.assert_not_called()

    def test_final_check_failure_returns_409_without_provider(self):
        with Session(self.engine) as session:
            application = session.get(JobApplication, self.application_id)
            application.application_decision_json = "{}"
            session.commit()
        with patch.object(ai, "_selection_provider_response") as provider:
            response = self.client.post(f"/applications/{self.application_id}/pack-review")
        self.assertEqual(response.status_code, 409)
        self.assertIn("issues", response.json()["detail"])
        provider.assert_not_called()

    def test_explicit_endpoint_runs_one_bounded_review_for_shared_evidence(self):
        with Session(self.engine) as session:
            cover = session.exec(select(GeneratedDocument).where(GeneratedDocument.document_type == "cover_letter")).first()
            cover.content = "Alex Morgan 0400000000 alex@example.com\nApplication for Project Officer at Example Agency. Managed procurement processes. " + "evidence " * 220
            cover.structured_content_json = '{"selected_evidence":[{"evidence_id":"E1","purpose":"differentiator"}]}'
            cover.used_experiences_json = '["E1"]'
            session.commit()
        raw = {"results": [{"document_type": "cover_letter", "issues": [{
            "type": "unsupported_inference", "evidence_id": "E1", "description": "Managed exceeds supported.",
            "evidence": "E1: Supported procurement administration.", "location": "Managed procurement processes",
            "recommended_action": "Cover Letter regeneration/repair",
        }]}]}
        with patch.object(ai, "_selection_provider_response", return_value=json.dumps(raw)) as provider:
            response = self.client.post(f"/applications/{self.application_id}/pack-review")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "fail")
        self.assertTrue(response.json()["blocks_release"])
        self.assertEqual(provider.call_count, 1)
        self.assertNotIn("Prepared monthly reports", provider.call_args.args[0])

    def test_provider_failure_does_not_mutate_pack_or_upstream_state(self):
        with Session(self.engine) as session:
            cover = session.exec(select(GeneratedDocument).where(GeneratedDocument.document_type == "cover_letter")).first()
            cover.structured_content_json = '{"selected_evidence":[{"evidence_id":"E1"}]}'
            cover.used_experiences_json = '["E1"]'
            session.commit()
            application_before = session.get(JobApplication, self.application_id).model_dump()
            documents_before = [item.model_dump() for item in session.exec(select(GeneratedDocument).order_by(GeneratedDocument.id)).all()]
            ckb_before = session.exec(select(Resume)).first().ckb_json
        with patch.object(ai, "_selection_provider_response", return_value="malformed") as provider:
            response = self.client.post(f"/applications/{self.application_id}/pack-review")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(provider.call_count, 2)
        with Session(self.engine) as session:
            self.assertEqual(session.get(JobApplication, self.application_id).model_dump(), application_before)
            self.assertEqual([item.model_dump() for item in session.exec(select(GeneratedDocument).order_by(GeneratedDocument.id)).all()], documents_before)
            self.assertEqual(session.exec(select(Resume)).first().ckb_json, ckb_before)


if __name__ == "__main__":
    unittest.main()
