"""Live PostgreSQL contract for the cross-worker Rundown budget RPC.

Opt-in through WOW_RUNDOWN_ATOMIC_DB_TEST=1 on an ephemeral CI Postgres.
Never connects to production and never uses application credentials.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("WOW_RUNDOWN_ATOMIC_DB_TEST") != "1",
    reason="requires disposable PostgreSQL service",
)


def _pg():
    import psycopg
    return psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
        dbname=os.getenv("PGDATABASE", "wowtest"),
        autocommit=True,
    )


@pytest.fixture(scope="module", autouse=True)
def migrated():
    if os.getenv("WOW_RUNDOWN_ATOMIC_DB_TEST") != "1":
        yield
        return
    sql = (Path(__file__).resolve().parents[1] /
           "migrations/20261009_v17_rundown_atomic_budget.sql").read_text()
    with _pg() as conn:
        for role in ("anon", "authenticated", "service_role"):
            conn.execute(
                "do $$ begin if not exists (select from pg_roles where rolname = '" +
                role + "') then create role " + role + "; end if; end $$"
            )
        conn.execute(sql)
    yield


def _reserve(rid, limit=1, points=100):
    with _pg() as conn:
        result = conn.execute(
            "select public.wow_rundown_reserve_call(%s, (now() at time zone 'UTC')::date, %s, %s)",
            (rid, limit, points),
        ).fetchone()[0]
        return result


def _finish(rid, points):
    with _pg() as conn:
        return conn.execute(
            "select public.wow_rundown_finish_call(%s, %s)",
            (rid, points),
        ).fetchone()[0]


def test_atomic_reservation_across_independent_postgres_connections():
    ids = [uuid4().hex for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        replies = list(pool.map(_reserve, ids))
    accepted = [i for i, v in enumerate(replies) if v["allowed"]]
    assert len(accepted) == 1
    assert all(v["code"] == "PAID_PROVIDER_USAGE_UNRECONCILED" for i, v in enumerate(replies) if i not in accepted)
    chosen = ids[accepted[0]]
    assert _finish(chosen, 15)["ok"]
    assert _reserve(uuid4().hex)["code"] == "PAID_PROVIDER_BUDGET_EXHAUSTED"
    assert _finish(chosen, 15)["ok"] is False


def test_request_identity_validation_and_role_permissions():
    malformed = _reserve("not-a-request-id", limit=100)
    assert malformed["allowed"] is False
    with _pg() as conn:
        anonymous = conn.execute(
            "select has_function_privilege('anon', 'public.wow_rundown_reserve_call(text,date,integer,bigint)', 'EXECUTE')"
        ).fetchone()[0]
        assert anonymous is False
        assert conn.execute(
            "select has_function_privilege('service_role', 'public.wow_rundown_reserve_call(text,date,integer,bigint)', 'EXECUTE')"
        ).fetchone()[0] is True
