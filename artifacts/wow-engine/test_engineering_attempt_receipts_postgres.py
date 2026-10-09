"""Real-Postgres contract for migrations/20261009_v17_engineering_attempt_receipts.sql.

Charter §8: every engineering attempt gets an append-only receipt. This test
executes the migration (twice, for idempotency) against the ephemeral Postgres 16
service in wow-engine-verify and proves the immutability, least-privilege and
typed-disposition guarantees with real SQL rather than source-string checks.

Gated on WOW_AGENT_RUNTIME_INTEGRATION=1 like the other real-Postgres suite;
CI runs it in the "agent runtime integration" job.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("WOW_AGENT_RUNTIME_INTEGRATION") != "1",
    reason="real Postgres required (WOW_AGENT_RUNTIME_INTEGRATION=1)",
)

MIGRATION = Path(__file__).parent / "migrations" / "20261009_v17_engineering_attempt_receipts.sql"
TABLE = "public.wow_engineering_attempt_receipts"
SHA_A = "a" * 40
SHA_B = "b" * 40


def _conn():
    return psycopg.connect(os.environ["AGENT_RUNTIME_POSTGRES_DSN"], autocommit=True)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    with _conn() as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(
                f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') "
                f"then create role {role} nologin; end if; end $$;"
            )
        cur.execute("alter role service_role bypassrls")
        # Pre-existing table whose excess client grants this migration revokes.
        cur.execute("create table if not exists public.wow_engineering_backlog (ticket_id text)")
        cur.execute("grant all on public.wow_engineering_backlog to anon, authenticated, service_role")
        sql = MIGRATION.read_text()
        cur.execute(sql)
        cur.execute(sql)  # idempotent re-apply


def _insert(cur, **overrides):
    row = {
        "incident_id": f"INC-{uuid.uuid4().hex[:8]}",
        "provider": "claude",
        "role": "implementer",
        "worker_run_id": "run-1",
        "disposition": "IN_PROGRESS",
    }
    row.update(overrides)
    cols = ", ".join(row)
    params = ", ".join(["%s"] * len(row))
    cur.execute(f"insert into {TABLE} ({cols}) values ({params}) returning receipt_id", list(row.values()))
    return cur.fetchone()[0]


def _seed_row() -> uuid.UUID:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        return _insert(cur)


def test_service_role_can_insert_and_read():
    rid = _seed_row()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        cur.execute(f"select can_execute from {TABLE} where receipt_id=%s", (rid,))
        assert cur.fetchone() == (False,)


@pytest.mark.parametrize("role", [None, "service_role"])
@pytest.mark.parametrize("stmt", ["update {t} set next_action='mutated' where receipt_id=%s",
                                  "delete from {t} where receipt_id=%s"])
def test_existing_rows_are_immutable_for_every_role(role, stmt):
    rid = _seed_row()
    with _conn() as conn, conn.cursor() as cur:
        if role:
            cur.execute(f"set role {role}")
        with pytest.raises(psycopg.Error) as exc:
            cur.execute(stmt.format(t=TABLE), (rid,))
        # service_role lacks the grant; owner/superuser hits the trigger.
        assert ("WOW_ENGINEERING_ATTEMPT_RECEIPT_APPEND_ONLY" in str(exc.value)
                or "permission denied" in str(exc.value))
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"select next_action from {TABLE} where receipt_id=%s", (rid,))
        assert cur.fetchone() == (None,)


def test_truncate_fails_closed_even_for_owner():
    _seed_row()
    with _conn() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error, match="WOW_ENGINEERING_ATTEMPT_RECEIPT_APPEND_ONLY"):
            cur.execute(f"truncate {TABLE}")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_have_no_access(role):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"set role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(f"select 1 from {TABLE}")


@pytest.mark.parametrize("overrides,constraint", [
    ({"can_execute": True}, "can_execute"),
    ({"disposition": "MODEL_UNAVAILABLE"}, "disposition"),
    ({"head_sha": "abc"}, "head_sha"),
    ({"disposition": "BLOCKED_WITH_EXACT_REASON", "typed_blocker": "POLICY_DENIAL"}, "blocked_is_typed"),
    # Regression: a NULL qa_decision must not satisfy the verified-evidence check.
    ({"disposition": "FIXED_AND_VERIFIED", "deployed_sha": SHA_A}, "verified_has_evidence"),
    ({"disposition": "FIXED_AND_VERIFIED", "deployed_sha": SHA_A, "qa_decision": "HOLD"}, "verified_has_evidence"),
    ({"disposition": "FIXED_AND_VERIFIED", "qa_decision": "PASS"}, "verified_has_evidence"),
    ({"disposition": "PR_CREATED", "pr_number": 5}, "pr_has_identity"),
])
def test_invalid_receipts_rejected(overrides, constraint):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
            _insert(cur, **overrides)


def test_valid_terminal_receipts_and_supersession():
    prior = _seed_row()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        _insert(cur, disposition="PR_CREATED", pr_number=7, head_sha=SHA_B, supersedes_receipt_id=prior)
        _insert(cur, disposition="FIXED_AND_VERIFIED", deployed_sha=SHA_A, qa_decision="PASS")
        _insert(cur, disposition="BLOCKED_WITH_EXACT_REASON", typed_blocker="POLICY_DENIAL",
                blocker_owner="repository_owner", revisit_trigger="owner grants release identity")


def test_backlog_client_grants_revoked():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from information_schema.role_table_grants "
            "where table_name='wow_engineering_backlog' and grantee in ('anon','authenticated')"
        )
        assert cur.fetchone() == (0,)
