import unittest
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.config import settings
from app.database import get_session
from app.main import app, check_generation_quota, check_selection_criteria_credit, selection_criteria_access
from app.models import CreditLedger, GenerationUsage


class OnlineSecurityTests(unittest.TestCase):
    def setUp(self):
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
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_online_api_requires_sign_in(self):
        with patch.object(settings, "deployment_mode", "online"):
            response = self.client.get("/applications")

        self.assertEqual(response.status_code, 401)

    def test_existing_pack_does_not_consume_quota_twice(self):
        user_id = uuid4()
        pack_id = uuid4()
        with Session(self.engine) as session:
            session.add(GenerationUsage(
                user_id=user_id,
                pack_id=pack_id,
                generated_at=datetime.utcnow(),
            ))
            session.commit()
            with patch.object(settings, "deployment_mode", "online"):
                self.assertFalse(check_generation_quota(session, user_id, pack_id))

    def test_daily_pack_limit_stops_a_new_pack(self):
        user_id = uuid4()
        with Session(self.engine) as session:
            session.add(GenerationUsage(
                user_id=user_id,
                pack_id=uuid4(),
                generated_at=datetime.utcnow(),
            ))
            session.commit()
            with patch.object(settings, "deployment_mode", "online"), patch.object(
                settings, "daily_pack_limit_per_user", 1
            ):
                with self.assertRaisesRegex(Exception, "Today's beta limit"):
                    check_generation_quota(session, user_id, uuid4())

    def test_new_user_receives_two_selection_criteria_credits(self):
        user_id = uuid4()
        with Session(self.engine) as session, patch.object(settings, "deployment_mode", "online"):
            access = selection_criteria_access(session, user_id)

        self.assertEqual(access.remaining_credits, 2)
        self.assertEqual(access.referral_code, str(user_id))

    def test_selection_criteria_generation_requires_remaining_credit(self):
        user_id = uuid4()
        with Session(self.engine) as session:
            session.add_all([
                CreditLedger(user_id=user_id, delta=-1, reason="generation", idempotency_key="used-1"),
                CreditLedger(user_id=user_id, delta=-1, reason="generation", idempotency_key="used-2"),
            ])
            session.commit()
            with patch.object(settings, "deployment_mode", "online"):
                with self.assertRaisesRegex(Exception, "No Selection Criteria credits"):
                    check_selection_criteria_credit(session, user_id, uuid4())


if __name__ == "__main__":
    unittest.main()
