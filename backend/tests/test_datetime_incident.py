"""Offline regression contracts for the production timezone incident."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, JobApplication, JobApplicationSubmissionUpdate


@pytest.fixture
def incident_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    user_id = uuid4()
    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        profile = ApplicantProfile(user_id=user_id, first_name="Synthetic", last_name="Applicant",
                                   phone="000", email="synthetic@example.invalid",
                                   created_at=instant, updated_at=instant)
        application = JobApplication(user_id=user_id, company="Synthetic company",
                                     position_title="Synthetic role", job_description="Synthetic description",
                                     status="ready_to_apply", created_at=instant, updated_at=instant)
        session.add_all([profile, application])
        session.commit()
        session.refresh(application)
        application_id = application.id

    def sessions():
        with Session(engine) as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_user] = lambda: user_id
    try:
        # No lifespan: never execute application startup or migrations.
        yield TestClient(app), application_id, engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def test_update_existing_profile_persists_and_returns_aware_timestamp(incident_client):
    client, _, engine = incident_client
    response = client.put("/profile", json={"first_name": "Changed", "last_name": "Applicant",
                                          "phone": "000", "email": "synthetic@example.invalid"})
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["updated_at"]).utcoffset() is not None
    with Session(engine) as session:
        profile = session.get(ApplicantProfile, response.json()["id"])
        assert profile.first_name == "Changed"
        assert profile.updated_at.utcoffset() is not None


def test_submission_rejects_naive_datetime_without_mutation(incident_client):
    client, application_id, engine = incident_client
    response = client.patch(f"/applications/{application_id}/submission",
                            json={"submitted_at": "2026-01-02T09:00:00"})
    assert response.status_code == 422
    with Session(engine) as session:
        application = session.get(JobApplication, application_id)
        assert application.status == "ready_to_apply"
        assert application.submitted_at is None


def test_submission_input_model_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        JobApplicationSubmissionUpdate.model_validate({"submitted_at": "2026-01-02T09:00:00"})


@pytest.mark.parametrize("timestamp", ["2026-01-02T01:00:00Z", "2026-01-02T09:00:00+08:00"])
def test_submission_input_model_accepts_aware_datetime(timestamp):
    value = JobApplicationSubmissionUpdate.model_validate({"submitted_at": timestamp})
    assert value.submitted_at == datetime(2026, 1, 2, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("timestamp", ["2026-01-02T01:00:00Z", "2026-01-02T09:00:00+08:00"])
def test_submission_accepts_aware_datetime_and_preserves_instant(incident_client, timestamp):
    client, application_id, engine = incident_client
    response = client.patch(f"/applications/{application_id}/submission", json={"submitted_at": timestamp})
    assert response.status_code == 200
    expected = datetime(2026, 1, 2, 1, tzinfo=timezone.utc)
    assert datetime.fromisoformat(response.json()["submitted_at"]) == expected
    with Session(engine) as session:
        assert session.get(JobApplication, application_id).submitted_at == expected


def test_mark_applied_without_client_timestamp(incident_client):
    client, application_id, _ = incident_client
    response = client.patch(f"/applications/{application_id}/submission", json={"submission_reference": "synthetic"})
    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert datetime.fromisoformat(response.json()["submitted_at"]).utcoffset() is not None


def test_frontend_mark_applied_sends_reference_without_timestamp():
    source = (Path(__file__).resolve().parents[2] / "frontend/app/page.tsx").read_text(encoding="utf-8")
    function = source.split("async function markApplied()", 1)[1].split("async function ", 1)[0]
    assert "JSON.stringify({ submission_reference:" in function
    assert "submitted_at" not in function
