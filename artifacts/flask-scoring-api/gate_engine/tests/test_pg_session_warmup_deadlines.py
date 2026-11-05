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
