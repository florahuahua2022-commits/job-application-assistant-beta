import json
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth import get_current_user
from app.config import settings
from app.database import get_session
from app.application_requirements import empty_application_requirements
from app.main import app, check_generation_quota, check_selection_criteria_credit, selection_criteria_access, update_application_requirements
from app.models import ApplicationRequirementsUpdate, CreditLedger, GeneratedDocument, GenerationUsage, JobApplication, Resume


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
                generated_at=datetime.now(timezone.utc),
            ))
            session.commit()
            with patch.object(settings, "deployment_mode", "online"):
                self.assertFalse(check_generation_quota(session, user_id, pack_id))

    def test_generation_usage_quota_uses_timezone_aware_utc_datetimes(self):
        class Result:
            def first(self): return None
            def one(self): return 0

        class RecordingSession:
            def __init__(self): self.datetimes = []
            def exec(self, statement):
                self.datetimes.extend(value for value in statement.compile().params.values() if isinstance(value, datetime))
                return Result()

        session = RecordingSession()
        with patch.object(settings, "deployment_mode", "online"):
            self.assertTrue(check_generation_quota(session, uuid4(), uuid4()))

        self.assertEqual(len(session.datetimes), 2)
        self.assertTrue(all(value.utcoffset() == timedelta(0) for value in session.datetimes))
        self.assertEqual(GenerationUsage(user_id=uuid4(), pack_id=uuid4()).generated_at.utcoffset(), timedelta(0))

    def test_generated_document_timestamp_is_timezone_aware_utc(self):
        document = GeneratedDocument(
            user_id=uuid4(), application_id=1, document_type="tailored_resume", content="Draft"
        )

        self.assertEqual(document.created_at.utcoffset(), timedelta(0))

    def test_resume_timestamps_are_timezone_aware_utc(self):
        resume = Resume(source_text="Resume")

        self.assertEqual(resume.created_at.utcoffset(), timedelta(0))
        self.assertEqual(resume.updated_at.utcoffset(), timedelta(0))

    def test_application_requirements_update_uses_timezone_aware_utc(self):
        application = JobApplication(
            id=1,
            company="Private",
            position_title="Administration & Warehouse Assistant",
            job_description="General duties",
            application_requirements_json=json.dumps(empty_application_requirements("General duties")),
        )
        requirements = empty_application_requirements("General duties")
        requirements["documents"]["cover_letter"].update(
            requirement="required", format="standalone", basis="user_confirmed"
        )
        session = unittest.mock.Mock()

        with patch("app.main.get_for_user", return_value=application):
            update_application_requirements(
                1,
                ApplicationRequirementsUpdate(
                    action="correct",
                    documents=requirements["documents"],
                    additional_documents=[],
                ),
                session,
                uuid4(),
            )

        self.assertEqual(application.updated_at.utcoffset(), timedelta(0))

    def test_daily_pack_limit_stops_a_new_pack(self):
        user_id = uuid4()
        with Session(self.engine) as session:
            session.add(GenerationUsage(
                user_id=user_id,
                pack_id=uuid4(),
                generated_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            ))
            session.commit()
            with patch.object(settings, "deployment_mode", "online"), patch.object(
                settings, "daily_pack_limit_per_user", 1
            ):
                with self.assertRaisesRegex(Exception, "Today's beta limit"):
                    check_generation_quota(session, user_id, uuid4())

    def test_incomplete_pack_does_not_consume_daily_quota(self):
        user_id = uuid4()
        with Session(self.engine) as session:
            session.add(GenerationUsage(
                user_id=user_id,
                pack_id=uuid4(),
                generated_at=datetime.now(timezone.utc),
            ))
            session.commit()
            with patch.object(settings, "deployment_mode", "online"), patch.object(
                settings, "daily_pack_limit_per_user", 1
            ):
                self.assertTrue(check_generation_quota(session, user_id, uuid4()))

    def test_pack_credit_models_are_separate_from_selection_criteria_credits(self):
        models = importlib.import_module("app.models")

        account = models.PackCreditAccount(user_id=uuid4(), balance=2)
        ledger = models.PackCreditLedger(
            user_id=account.user_id,
            entry_type="grant_free",
            credits_delta=2,
            idempotency_key=f"grant-free:{account.user_id}",
        )
        month = models.GlobalMonthlyUsage(month_start=datetime(2026, 10, 1, tzinfo=timezone.utc).date())

        self.assertEqual(account.balance, 2)
        self.assertEqual(ledger.credits_delta, 2)
        self.assertEqual(month.reserved_count, 0)
        self.assertEqual(month.completed_count, 0)
        self.assertNotEqual(models.PackCreditLedger.__tablename__, models.CreditLedger.__tablename__)

    def test_manual_topup_catalog_is_validation_metadata_without_payment_dependencies(self):
        pack_credits = importlib.import_module("app.pack_credits")

        self.assertEqual(pack_credits.PACK_CATALOG, {
            "single": {"credits": 1, "amount_cents": 1695, "currency": "AUD"},
            "starter": {"credits": 8, "amount_cents": 10995, "currency": "AUD"},
            "job_search": {"credits": 18, "amount_cents": 19900, "currency": "AUD"},
        })
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("stripe", requirements)

    def test_pack_credit_migration_has_constraints_rls_and_historical_usage_mapping(self):
        migration = (
            Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261002_pack_credit_ledger.sql"
        ).read_text(encoding="utf-8").lower()

        for table in ("packcreditaccount", "packcreditledger", "globalmonthlyusage"):
            self.assertIn(f"create table if not exists public.{table}", migration)
        for entry_type in ("grant_free", "grant_manual_topup", "debit_generation", "release"):
            self.assertIn(entry_type, migration)
        self.assertIn("enable row level security", migration)
        self.assertIn("completed_at is not null then 'completed'", migration)
        self.assertIn("completed_at is null then 'released'", migration)
        self.assertIn("from auth.users", migration)
        self.assertIn("'grant_free'", migration)
        self.assertIn("'grant-free:' || id::text", migration)
        self.assertIn("unique", migration)
        self.assertNotIn("grant usage, select on all sequences", migration)

    def test_local_upgrade_maps_historical_generation_usage_status(self):
        from app import database

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        tables = [table for table in SQLModel.metadata.sorted_tables if table.name != "generationusage"]
        SQLModel.metadata.create_all(engine, tables=tables)
        user_id, completed_pack, incomplete_pack = uuid4(), uuid4(), uuid4()
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE generationusage (
                    id INTEGER PRIMARY KEY, user_id CHAR(32) NOT NULL, application_id INTEGER,
                    pack_id CHAR(32) NOT NULL, generated_at DATETIME NOT NULL, completed_at DATETIME
                )
            """))
            connection.execute(text("""
                INSERT INTO generationusage (id, user_id, pack_id, generated_at, completed_at)
                VALUES (1, :user_id, :completed_pack, :generated_at, :completed_at),
                       (2, :user_id, :incomplete_pack, :generated_at, NULL)
            """), {
                "user_id": user_id.hex,
                "completed_pack": completed_pack.hex,
                "incomplete_pack": incomplete_pack.hex,
                "generated_at": now,
                "completed_at": now,
            })
        with patch.object(database, "engine", engine):
            database.create_db_and_tables()
        columns = {column["name"] for column in inspect(engine).get_columns("generationusage")}
        with engine.connect() as connection:
            statuses = connection.execute(text("SELECT status FROM generationusage ORDER BY id")).scalars().all()

        self.assertTrue({"status", "credit_cost", "reserved_at", "expires_at", "released_at", "usage_month"} <= columns)
        self.assertEqual(statuses, ["completed", "released"])
        engine.dispose()

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
