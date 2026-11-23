import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.diagnosis_lock import DiagnosisAlreadyRunning, diagnosis_lock, diagnosis_lock_key


class DiagnosisLockTests(unittest.TestCase):
    def test_key_is_stable_across_processes_and_distinguishes_jobs(self):
        user = UUID("00000000-0000-0000-0000-000000000123")
        expected = diagnosis_lock_key(user, 7)
        code = "from uuid import UUID; from app.diagnosis_lock import diagnosis_lock_key; print(diagnosis_lock_key(UUID('00000000-0000-0000-0000-000000000123'), 7))"
        actual = int(subprocess.check_output(
            [sys.executable, "-c", code], text=True, cwd=Path(__file__).resolve().parents[1],
        ).strip())
        self.assertEqual(actual, expected)
        self.assertNotEqual(expected, diagnosis_lock_key(user, 8))

    def test_sqlite_lock_rejects_duplicate_and_releases_after_exception(self):
        session = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
        with diagnosis_lock(session, None, 4):
            with self.assertRaises(DiagnosisAlreadyRunning):
                with diagnosis_lock(session, None, 4):
                    pass
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with diagnosis_lock(session, None, 4):
                raise RuntimeError("synthetic")
        with diagnosis_lock(session, None, 4):
            pass

    def test_postgres_lock_uses_and_releases_independent_connection(self):
        calls = []

        class Result:
            def scalar(self):
                return True

        class Connection:
            def execution_options(self, **values):
                calls.append(("execution_options", values))
                return self

            def execute(self, statement, values):
                calls.append((str(statement), values))
                return Result()

            def invalidate(self):
                calls.append(("invalidate", None))

            def close(self):
                calls.append(("close", None))

        connection = Connection()
        engine = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql"), connect=lambda: connection,
        )
        session = SimpleNamespace(get_bind=lambda: engine)

        with diagnosis_lock(session, "synthetic-user", 4):
            calls.append(("inside", None))

        self.assertEqual(calls[0], ("execution_options", {"isolation_level": "AUTOCOMMIT"}))
        self.assertIn("pg_try_advisory_lock", calls[1][0])
        self.assertEqual(calls[2], ("inside", None))
        self.assertIn("pg_advisory_unlock", calls[3][0])
        self.assertEqual(calls[4], ("close", None))


if __name__ == "__main__":
    unittest.main()
