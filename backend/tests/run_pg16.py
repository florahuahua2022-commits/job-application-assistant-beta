"""Explicit local PG16 incident acceptance; never reads dotenv or credentials."""
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch


def create_test_engine(port, zone):
    from sqlalchemy import URL
    from sqlmodel import create_engine
    engine = create_engine(URL.create("postgresql+psycopg", username="postgres", host="127.0.0.1",
                                     port=int(port), database="jaa_datetime_test"),
                           connect_args={"options": f"-c timezone={zone}"})
    with engine.begin() as connection:
        assert connection.exec_driver_sql("SHOW server_version_num").scalar_one().startswith("16")
        assert connection.exec_driver_sql("SELECT current_database()").scalar_one() == "jaa_datetime_test"
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS auth CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public; CREATE SCHEMA auth")
        connection.exec_driver_sql("CREATE TABLE auth.users (id uuid PRIMARY KEY)")
        connection.exec_driver_sql("CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS 'SELECT NULL::uuid'")
        connection.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF; IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF; END $$")
        root = Path(__file__).resolve().parents[2] / "supabase/migrations"
        for migration in sorted(root.glob("*.sql")):
            if migration.name == "20261002_pack_credit_ledger.sql":
                # The repository startup upgrade supplies this pre-ledger column.
                connection.exec_driver_sql("ALTER TABLE public.generationusage ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
            # No parameter mapping: PL/pgSQL %ROWTYPE is SQL syntax, not a bind.
            connection.connection.driver_connection.execute(migration.read_text(encoding="utf-8"))
    from app import database
    with patch.object(database, "engine", engine):
        database.create_db_and_tables()
    return engine


class Options:
    def pytest_addoption(self, parser):
        parser.addoption("--incident-pg16-port", type=int)
        parser.addoption("--incident-timezone", choices=["UTC", "Australia/Perth"], default="UTC")


def main():
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Incident acceptance requires Python 3.13")
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    clean = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    os.environ.clear()
    os.environ.update(clean, DATABASE_URL="sqlite:" + "//", DEPLOYMENT_MODE="local",
                      PACK_CREDIT_TEST_DATABASE_URL="isolated-pg16-fixture")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    port = int(sys.argv[sys.argv.index("--incident-pg16-port") + 1])
    if not 49152 <= port <= 65535:
        raise RuntimeError("Use a dedicated ephemeral local PostgreSQL port")
    zone = sys.argv[sys.argv.index("--incident-timezone") + 1] if "--incident-timezone" in sys.argv else "UTC"
    if zone not in {"UTC", "Australia/Perth"}:
        raise RuntimeError("Unsupported fixture time zone")
    from pydantic_settings import BaseSettings
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    import psycopg
    original_settings, original_connect = BaseSettings.__init__, psycopg.connect
    original_socket = socket.socket.connect

    def no_dotenv(self, *args, **kwargs):
        original_settings(self, *args, **{**kwargs, "_env_file": None})

    def local_connect(conninfo="", **kwargs):
        # Existing PG contracts receive an opaque fixture marker, never a URL.
        if conninfo == "isolated-pg16-fixture" and not kwargs:
            conninfo = ""
            kwargs = {"host": "127.0.0.1", "port": port, "dbname": "jaa_datetime_test",
                      "user": "postgres", "options": f"-c timezone={zone}"}
        if conninfo or set(kwargs) - {"host", "port", "dbname", "user", "options", "context"} or kwargs.get("host") != "127.0.0.1" or int(kwargs.get("port", 0)) != port or kwargs.get("dbname") != "jaa_datetime_test":
            raise AssertionError("Only the explicit isolated PG16 fixture is permitted")
        return original_connect(**kwargs)

    def socket_connect(self, address):
        fallback = getattr(socket, "_fallback_socketpair", None)
        if fallback and sys._getframe(1).f_code is fallback.__code__:
            return original_socket(self, address)
        raise AssertionError("External connections are disabled")

    def guard(dialect, record, args, kwargs):
        if dialect.name != "postgresql":
            raise AssertionError("PG16 acceptance requires PostgreSQL")

    def denied(*args, **kwargs):
        raise AssertionError("External connections are disabled")

    with patch.object(BaseSettings, "__init__", no_dotenv), patch.object(psycopg, "connect", local_connect), patch.object(socket.socket, "connect", socket_connect), patch.object(socket.socket, "connect_ex", denied), patch.object(socket, "create_connection", denied):
        event.listen(Engine, "do_connect", guard)
        try:
            import pytest
            return pytest.main(["-p", "no:cacheprovider", *sys.argv[1:]], plugins=[Options()])
        finally:
            event.remove(Engine, "do_connect", guard)


if __name__ == "__main__":
    raise SystemExit(main())
