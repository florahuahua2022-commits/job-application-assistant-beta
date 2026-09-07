import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from app.main import app, generate as start_generation
from app.database import get_session
from app.auth import get_current_user
from app.models import JobApplication, GeneratedDocument, GenerateRequest


class GenerationRequestTests(unittest.TestCase):
    def test_background_completion_failure_retry_and_owner_isolation(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        owner = uuid4()
        with Session(engine) as session:
            application = JobApplication(user_id=owner, company="Example", position_title="Officer", job_description="JD")
            session.add(application)
            session.commit()
            session.refresh(application)
            application_id = application.id

        def sessions():
            with Session(engine) as session:
                yield session

        def generated(payload, session, user_id):
            document = GeneratedDocument(user_id=user_id, application_id=payload.application_id, document_type=payload.document_type, content="Saved draft")
            session.add(document)
            session.commit()
            session.refresh(document)
            return document

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_current_user] = lambda: owner
        try:
            client = TestClient(app)
            with Session(engine) as session:
                queued = GenerateRequest(application_id=application_id, document_type="tailored_resume", pack_id=uuid4())
                first, duplicate = BackgroundTasks(), BackgroundTasks()
                self.assertEqual(start_generation(queued, first, True, session, owner).status_code, 202)
                self.assertEqual(start_generation(queued, duplicate, True, session, owner).status_code, 202)
                self.assertEqual(len(first.tasks), 1)
                self.assertEqual(len(duplicate.tasks), 0)
                status = client.get(f"/applications/{application_id}/generation-requests/{queued.pack_id}/tailored_resume")
                self.assertEqual(status.json()["status"], "running")
            payload = {"application_id": application_id, "document_type": "tailored_resume", "pack_id": str(uuid4())}
            url = f"/applications/{application_id}/generation-requests/{payload['pack_id']}/tailored_resume"
            with patch("app.main.generate_document", side_effect=generated) as generate:
                self.assertEqual(client.post("/generate?background=true", json=payload).status_code, 202)
                self.assertEqual(client.post("/generate?background=true", json=payload).status_code, 202)
                self.assertEqual(generate.call_count, 1)
                state = client.get(url).json()
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["document"]["content"], "Saved draft")
            app.dependency_overrides[get_current_user] = lambda: uuid4()
            self.assertEqual(client.get(url).status_code, 404)
            self.assertEqual(client.post("/generate?background=true", json=payload).status_code, 404)
            app.dependency_overrides[get_current_user] = lambda: owner
            payload["pack_id"] = str(uuid4())
            url = f"/applications/{application_id}/generation-requests/{payload['pack_id']}/tailored_resume"
            with patch("app.main.generate_document", side_effect=HTTPException(502, "Provider unavailable")):
                self.assertEqual(client.post("/generate?background=true", json=payload).status_code, 202)
                self.assertEqual(client.get(url).json()["message"], "Provider unavailable")
                self.assertEqual(client.get(url).json()["status"], "failed")
            payload["pack_id"] = str(uuid4())
            with patch("app.main.generate_document", side_effect=generated):
                self.assertEqual(client.post("/generate", json=payload).status_code, 200)
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
            engine.dispose()
