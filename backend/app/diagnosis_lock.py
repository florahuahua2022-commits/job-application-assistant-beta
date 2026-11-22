from contextlib import contextmanager
from hashlib import blake2b
from threading import Lock

from sqlalchemy import text


class DiagnosisAlreadyRunning(RuntimeError):
    pass


_local_guard = Lock()
_local_keys: set[int] = set()


def diagnosis_lock_key(user_id, application_id: int) -> int:
    payload = f"{user_id or 'local'}:{application_id}".encode()
    return int.from_bytes(blake2b(payload, digest_size=8).digest(), "big", signed=True)


@contextmanager
def diagnosis_lock(session, user_id, application_id: int):
    key = diagnosis_lock_key(user_id, application_id)
    engine = session.get_bind()
    if engine.dialect.name != "postgresql":
        with _local_guard:
            if key in _local_keys:
                raise DiagnosisAlreadyRunning()
            _local_keys.add(key)
        try:
            yield
        finally:
            with _local_guard:
                _local_keys.discard(key)
        return

    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    acquired = False
    try:
        acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
        if not acquired:
            raise DiagnosisAlreadyRunning()
        yield
    finally:
        if acquired:
            try:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception:
                connection.invalidate()
        connection.close()
