"""Count actual SQLite result rows/bytes, not ORM objects or HTTP bytes."""
import sqlite3

from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine


def measured_engine(records):
    class Cursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            self.record = {"sql": sql, "columns": tuple(c[0] for c in self.description or ()), "rows": 0, "bytes": 0}
            records.append(self.record)
            return result

        def count(self, rows):
            for row in rows:
                self.record["rows"] += 1
                self.record["bytes"] += sum(
                    len(value) if isinstance(value, bytes) else len(str(value).encode("utf-8"))
                    for value in row if value is not None
                )

        def fetchone(self):
            row = super().fetchone()
            self.count([] if row is None else [row])
            return row

        def fetchmany(self, size=None):
            rows = super().fetchmany() if size is None else super().fetchmany(size)
            self.count(rows)
            return rows

        def fetchall(self):
            rows = super().fetchall()
            self.count(rows)
            return rows

    class Connection(sqlite3.Connection):
        def cursor(self, factory=Cursor):
            return super().cursor(factory)

    return create_engine(
        "sqlite:" + "//", poolclass=StaticPool,
        creator=lambda: sqlite3.connect(":memory:", factory=Connection, check_same_thread=False),
    )
