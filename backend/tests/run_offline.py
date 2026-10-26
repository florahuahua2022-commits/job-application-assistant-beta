"""Run backend tests without dotenv, credentials, or external connections."""
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def main():
    # Keep only process essentials; never inherit credentials or optional DB tests.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    environment.update(DATABASE_URL="sqlite:" + "//", DEPLOYMENT_MODE="local")
    os.environ.clear()
    os.environ.update(environment)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    def denied(*args, **kwargs):
        raise AssertionError("External connections are disabled in offline tests")

    original_connect = socket.socket.connect

    def connect(self, address):
        # Windows implements asyncio's internal socketpair through loopback TCP.
        fallback = getattr(socket, "_fallback_socketpair", None)
        if fallback and sys._getframe(1).f_code is fallback.__code__:
            return original_connect(self, address)
        return denied()

    from pydantic_settings import BaseSettings
    original_init = BaseSettings.__init__

    def without_dotenv(self, *args, **kwargs):
        kwargs["_env_file"] = None
        original_init(self, *args, **kwargs)

    with patch.object(BaseSettings, "__init__", without_dotenv), \
         patch.object(socket.socket, "connect", connect), \
         patch.object(socket.socket, "connect_ex", denied), \
         patch.object(socket, "create_connection", denied):
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        def local_database_only(dialect, connection_record, args, kwargs):
            if dialect.name != "sqlite":
                raise AssertionError("Only isolated SQLite fixtures are permitted")
            database = args[0] if args else kwargs.get("database", ":memory:")
            if database != ":memory:":
                # Existing concurrency tests explicitly create disposable temp files.
                if not Path(database).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                    raise AssertionError("SQLite file fixtures must be in the temporary directory")

        event.listen(Engine, "do_connect", local_database_only)
        try:
            import pytest
            return pytest.main(["-p", "no:cacheprovider", *(sys.argv[1:] or ["backend/tests", "-q", "--tb=short"])])
        finally:
            event.remove(Engine, "do_connect", local_database_only)


if __name__ == "__main__":
    raise SystemExit(main())
