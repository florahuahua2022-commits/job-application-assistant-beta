import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import GenerationUsage, GlobalMonthlyUsage, PackCreditAccount, PackCreditLedger
from app.auth import get_current_user
from app.config import settings
from app.database import get_session
from app.main import app
from app.pack_credits import (
    complete_pack_credits,
    expire_pack_reservations,
    grant_manual_topup,
    release_pack_credits,
    reserve_pack_credits,
)


class PackCreditEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'credits.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
            poolclass=NullPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def reserve(self, user_id, pack_id, cost=1, limit=500):
        with Session(self.engine) as session:
            result = reserve_pack_credits(session, user_id, pack_id, cost, limit)
            session.commit()
            return result

    def test_new_user_gets_two_lifetime_credits_and_reservation_costs_one_or_two(self):
        normal_user, criteria_user = uuid4(), uuid4()

        normal = self.reserve(normal_user, uuid4(), 1)
        criteria = self.reserve(criteria_user, uuid4(), 2)

        with Session(self.engine) as session:
            self.assertEqual(normal.status, "reserved")
            self.assertEqual(criteria.status, "reserved")
            self.assertEqual((normal.balance, criteria.balance), (1, 0))
            self.assertEqual(session.get(PackCreditAccount, normal_user).balance, 1)
            self.assertEqual(session.get(PackCreditAccount, criteria_user).balance, 0)
            grants = session.exec(select(PackCreditLedger).where(PackCreditLedger.entry_type == "grant_free")).all()
            self.assertEqual(len(grants), 2)

    def test_same_pack_is_idempotent_and_does_not_debit_twice(self):
        user_id, pack_id = uuid4(), uuid4()

        first = self.reserve(user_id, pack_id)
        second = self.reserve(user_id, pack_id)

        with Session(self.engine) as session:
            debits = session.exec(select(PackCreditLedger).where(PackCreditLedger.entry_type == "debit_generation")).all()
            self.assertEqual((first.status, second.status), ("reserved", "existing"))
            self.assertEqual(session.get(PackCreditAccount, user_id).balance, 1)
            self.assertEqual(len(debits), 1)

    def test_historical_pack_without_new_ledger_is_not_retroactively_debited(self):
        user_id, pack_id = uuid4(), uuid4()
        with Session(self.engine) as session:
            session.add(PackCreditAccount(user_id=user_id, balance=2))
            session.add(GenerationUsage(
                user_id=user_id, pack_id=pack_id, status="completed", credit_cost=1,
                completed_at=datetime.now(timezone.utc),
            ))
            session.commit()

        result = self.reserve(user_id, pack_id)

        with Session(self.engine) as session:
            self.assertEqual(result.status, "existing")
            self.assertEqual(session.get(PackCreditAccount, user_id).balance, 2)
            self.assertFalse(session.exec(select(PackCreditLedger).where(
                PackCreditLedger.entry_type == "debit_generation",
            )).first())

    def test_complete_and_release_are_each_idempotent(self):
        completed_user, released_user = uuid4(), uuid4()
        completed_pack, released_pack = uuid4(), uuid4()
        self.reserve(completed_user, completed_pack)
        self.reserve(released_user, released_pack)

        with Session(self.engine) as session:
            self.assertTrue(complete_pack_credits(session, completed_user, completed_pack))
            self.assertFalse(complete_pack_credits(session, completed_user, completed_pack))
            self.assertTrue(release_pack_credits(session, released_user, released_pack))
            self.assertFalse(release_pack_credits(session, released_user, released_pack))
            session.commit()

        with Session(self.engine) as session:
            month = session.get(GlobalMonthlyUsage, date.today().replace(day=1))
            self.assertEqual((month.reserved_count, month.completed_count), (0, 1))
            self.assertEqual(session.get(PackCreditAccount, completed_user).balance, 1)
            self.assertEqual(session.get(PackCreditAccount, released_user).balance, 2)

    def test_expired_reservations_release_credit_once(self):
        user_id, pack_id = uuid4(), uuid4()
        self.reserve(user_id, pack_id)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            usage = session.exec(select(GenerationUsage).where(GenerationUsage.pack_id == pack_id)).one()
            usage.expires_at = now - timedelta(seconds=1)
            session.add(usage)
            session.commit()
            self.assertEqual(expire_pack_reservations(session, now), 1)
            self.assertEqual(expire_pack_reservations(session, now), 0)
            session.commit()
            self.assertEqual(session.get(PackCreditAccount, user_id).balance, 2)

    def test_manual_topup_validates_catalog_and_is_idempotent(self):
        user_id, admin_id = uuid4(), uuid4()
        with Session(self.engine) as session:
            balance = grant_manual_topup(session, user_id, "starter", "manual:receipt-1", admin_id, "Receipt 1")
            repeated = grant_manual_topup(session, user_id, "starter", "manual:receipt-1", admin_id, "Receipt 1")
            session.commit()
            self.assertEqual((balance, repeated), (10, 10))
            entry = session.exec(select(PackCreditLedger).where(PackCreditLedger.entry_type == "grant_manual_topup")).one()
            self.assertEqual((entry.credits_delta, entry.amount_cents, entry.package_code), (8, 10995, "starter"))
            with self.assertRaisesRegex(ValueError, "Idempotency key"):
                grant_manual_topup(session, user_id, "single", "manual:receipt-1", admin_id, "Wrong replay")
            with self.assertRaisesRegex(ValueError, "Unknown package"):
                grant_manual_topup(session, user_id, "unlimited", "manual:bad", admin_id, "Bad")

    def test_manual_topup_endpoint_requires_configured_admin(self):
        target, admin, ordinary_user = uuid4(), uuid4(), uuid4()

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        client = TestClient(app)
        payload = {
            "user_id": str(target), "package_code": "single",
            "idempotency_key": "manual:receipt-api", "note": "Receipt API",
        }
        try:
            app.dependency_overrides[get_current_user] = lambda: ordinary_user
            with patch.object(settings, "admin_user_ids", str(admin)):
                self.assertEqual(client.post("/admin/pack-credits/topup", json=payload).status_code, 403)

            app.dependency_overrides[get_current_user] = lambda: admin
            with patch.object(settings, "admin_user_ids", str(admin)):
                response = client.post("/admin/pack-credits/topup", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["balance"], 3)
        finally:
            app.dependency_overrides.clear()

    def test_concurrent_requests_for_last_credit_only_reserve_once(self):
        user_id = uuid4()
        with Session(self.engine) as session:
            session.add(PackCreditAccount(user_id=user_id, balance=1))
            session.commit()
        barrier = threading.Barrier(2)

        def compete():
            barrier.wait()
            return self.reserve(user_id, uuid4()).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: compete(), range(2)))

        with Session(self.engine) as session:
            self.assertEqual(sorted(statuses), ["insufficient_credits", "reserved"])
            self.assertEqual(session.get(PackCreditAccount, user_id).balance, 0)

    def test_concurrent_requests_for_final_global_slot_only_reserve_once(self):
        users = [uuid4(), uuid4()]
        month = date.today().replace(day=1)
        with Session(self.engine) as session:
            session.add(GlobalMonthlyUsage(month_start=month, completed_count=499))
            session.add_all(PackCreditAccount(user_id=user, balance=2) for user in users)
            session.commit()
        barrier = threading.Barrier(2)

        def compete(index):
            barrier.wait()
            return self.reserve(users[index], uuid4(), limit=500).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(compete, range(2)))

        with Session(self.engine) as session:
            counter = session.get(GlobalMonthlyUsage, month)
            self.assertEqual(sorted(statuses), ["global_limit", "reserved"])
            self.assertEqual(counter.reserved_count + counter.completed_count, 500)

    def test_global_limit_wins_even_when_personal_balance_is_available(self):
        user_id = uuid4()
        month = date.today().replace(day=1)
        with Session(self.engine) as session:
            session.add(PackCreditAccount(user_id=user_id, balance=2))
            session.add(GlobalMonthlyUsage(month_start=month, completed_count=500))
            session.commit()

        result = self.reserve(user_id, uuid4(), limit=500)

        with Session(self.engine) as session:
            self.assertEqual(result.status, "global_limit")
            self.assertEqual(session.get(PackCreditAccount, user_id).balance, 2)


