"""Fail-closed startup warmup deadlines for the PostgreSQL session exposure ledger.

Source tests only: no production database connection, migration, or wager.
"""
import os
import sys
import types

import pytest

from gate_engine import pg_session_ledger


class _FakeConnection:
    def __init__(self, *, reject_ddl=False):
        self.calls = []
        self.commit_calls = 0
        self.reject_ddl = reject_ddl

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self

    def execute(self, query):
        self.calls.append(query)
        if self.reject_ddl and query == pg_session_ledger._DDL:
            raise RuntimeError("synthetic lock timeout")

    def commit(self):
        self.commit_calls += 1


def _install_fake_psycopg2(monkeypatch, *, reject_ddl=False):
    observed = []
    conn = _FakeConnection(reject_ddl=reject_ddl)

    def connect(*args, **kwargs):
        observed.append((args, kwargs))
        return conn

    driver = types.ModuleType("psycopg2")
    driver.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg2", driver)
    return observed, conn


def test_startup_schema_uses_connection_and_transaction_local_ddl_deadlines(monkeypatch):
    observed, conn = _install_fake_psycopg2(monkeypatch)
    pg_session_ledger.ensure_table_exists("postgresql://fake/safety-test")

    assert observed == [(("postgresql://fake/safety-test",), {"connect_timeout": 5})]
    assert conn.calls == [
        "SET LOCAL lock_timeout = '3000ms'",
        "SET LOCAL statement_timeout = '10000ms'",
        pg_session_ledger._DDL,
    ]
    assert conn.commit_calls == 1


def test_startup_schema_uses_configured_url_without_disabling_deadlines(monkeypatch):
    observed, conn = _install_fake_psycopg2(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured/ci")
    pg_session_ledger.ensure_table_exists()

    assert observed == [(("postgresql://configured/ci",), {"connect_timeout": 5})]
    assert conn.calls[-1] == pg_session_ledger._DDL


def test_startup_ddl_failure_stays_failed_and_never_claims_commit(monkeypatch):
    observed, conn = _install_fake_psycopg2(monkeypatch, reject_ddl=True)
    with pytest.raises(RuntimeError, match="synthetic lock timeout"):
        pg_session_ledger.ensure_table_exists("postgresql://fake/locked")

    assert observed and observed[0][1]["connect_timeout"] == 5
    assert conn.calls[:2] == [
        "SET LOCAL lock_timeout = '3000ms'",
        "SET LOCAL statement_timeout = '10000ms'",
    ]
    assert conn.commit_calls == 0



def test_actual_postgres_conflicting_lock_times_out_and_recovery_succeeds():
    """CI-local PostgreSQL: 3s lock limit must stop stalled warmup DDL.

    A random isolated schema prevents modifying the main test ledger or any
    production DB. This test refuses non-local / non-CI databases outright.
    """
    import threading
    import time
    import uuid

    if os.environ.get("WOW_CI") != "1":
        pytest.skip("real DB lock test is CI-local only")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("CI-local DATABASE_URL is unavailable")
    psycopg2 = pytest.importorskip("psycopg2")
    parsed = psycopg2.extensions.parse_dsn(url)
    if parsed.get("host") not in ("localhost", "127.0.0.1") or parsed.get("dbname") != "wow_test":
        pytest.skip("refuse any database that is not the isolated CI PostgreSQL service")

    schema = "wow_v17_warmup_" + uuid.uuid4().hex[:16]
    identifier = psycopg2.sql.Identifier(schema)
    # For this test only, every warmup operation resolves unqualified DDL to
    # a disposable schema, never to the CI main or any real production table.
    schema_dsn = psycopg2.extensions.make_dsn(
        url, options=f"-csearch_path={schema}"
    )
    with psycopg2.connect(url, connect_timeout=5) as setup:
        with setup.cursor() as cur:
            cur.execute(psycopg2.sql.SQL("CREATE SCHEMA {}").format(identifier))

    locked = None
    try:
        pg_session_ledger.ensure_table_exists(schema_dsn)
        # Force the next warmup to rebuild the index; otherwise IF NOT EXISTS
        # may skip its DDL without needing a table lock.
        with psycopg2.connect(schema_dsn, connect_timeout=5) as edit:
            with edit.cursor() as cur:
                cur.execute("DROP INDEX idx_wse_expires")
        locked = psycopg2.connect(schema_dsn, connect_timeout=5)
        with locked.cursor() as cur:
            cur.execute("LOCK TABLE wow_session_exposure IN ACCESS EXCLUSIVE MODE")

        errors = []

        def attempt_warmup():
            try:
                pg_session_ledger.ensure_table_exists(schema_dsn)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=attempt_warmup, daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(timeout=8.0)
        exceeded_deadline = worker.is_alive()
        locked.rollback()  # release the lock even if the warmup hangs
        worker.join(timeout=5.0)
        elapsed = time.monotonic() - started

        assert not exceeded_deadline, f"warmup exceeded bounded lock wait ({elapsed:.2f}s)"
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], psycopg2.Error)
        assert errors[0].pgcode == "55P03", repr(errors[0])

        with psycopg2.connect(schema_dsn, connect_timeout=5) as check:
            with check.cursor() as cur:
                cur.execute("SELECT to_regclass('idx_wse_expires')")
                assert cur.fetchone()[0] is None, "failed DDL must not create the index"

        pg_session_ledger.ensure_table_exists(schema_dsn)
        with psycopg2.connect(schema_dsn, connect_timeout=5) as check:
            with check.cursor() as cur:
                cur.execute("SELECT to_regclass('idx_wse_expires')")
                assert cur.fetchone()[0] is not None
    finally:
        if locked is not None:
            locked.rollback()
            locked.close()
        with psycopg2.connect(url, connect_timeout=5) as cleanup:
            with cleanup.cursor() as cur:
                cur.execute(psycopg2.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(identifier))
