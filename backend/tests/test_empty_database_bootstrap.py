import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


@unittest.skipUnless(os.getenv("PACK_CREDIT_TEST_DATABASE_URL"), "requires PostgreSQL 16 test service")
class EmptyDatabaseBootstrapTests(unittest.TestCase):
    def test_baseline_applies_in_dependency_order_without_migration_history(self):
        import psycopg
        from scripts.empty_database_bootstrap import apply_baseline, inspect_target, prepare_cli_workdir

        url = os.environ["PACK_CREDIT_TEST_DATABASE_URL"]
        with psycopg.connect(url) as connection:
            self.assertTrue(connection.execute("show server_version_num").fetchone()[0].startswith("16"))
            self.assertIn("test", connection.info.dbname.lower())
            connection.execute("drop schema if exists supabase_migrations cascade")
            connection.execute("drop schema if exists public cascade")
            connection.execute("drop schema if exists auth cascade")
            connection.execute("create schema public")
            connection.execute("create schema auth")
            connection.execute("create table auth.users (id uuid primary key)")
            connection.execute("create or replace function auth.uid() returns uuid language sql stable as 'select null::uuid'")
            connection.commit()

        target = inspect_target(url)
        apply_baseline(url, target, allow_local_rehearsal=True)

        with psycopg.connect(url) as connection:
            discovery_context = connection.execute("""
                select count(*) from information_schema.columns
                where table_schema='public' and table_name='jobsource'
                  and column_name='discovery_context'
            """).fetchone()[0]
            completed_at = connection.execute("""
                select count(*) from information_schema.columns
                where table_schema='public' and table_name='generationusage'
                  and column_name='completed_at'
            """).fetchone()[0]
            migration_history = connection.execute("select to_regclass('supabase_migrations.schema_migrations')").fetchone()[0]
        self.assertEqual(discovery_context, 1)
        self.assertEqual(completed_at, 1)
        self.assertIsNone(migration_history)

        with TemporaryDirectory(dir=Path(__file__).parent) as temp:
            workdir = prepare_cli_workdir(Path(temp))
            names = [path.name for path in (workdir / "supabase" / "migrations").glob("*.sql")]
        versions = [name.split("_", 1)[0] for name in names]
        self.assertEqual(len(versions), len(set(versions)))
        self.assertIn("2026100903_stripe_checkout_webhook.sql", names)
        self.assertIn("20261015000100_ensure_jobsource_discovery_context.sql", names)

    def test_non_pooler_target_is_rejected_outside_local_rehearsal(self):
        from scripts.empty_database_bootstrap import inspect_target, validate_target

        target = inspect_target(os.environ["PACK_CREDIT_TEST_DATABASE_URL"])
        with self.assertRaisesRegex(RuntimeError, "Session pooler"):
            validate_target(target, allow_local_rehearsal=False)


if __name__ == "__main__":
    unittest.main()