class PostgreSQLPackCreditContractTests(unittest.TestCase):
    def test_database_functions_are_server_side_and_not_client_executable(self):
        sql = (Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261002_pack_credit_engine.sql").read_text(encoding="utf-8").lower()
        self.assertIn("for update", sql)
        self.assertIn("v_reserved + v_completed", sql)
        self.assertIn("revoke all on function", sql)
        self.assertIn("create or replace function public.reserve_pack_credits", sql)

    @unittest.skipUnless(os.getenv("PACK_CREDIT_TEST_DATABASE_URL"), "requires an isolated PostgreSQL test database")
    def test_real_postgres_connections_contend_for_one_credit_and_one_global_slot(self):
        import psycopg

        url = os.environ["PACK_CREDIT_TEST_DATABASE_URL"]
        with psycopg.connect(url) as connection:
            database_name = connection.info.dbname.lower()
            self.assertIn("test", database_name, "PACK_CREDIT_TEST_DATABASE_URL must identify an isolated test database")
            connection.execute("""
                do $$ begin
                    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
                    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
                end $$
            """)
            connection.execute("drop schema if exists public cascade")
            connection.execute("drop schema if exists auth cascade")
            connection.execute("create schema public")
            connection.execute("create schema auth")
            connection.execute("create table auth.users (id uuid primary key)")
            connection.execute("create function auth.uid() returns uuid language sql stable as 'select null::uuid'")
            connection.execute("""
                create table public.generationusage (
                    id bigint generated by default as identity primary key,
                    user_id uuid not null, application_id integer, pack_id uuid not null,
                    generated_at timestamptz not null, completed_at timestamptz
                )
            """)
            root = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
            connection.execute((root / "20261002_pack_credit_ledger.sql").read_text(encoding="utf-8"))
            connection.execute((root / "20261002_pack_credit_engine.sql").read_text(encoding="utf-8"))

        def run(user_id, pack_id):
            with psycopg.connect(url) as connection:
                barrier.wait()
                row = connection.execute(
                    "select * from public.reserve_pack_credits(%s, %s, 1, 500)", (user_id, pack_id),
                ).fetchone()
                connection.commit()
                return row[0]

        one_credit_user = uuid4()
        with psycopg.connect(url) as connection:
            connection.execute("insert into auth.users (id) values (%s)", (one_credit_user,))
            connection.execute("insert into public.packcreditaccount (user_id, balance) values (%s, 1)", (one_credit_user,))
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: run(one_credit_user, uuid4()), range(2)))
        self.assertEqual(sorted(statuses), ["insufficient_credits", "reserved"])

        users = [uuid4(), uuid4()]
        with psycopg.connect(url) as connection:
            connection.execute("insert into auth.users (id) values (%s), (%s)", users)
            connection.execute(
                "insert into public.packcreditaccount (user_id, balance) values (%s, 2), (%s, 2)", users,
            )
            connection.execute(
                "update public.globalmonthlyusage set reserved_count = 0, completed_count = 499"
            )
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda index: run(users[index], uuid4()), range(2)))
        self.assertEqual(sorted(statuses), ["global_limit", "reserved"])
        with psycopg.connect(url) as connection:
            total = connection.execute(
                "select reserved_count + completed_count from public.globalmonthlyusage"
            ).fetchone()[0]
        self.assertEqual(total, 500)


if __name__ == "__main__":
    unittest.main()
