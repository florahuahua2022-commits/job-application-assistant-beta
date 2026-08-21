from io import BytesIO
import json
import logging
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.auth import get_current_user
from app.database import get_session
from app.main import app
from app.models import ApplicantProfile, GeneratedDocument, JobApplication, Resume


class AccountLifecycleTests(unittest.TestCase):
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
            owned = JobApplication(user_id=self.user, company="Owned", position_title="Role", job_description="PRIVATE JD")
            other = JobApplication(user_id=self.other, company="Other", position_title="Role", job_description="OTHER JD")
            session.add_all([
                ApplicantProfile(user_id=self.user, first_name="Alex", last_name="Morgan", phone="0400000000", email="private@example.com"),
                Resume(user_id=self.user, source_text="PRIVATE RESUME"), owned, other,
            ]); session.flush()
            session.add_all([
                GeneratedDocument(user_id=self.user, application_id=owned.id, document_type="cover_letter", content="PRIVATE LETTER"),
                GeneratedDocument(user_id=self.other, application_id=other.id, document_type="cover_letter", content="OTHER LETTER"),
            ]); session.commit()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear(); self.engine.dispose()

    def test_export_contains_complete_owned_data_and_no_other_user_records(self):
        response = self.client.get("/account/export")
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.content)) as archive:
            payload = json.loads(archive.read("account-data.json"))
            names = archive.namelist()
        serialized = json.dumps(payload)
        self.assertIn("PRIVATE RESUME", serialized); self.assertIn("PRIVATE JD", serialized); self.assertIn("PRIVATE LETTER", serialized)
        self.assertNotIn("OTHER JD", serialized); self.assertNotIn("OTHER LETTER", serialized)
        self.assertTrue(any(name.startswith("generated-documents/") for name in names))

    def test_export_requires_authenticated_user(self):
        app.dependency_overrides[get_current_user] = lambda: None
        self.assertEqual(self.client.get("/account/export").status_code, 401)

    def test_delete_cannot_target_another_user_and_removes_only_owned_rows(self):
        with patch("app.main.delete_supabase_auth_user") as auth_delete:
            response = self.client.request("DELETE", "/account", json={"confirmation": "DELETE MY ACCOUNT", "user_id": str(self.other)})
        self.assertEqual(response.status_code, 200); auth_delete.assert_called_once_with(self.user)
        with Session(self.engine) as session:
            self.assertIsNone(session.exec(select(JobApplication).where(JobApplication.user_id == self.user)).first())
            self.assertIsNotNone(session.exec(select(JobApplication).where(JobApplication.user_id == self.other)).first())

    def test_delete_requires_exact_destructive_confirmation(self):
        with patch("app.main.delete_supabase_auth_user") as auth_delete:
            response = self.client.request("DELETE", "/account", json={"confirmation": "delete"})
        self.assertEqual(response.status_code, 400); auth_delete.assert_not_called()

    def test_structured_request_log_does_not_include_sensitive_payload(self):
        records = []
        handler = logging.Handler(); handler.emit = records.append
        logger = logging.getLogger("job_assistant.operations"); previous = logger.level; logger.setLevel(logging.INFO); logger.addHandler(handler)
        try:
            self.client.post("/applications/parse-ad", json={"raw_text": "PRIVATE JD private@example.com 0400000000"})
        finally:
            logger.removeHandler(handler); logger.setLevel(previous)
        rendered = "\n".join(record.getMessage() for record in records)
        self.assertNotIn("PRIVATE JD", rendered); self.assertNotIn("private@example.com", rendered); self.assertNotIn("0400000000", rendered)
        self.assertIn("request_id", rendered); self.assertIn("duration_ms", rendered)


if __name__ == "__main__":
    unittest.main()
