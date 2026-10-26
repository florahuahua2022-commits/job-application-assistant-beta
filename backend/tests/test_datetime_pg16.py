"""Critical write paths against repository DDL and real PG16 credit functions."""
import json
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from test_datetime_incident import incident_client, utc_instant
from app.models import GeneratedDocument, JobApplication, JobSource, Resume, PackCreditLedger, GenerationUsage
from app.pack_credits import reserve_pack_credits, complete_pack_credits, release_pack_credits, pack_credit_balance

pytestmark = pytest.mark.skipif("--incident-pg16-port" not in sys.argv, reason="explicit isolated PG16 runner required")


@pytest.mark.parametrize("timestamp", [None, "2026-01-02T01:00:00Z", "2026-01-02T09:00:00+08:00"])
def test_pg16_submission_response_json_contains_timezone(incident_client, timestamp):
    client, app_id, _ = incident_client
    payload = {"submission_reference": "synthetic"}
    if timestamp is not None:
        payload["submitted_at"] = timestamp
    response = client.patch(f"/applications/{app_id}/submission", json=payload)
    assert response.status_code == 200
    value = response.json()["submitted_at"]
    # Inspect the API value directly: no utc_instant() or timezone replacement.
    assert re.search(r"(?:Z|[+-]\d{2}:\d{2})$", value), value
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() is not None
    if timestamp is not None:
        assert parsed == datetime(2026, 1, 2, 1, tzinfo=timezone.utc)


def test_pg16_create_edit_sources_and_resume_replacement(incident_client, monkeypatch):
    client, _, engine = incident_client
    created = client.post("/applications", json={"company": "Synthetic", "position_title": "Officer",
                                               "job_description": "Coordinate reports."})
    assert created.status_code == 200
    app_id = created.json()["id"]
    edited = client.patch(f"/applications/{app_id}", json={"company": "Changed", "position_title": "Senior Officer",
                                                         "job_description": "Coordinate reports and registers."})
    assert edited.status_code == 200
    assert edited.json()["company"] == "Changed"
    uploaded = client.post(f"/applications/{app_id}/sources/upload", data={"expected_source_type": "job_description_attachment"},
                           files={"file": ("synthetic.txt", b"Coordinate reports and registers with project teams.", "text/plain")})
    assert uploaded.status_code == 200
    import app.main as main
    monkeypatch.setattr(main, "extract_resume_text", lambda *args: "Synthetic Applicant\nProject officer\nPrepared reports.")
    for title in ["First synthetic resume", "Replacement synthetic resume"]:
        response = client.post("/resumes/upload", data={"title": title}, files={"file": ("resume.txt", b"synthetic", "text/plain")})
        assert response.status_code == 200
    with Session(engine) as session:
        resumes = session.exec(select(Resume)).all()
        assert len(resumes) == 1
        assert resumes[0].title == "Replacement synthetic resume"
        assert resumes[0].updated_at.utcoffset() is not None


def test_pg16_submission_status_archive_restore_and_outcomes(incident_client):
    client, app_id, engine = incident_client
    base = f"/applications/{app_id}"
    for path, payload in [("/status", {"status": "applied"}), ("/archive", {"action": "archive"}),
                          ("/archive", {"action": "restore"})]:
        response = client.patch(base + path, json=payload)
        assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["archived_at"] is None
    created = client.post(base + "/outcome/events", json={"event_type": "interview", "effective_date": "2026-01-02"})
    assert created.status_code == 200
    event_id = created.json()["events"][-1]["event_id"]
    corrected = client.put(base + f"/outcome/events/{event_id}", json={"event_type": "offer", "effective_date": "2026-01-03"})
    assert corrected.status_code == 200
    assert corrected.json()["current_outcome"] == "offer"
    assert client.patch(base + "/outcome/exclusion", json={"excluded_from_learning": True}).status_code == 200
    assert client.delete(base + f"/outcome/events/{event_id}").status_code == 200
    with Session(engine) as session:
        assert session.get(JobApplication, app_id).submitted_at.utcoffset() is not None


