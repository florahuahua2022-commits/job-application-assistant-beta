import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, Resume
from app.outcome_learning import build_outcome_signals, source_hash
from app.resume_plan import build_resume_curation_plan


def ckb_item(evidence_id, text, framing="experience"):
    return {"evidence_id": evidence_id, "evidence_type": framing, "source_section": f"Work > {evidence_id}", "source_text": text, "action": text, "evidence_quality": "medium"}


def historical(application_id, evidence_id="E1", text="Same evidence", market="AU", title="project officer", events=("interview",), excluded=False, document_id=10):
    outcome_events = [{"event_id": f"{application_id}-{index}", "event_type": event, "effective_date": "2026-01-01", "recorded_at": "2026-01-01T00:00:00+00:00", "stage_label": "", "reason": "", "note": ""} for index, event in enumerate(("submitted", *events))]
    payload = {
        "schema_version": "1.0", "current_outcome": outcome_events[-1]["event_type"], "excluded_from_learning": excluded,
        "events": outcome_events, "submission_snapshot": {
            "market": market, "normalized_position_title": title,
            "documents": {"tailored_resume": {"document_id": document_id, "used_evidence_ids": [evidence_id]}},
            "evidence_identities": [{"evidence_id": evidence_id, "evidence_type": "experience", "source_text_hash": source_hash(text)}],
        },
    }
    return SimpleNamespace(id=application_id, outcome_json=json.dumps(payload))


class OutcomeLearningUnitTests(unittest.TestCase):
    def setUp(self):
        self.ckb = [ckb_item("E1", "Same evidence"), ckb_item("E2", "Other evidence")]

    def signals(self, applications, **kwargs):
        return build_outcome_signals(applications, 99, kwargs.get("market", "Australia"), kwargs.get("title", "Project Officer"), kwargs.get("ckb", self.ckb))

    def test_zero_one_and_sparse_history_are_inert(self):
        self.assertEqual(self.signals([])["evidence_signals"], [])
        self.assertEqual(self.signals([historical(1)])["evidence_signals"], [])
        self.assertEqual(self.signals([historical(1), historical(2)])["evidence_signals"], [])

    def test_three_comparable_and_two_positives_produce_bounded_signal(self):
        result = self.signals([historical(1), historical(2), historical(3, events=("rejected",))])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["comparable_applications"], 3)
        self.assertEqual(result["evidence_signals"][0]["effect"], "tie_break_only")
        self.assertEqual(result["evidence_signals"][0]["interviews_or_progressions"], 2)

    def test_negative_events_are_neutral_and_interview_survives_later_rejection(self):
        neutral = self.signals([historical(1, events=("rejected",)), historical(2, events=("no_response",)), historical(3, events=("withdrawn",))])
        self.assertEqual(neutral["evidence_signals"], [])
        mixed = self.signals([historical(1, events=("interview", "rejected")), historical(2, events=("offer", "withdrawn")), historical(3, events=("rejected",))])
        self.assertEqual(mixed["evidence_signals"][0]["interviews_or_progressions"], 1)
        self.assertEqual(mixed["evidence_signals"][0]["positive_applications"], 2)
        self.assertEqual(mixed["evidence_signals"][0]["offers"], 1)

    def test_market_title_exclusion_legacy_and_identity_mismatch_do_not_pool(self):
        cases = [
            historical(1, market="US"), historical(2, title="finance officer"), historical(3, excluded=True),
            SimpleNamespace(id=4, outcome_json='{"schema_version":"1.0","current_outcome":"interview","excluded_from_learning":false,"events":[{"event_type":"interview"}],"submission_snapshot":null}'),
            historical(5, text="Changed evidence"),
        ]
        self.assertEqual(self.signals(cases)["comparable_applications"], 1)
        self.assertEqual(self.signals(cases)["evidence_signals"], [])

    def test_selection_frequency_without_positive_outcome_never_creates_signal(self):
        histories = [historical(index, events=("rejected",)) for index in range(1, 8)]
        self.assertEqual(self.signals(histories)["evidence_signals"], [])

    def test_resume_plan_uses_signal_only_for_true_final_tie(self):
        model = {"criteria": [{"criteria_id": "C1", "criteria_type": "essential"}]}
        matches = {"matches": [{"criteria_id": "C1", "matched_evidence": ["E1", "E2"], "match_type": "direct", "coverage": "strong"}]}
        baseline = build_resume_curation_plan(model, matches, self.ckb, max_evidence=1)
        learning = {"status": "available", "evidence_signals": [{"evidence_id": "E2", "identity_match": True, "signal": "positive", "effect": "tie_break_only"}]}
        learned = build_resume_curation_plan(model, matches, self.ckb, max_evidence=1, outcome_learning=learning)
        self.assertEqual(baseline["selected_evidence"][0]["evidence_id"], "E1")
        self.assertEqual(learned["selected_evidence"][0]["evidence_id"], "E2")

    def test_history_cannot_override_direct_adjacent_or_confirmed_gap(self):
        model = {"criteria": [{"criteria_id": "D", "criteria_type": "essential"}, {"criteria_id": "A", "criteria_type": "essential"}, {"criteria_id": "G", "criteria_type": "essential"}]}
        matches = {"matches": [
            {"criteria_id": "D", "matched_evidence": ["E1"], "match_type": "direct", "coverage": "strong"},
            {"criteria_id": "A", "matched_evidence": ["E2"], "match_type": "inferred", "coverage": "strong"},
            {"criteria_id": "G", "matched_evidence": [], "match_type": "insufficient", "coverage": "weak"},
        ]}
        decision = {"requirements": [
            {"criteria_id": "D", "importance": "essential", "evidence_classification": "verified_match"},
            {"criteria_id": "A", "importance": "essential", "evidence_classification": "adjacent_match"},
            {"criteria_id": "G", "importance": "essential", "evidence_classification": "confirmed_gap"},
        ]}
        learning = {"status": "available", "evidence_signals": [{"evidence_id": "E2", "identity_match": True, "signal": "positive", "effect": "tie_break_only"}]}
        plan = build_resume_curation_plan(model, matches, self.ckb, max_evidence=1, application_decision=decision, outcome_learning=learning)
        self.assertEqual(plan["selected_evidence"][0]["evidence_id"], "E1")
        self.assertEqual(plan["selected_evidence"][0]["evidence_framing"], "direct")

    def test_aggregation_does_not_mutate_inputs(self):
        histories = [historical(1), historical(2), historical(3)]
        before = ([item.outcome_json for item in histories], deepcopy(self.ckb))
        self.signals(histories)
        self.assertEqual(([item.outcome_json for item in histories], self.ckb), before)


class OutcomeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.user, self.other = uuid4(), uuid4()
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_current_user] = lambda: self.user
        with Session(self.engine) as session:
            session.add_all([
                ApplicantProfile(user_id=self.user, first_name="Alex", last_name="Morgan", phone="1", email="a@b.com", country="Australia"),
                Resume(user_id=self.user, source_text="Evidence", ckb_json=json.dumps([ckb_item("E1", "Same evidence")])),
            ])
            application = JobApplication(user_id=self.user, company="Agency", position_title="Project Officer", job_description="Role", status="ready_to_apply")
            other = JobApplication(user_id=self.other, company="Other", position_title="Project Officer", job_description="Role")
            session.add_all([application, other]); session.flush()
            old = GeneratedDocument(user_id=self.user, application_id=application.id, document_type="tailored_resume", content="Old", structured_content_json='{"schema_version":"1.1"}', used_experiences_json='["E1"]', created_at=datetime.utcnow() - timedelta(days=1))
            session.add(old); session.commit(); session.refresh(old)
            self.application_id, self.other_id, self.old_id = application.id, other.id, old.id
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear(); self.engine.dispose()

    def test_submission_snapshot_is_immutable_and_later_document_gets_no_credit(self):
        submitted = self.client.patch(f"/applications/{self.application_id}/submission", json={})
        self.assertEqual(submitted.status_code, 200)
        first = json.loads(submitted.json()["outcome_json"])["submission_snapshot"]
        self.assertEqual(first["documents"]["tailored_resume"]["document_id"], self.old_id)
        with Session(self.engine) as session:
            later = GeneratedDocument(user_id=self.user, application_id=self.application_id, document_type="tailored_resume", content="Later", used_experiences_json='["E2"]')
            session.add(later); session.commit(); session.refresh(later); later_id = later.id
        repeated = self.client.patch(f"/applications/{self.application_id}/submission", json={}).json()
        snapshot = json.loads(repeated["outcome_json"])["submission_snapshot"]
        self.assertEqual(snapshot["documents"]["tailored_resume"]["document_id"], self.old_id)
        self.assertNotEqual(snapshot["documents"]["tailored_resume"]["document_id"], later_id)

    def test_record_correct_remove_unknown_and_exclude_without_ai(self):
        self.client.patch(f"/applications/{self.application_id}/submission", json={})
        with patch("app.ai._selection_provider_response") as provider:
            created = self.client.post(f"/applications/{self.application_id}/outcome/events", json={"event_type": "interview", "effective_date": "2026-08-01"})
        provider.assert_not_called()
        event_id = created.json()["events"][-1]["event_id"]
        corrected = self.client.put(f"/applications/{self.application_id}/outcome/events/{event_id}", json={"event_type": "unknown", "effective_date": "2026-08-02"})
        self.assertEqual(corrected.json()["current_outcome"], "unknown")
        excluded = self.client.patch(f"/applications/{self.application_id}/outcome/exclusion", json={"excluded_from_learning": True})
        self.assertTrue(excluded.json()["excluded_from_learning"])
        removed = self.client.delete(f"/applications/{self.application_id}/outcome/events/{event_id}")
        self.assertEqual(removed.status_code, 200)

    def test_cross_user_access_is_rejected(self):
        for method, path, payload in (
            ("get", f"/applications/{self.other_id}/outcome", None),
            ("post", f"/applications/{self.other_id}/outcome/events", {"event_type": "unknown", "effective_date": "2026-08-01"}),
        ):
            response = getattr(self.client, method)(path, json=payload) if payload else getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
