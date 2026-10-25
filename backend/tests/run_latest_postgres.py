"""Execute the same latest-document tests on a disposable PostgreSQL 16 service."""
import argparse
import ast
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--baseline", action="store_true")
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        raise SystemExit("A dedicated unprivileged fixture port is required")
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    environment.update(DATABASE_URL="sqlite:" + "//", DEPLOYMENT_MODE="local")
    os.environ.clear()
    os.environ.update(environment)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pydantic_settings import BaseSettings
    original_init, original_connect = BaseSettings.__init__, socket.socket.connect

    def without_dotenv(self, *args, **kwargs):
        kwargs["_env_file"] = None
        original_init(self, *args, **kwargs)

    def connect(self, address):
        if address != ("127.0.0.1", arguments.port):
            raise AssertionError("Only the disposable PostgreSQL fixture is allowed")
        return original_connect(self, address)

    def denied(*args, **kwargs):
        raise AssertionError("External connections are disabled")

    with patch.object(BaseSettings, "__init__", without_dotenv), patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", denied), patch.object(socket, "create_connection", denied):
        import psycopg
        from psycopg import sql
        from sqlalchemy import event
        from sqlalchemy.engine import Engine, URL
        from sqlmodel import create_engine
        import test_latest_document_reads as tests

        fixture = dict(host="127.0.0.1", port=arguments.port, user="egress_fixture", dbname="egress_fixture")
        with psycopg.connect(**fixture, autocommit=True) as connection:
            assert connection.info.server_version // 10000 == 16, "PostgreSQL 16 is mandatory"
        print("PostgreSQL 16 live fixture verified; credentials/dotenv are not inherited.")

        def fixture_only(dialect, record, args, kwargs):
            if dialect.name == "sqlite" and args and args[0] == ":memory:":
                return
            if dialect.name != "postgresql" or any(str(kwargs.get(k)) != str(v) for k, v in fixture.items()):
                raise AssertionError("Database target is not the disposable fixture")

        schemas = []

        def engine_factory(records):
            class Cursor(psycopg.Cursor):
                def execute(self, query, params=None, **kwargs):
                    result = super().execute(query, params, **kwargs)
                    self.record = {"sql": str(query), "columns": tuple(c.name for c in self.description or ()), "rows": 0, "bytes": 0}
                    records.append(self.record)
                    return result

                def count(self, rows):
                    for row in rows:
                        self.record["rows"] += 1
                        self.record["bytes"] += sum(len(v) if isinstance(v, bytes) else len(str(v).encode("utf-8")) for v in row if v is not None)

                def fetchone(self):
                    row = super().fetchone()
                    self.count([] if row is None else [row])
                    return row

                def fetchmany(self, size=0):
                    rows = super().fetchmany(size)
                    self.count(rows)
                    return rows

                def fetchall(self):
                    rows = super().fetchall()
                    self.count(rows)
                    return rows

            schema = "egress_" + uuid4().hex
            with psycopg.connect(**fixture, autocommit=True) as connection:
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            schemas.append(schema)
            target = URL.create("postgresql+psycopg", host=fixture["host"], port=fixture["port"], username=fixture["user"], database=fixture["dbname"])
            return create_engine(target, hide_parameters=True, connect_args={"cursor_factory": Cursor, "options": "-c search_path=" + schema})

        tests.LatestDocumentReadTests.engine_factory = staticmethod(engine_factory)
        if arguments.baseline:
            # Re-execute only these three functions from the immutable old main.
            source = subprocess.check_output(["git", "show", "dbce8ca:backend/app/main.py"], text=True, encoding="utf-8")
            names = {"latest_application_documents", "quality_check", "pack_review"}
            definitions = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
            assert len(definitions) == len(names)
            exec(compile(ast.Module(body=definitions, type_ignores=[]), "old-main-query-baseline", "exec"), tests.main.__dict__)
        event.listen(Engine, "do_connect", fixture_only)
        try:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(tests.LatestDocumentReadTests))
        finally:
            event.remove(Engine, "do_connect", fixture_only)
            with psycopg.connect(**fixture, autocommit=True) as connection:
                for schema in schemas:
                    connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        if arguments.baseline:
            if result.errors or not result.failures:
                return 1
            print("RED verified: old-main assertions failed, with no fixture/runtime errors.")
            return 0
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