def test_pg16_selection_document_edit_and_confirmation(incident_client):
    from app.application_requirements import empty_application_requirements
    client, app_id, engine = incident_client
    with Session(engine) as session:
        application = session.get(JobApplication, app_id)
        requirements = empty_application_requirements("Synthetic", source="user_supplied")
        requirements["documents"]["selection_criteria"].update(requirement="required", format="standalone")
        application.application_requirements_json = json.dumps(requirements)
        application.selection_plan_json = json.dumps({"items": [{"criteria_id": "SC1", "evidence_status": "weak"}]})
        document = GeneratedDocument(user_id=application.user_id, application_id=app_id,
                                     document_type="selection_criteria", content="Original synthetic response")
        session.add(document)
        session.add(application)
        session.commit()
        session.refresh(document)
        document_id = document.id
    edited = client.patch(f"/documents/{document_id}", json={"content": "Edited synthetic response"})
    assert edited.status_code == 200
    confirmed = client.post(f"/applications/{app_id}/selection-confirmations", json={"criteria_ids": ["SC1"]})
    assert confirmed.status_code == 200
    assert json.loads(confirmed.json()["selection_confirmations_json"]) == ["SC1"]
    assert datetime.fromisoformat(confirmed.json()["updated_at"]).utcoffset() is not None


def test_pg16_acquired_source_timestamp_persists(incident_client, monkeypatch):
    from app import source_acquisition
    client, app_id, engine = incident_client
    with Session(engine) as session:
        application = session.get(JobApplication, app_id)
        source = JobSource(user_id=application.user_id, application_id=app_id, source_id="synthetic-attachment",
                           title="Synthetic attachment", label="Synthetic", extraction_status="not_attempted",
                           source_type="job_description_attachment", source_url="https://example.invalid/synthetic.txt",
                           acquisition_status="discovered", classification_confidence="high")
        monkeypatch.setattr(source_acquisition, "_fetch", lambda *args: (b"Synthetic project duties", {"Content-Type": "text/plain"}, source.source_url))
        source_acquisition.acquire_sources([source])
        session.add(source)
        session.commit()
        session.refresh(source)
        assert source.updated_at.utcoffset() is not None


def test_pg16_credit_reserve_complete_release_and_retry_idempotency(incident_client):
    _, app_id, engine = incident_client
    with Session(engine) as session:
        user = session.get(JobApplication, app_id).user_id
        completed_pack, failed_pack = uuid4(), uuid4()
        assert reserve_pack_credits(session, user, completed_pack, 1).status == "reserved"
        assert reserve_pack_credits(session, user, completed_pack, 1).status == "existing"
        assert complete_pack_credits(session, user, completed_pack) is True
        assert complete_pack_credits(session, user, completed_pack) is False
        assert release_pack_credits(session, user, completed_pack) is False
        assert reserve_pack_credits(session, user, failed_pack, 1).status == "reserved"
        assert release_pack_credits(session, user, failed_pack) is True
        assert release_pack_credits(session, user, failed_pack) is False
        assert complete_pack_credits(session, user, failed_pack) is False
        assert reserve_pack_credits(session, user, failed_pack, 1).status == "existing"
        session.commit()
    with Session(engine) as session:
        assert pack_credit_balance(session, user) == 1
        assert sorted(u.status for u in session.exec(select(GenerationUsage)).all()) == ["completed", "released"]
        ledger = session.exec(select(PackCreditLedger)).all()
        assert sum(item.entry_type == "debit_generation" for item in ledger) == 2
        assert sum(item.entry_type == "release" for item in ledger) == 1
        counters = session.execute(text("SELECT reserved_count, completed_count FROM public.globalmonthlyusage")).one()
        assert tuple(counters) == (0, 1)


@pytest.mark.parametrize("method", [
    "test_successful_complete_pack_completes_one_credit_reservation",
    "test_pack_completes_only_after_every_document_in_this_pack_succeeds",
    "test_failed_generation_releases_reserved_credits",
    "test_released_pack_id_cannot_restart_generation_without_a_new_reservation",
])
def test_pg16_real_generation_credit_orchestration(incident_client, monkeypatch, method):
    import test_pack_credits as contracts
    _, _, engine = incident_client

    def synthetic_id():
        value = uuid4()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": value})
        return value

    monkeypatch.setattr(contracts, "uuid4", synthetic_id)
    case = contracts.PackCreditEngineTests("runTest")
    case.engine = engine
    # Reuse the existing orchestration contracts. Synthetic draft generation is
    # stubbed there; credit reservation/completion/release and PG functions are real.
    getattr(case, method)()


def test_pg16_legacy_timestamp_without_timezone_readback(incident_client):
    client, app_id, engine = incident_client
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE public.applicantprofile ALTER COLUMN updated_at TYPE timestamp without time zone USING updated_at AT TIME ZONE 'UTC'")
    response = client.get("/profile")
    assert response.status_code == 200
    with Session(engine) as session:
        raw = session.execute(text("SELECT updated_at FROM public.applicantprofile")).scalar_one()
        # A legacy timestamp column has no offset; compare known UTC values only.
        assert utc_instant(raw) == utc_instant(datetime.fromisoformat(response.json()["updated_at"]))
        assert utc_instant(raw) == datetime(2026, 1, 1, tzinfo=timezone.utc)
