"""Regression: Stage 2 provenance DDL cannot hang startup on a held PG lock."""

import threading
import pytest
from gate_engine import llp_stage2_tables as tables
from gate_engine.source_provenance import schema_migration


class Cursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql):
        self.conn.sql.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("SIMULATED_LOCK_TIMEOUT")
    def close(self): pass


class Connection:
    def __init__(self):
        self.sql, self.commits, self.closes, self.fail_on = [], 0, 0, None
    def cursor(self): return Cursor(self)
    def commit(self): self.commits += 1
    def close(self): self.closes += 1


@pytest.fixture
def bootstrap(monkeypatch):
    conn = Connection()
    monkeypatch.setattr(tables, "_TABLES_READY", False)
    monkeypatch.setattr(tables, "_TABLES_LAST_ERROR", None)
    monkeypatch.setattr(tables, "_TABLES_LOCK", threading.Lock())
    monkeypatch.setattr(tables, "_get_conn", lambda: conn)
    def migrated(connection):
        assert connection is conn
        connection.commit()  # Real provenance routine commits internally.
        return {"success": True, "errors": []}
    monkeypatch.setattr(schema_migration, "run_provenance_migration", migrated)
    return conn


def test_deadlines_are_session_scoped_before_commit_and_all_ddl(bootstrap):
    # RED before repair: unbounded CREATE runs before any timeout config.
    tables.ensure_all_tables()
    assert bootstrap.sql[:2] == [
        "SET lock_timeout = '3000ms'",
        "SET statement_timeout = '10000ms'",
    ]
    assert all(not q.startswith("SET LOCAL") for q in bootstrap.sql)
    assert tables._TABLES_READY is True
    assert tables._TABLES_LAST_ERROR is None
    assert bootstrap.commits == 3  # settings, provenance, table DDL
    assert bootstrap.closes == 1
    count = len(bootstrap.sql)
    tables.ensure_all_tables()
    assert len(bootstrap.sql) == count


def test_ddl_timeout_holds_readiness_and_closes_connection(bootstrap):
    bootstrap.fail_on = "CREATE TABLE IF NOT EXISTS llp_source_snapshots"
    tables.ensure_all_tables()
    assert tables._TABLES_READY is False
    assert "SIMULATED_LOCK_TIMEOUT" in (tables._TABLES_LAST_ERROR or "")
    assert bootstrap.closes == 1
    assert bootstrap.sql[:2] == [
        "SET lock_timeout = '3000ms'",
        "SET statement_timeout = '10000ms'",
    ]
