"""Real ephemeral Postgres + Redis + Celery integration for the WOW Agent
Runtime, retargeted from PR #33's private wow schema to this branch's
public.wow_agent_* tables during the convergence pass (see
agent_runtime_schema.sql). Skipped unless WOW_AGENT_RUNTIME_INTEGRATION=1 —
see .github/workflows/wow-engine-verify.yml, which runs this against real
service containers (Postgres 16, Redis 7, PostgREST).

Two things this file proves that the fake-client unit/integration tests
(test_agent_runtime_repository.py, test_agent_runtime_orchestrator.py, etc.)
cannot, because a fake client never parses or executes real SQL:

1. test_schema_applies_cleanly_to_real_postgres — agent_runtime_schema.sql,
   including the wow_agent_complete_job plpgsql function, is valid,
   idempotent SQL against a real Postgres 16 instance: every CHECK
   constraint, the RLS-enabled-with-no-policies posture, and the RPC's
   compile successfully.
2. test_durable_api_to_worker_to_terminal_reconciliation — the real
   supabase-py PostgREST client (agent_runtime/repository.py's actual
   production code path, not the fake) driven through a real Celery worker
   subprocess against real Redis, via the real HTTP API.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("WOW_AGENT_RUNTIME_INTEGRATION") != "1",
    reason="agent runtime integration only (real Postgres/Redis/PostgREST required)",
)


def _pg_dsn() -> str:
    return os.environ["AGENT_RUNTIME_POSTGRES_DSN"]


def _apply_schema() -> None:
    """Mirror the Supabase/PostgREST role model closely enough for CI.

    PostgREST connects as ``wow_agent_ci`` and switches to the JWT ``role``
    claim for each request.  The connection role therefore needs membership
    in the API roles in addition to the service role needing BYPASSRLS and
    base object privileges.  Missing that membership makes an otherwise
    healthy PostgREST instance fail every authenticated table request.
    """
    schema_sql = Path("agent_runtime_schema.sql").read_text()
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(
                f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') "
                f"then create role {role} nologin; end if; end $$;"
            )
        cur.execute("alter role service_role bypassrls")
        # The PostgREST authenticator/connection role must be allowed to SET ROLE
        # to the JWT role. This is how a real Supabase PostgREST deployment is
        # wired and was the missing piece in the first CI attempt.
        cur.execute("grant anon to wow_agent_ci")
        cur.execute("grant authenticated to wow_agent_ci")
        cur.execute("grant service_role to wow_agent_ci")
        cur.execute(schema_sql)
        cur.execute("grant usage on schema public to service_role")
        cur.execute("grant all on all tables in schema public to service_role")
        cur.execute("grant all on all sequences in schema public to service_role")
        cur.execute("grant execute on all functions in schema public to service_role")
        cur.execute("alter default privileges in schema public grant all on tables to service_role")
        cur.execute("alter default privileges in schema public grant all on sequences to service_role")
        cur.execute("alter default privileges in schema public grant execute on functions to service_role")
        # PostgREST boots before this DDL runs, so reload its schema cache after
        # the roles, objects, grants, and RPC are all present.
        cur.execute("notify pgrst, 'reload schema'")


def test_schema_applies_cleanly_to_real_postgres():
    """Migration is valid/idempotent and registry matches the code contract."""
    _apply_schema()
    _apply_schema()

    with psycopg.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute("select worker_id, authority_ceiling from public.wow_agent_worker_registry where enabled = true order by worker_id")
        rows = dict(cur.fetchall())

    from agent_runtime.registry import WORKERS

    assert len(rows) == len(WORKERS)
    for worker_id, spec in WORKERS.items():
        assert rows[worker_id] == spec.authority_ceiling


def test_wow_agent_complete_job_rpc_is_atomic_and_rejects_duplicates():
    """Exercise the real plpgsql atomic completion primitive."""
    _apply_schema()
    run_id = "11111111-1111-1111-1111-111111111111"
    job_id = "22222222-2222-2222-2222-222222222222"
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("delete from public.wow_agent_jobs where job_id = %s", (job_id,))
        cur.execute("delete from public.wow_agent_runs where run_id = %s", (run_id,))
        cur.execute(
            "insert into public.wow_agent_runs (run_id, idempotency_key, request_hash, run_type, "
            "requested_as_of, user_timezone, status, stage, governance_version) "
            "values (%s, 'k', 'h', 'FULL_MODEL', now(), 'UTC', 'CREATED', 'CREATED', 'TEST')",
            (run_id,),
        )
        cur.execute(
            "insert into public.wow_agent_jobs (job_id, run_id, worker_id, worker_version, "
            "idempotency_key, status, required, input_hash) "
            "values (%s, %s, 'wow.parallel-discovery-router', '1.0.0', 'jk', 'RUNNING', true, 'ih')",
            (job_id, run_id),
        )
        cur.execute(
            "select public.wow_agent_complete_job(%s, %s, null, 'wow.parallel-discovery-router', "
            "'1.0.0', 'wow.agent-output.v1', null, '{}'::jsonb, 'oh', 'SUCCEEDED', "
            "'RESEARCH_INTEREST', '[]'::jsonb, null)",
            (job_id, run_id),
        )
        first_applied = cur.fetchone()[0]
        cur.execute(
            "select public.wow_agent_complete_job(%s, %s, null, 'wow.parallel-discovery-router', "
            "'1.0.0', 'wow.agent-output.v1', null, '{\"different\":true}'::jsonb, 'oh2', 'SUCCEEDED', "
            "'RESEARCH_INTEREST', '[]'::jsonb, null)",
            (job_id, run_id),
        )
        duplicate_applied = cur.fetchone()[0]
        cur.execute("select status, output_hash from public.wow_agent_jobs where job_id = %s", (job_id,))
        status, output_hash = cur.fetchone()
        cur.execute("select count(*) from public.wow_agent_job_outputs where job_id = %s", (job_id,))
        output_count = cur.fetchone()[0]

    assert first_applied is True
    assert duplicate_applied is False
    assert status == "SUCCEEDED"
    assert output_hash == "oh"
    assert output_count == 1


def _candidate() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "canonical_key": "WNBA:ci-event-1:REB",
        "sport": "WNBA", "official_event_id": "ci-event-1", "participant": "CI Player",
        "market_family": "PLAYER_PROP", "stat_family": "REBOUNDS", "period": "FULL_GAME",
        "exact_line": 7.5, "side": "MORE",
        "event_start_utc": (now + timedelta(hours=2)).isoformat(),
        "evidence": {
            "candidate_identity": {}, "official_event": {}, "exact_market_identity": {},
            "game_log": [6, 8, 7], "box_score_log": [{"min": 30}], "role_status": "CONFIRMED",
            "role_timestamp": now.isoformat(), "source_attempts": [{"source": "CI_FIXTURE"}],
        },
    }


def test_durable_api_to_worker_to_terminal_reconciliation(monkeypatch):
    """Real HTTP -> PostgREST -> Redis/Celery -> reducer/reconciliation E2E."""
    _apply_schema()
    monkeypatch.setenv("WOW_ACTION_API_KEY", "ci-agent-runtime-key")

    from fastapi.testclient import TestClient
    import api_ncaaf_acceptance as prod
    import ledger

    probe_client = ledger.get_client()
    probe_client.table("wow_agent_runs").select("run_id").limit(1).execute()

    client = TestClient(prod.app)
    ready = None
    for _ in range(25):
        ready = client.get("/health/ready")
        if ready.status_code == 200:
            break
        time.sleep(0.2)
    assert ready.status_code == 200, ready.text
    assert ready.json()["worker_registry"] == "ok"
    assert ready.json()["queue"] == "ok"

    body = {
        "run_type": "FULL_MODEL", "as_of": datetime.now(timezone.utc).isoformat(),
        "user_timezone": "America/Chicago", "discovery_enabled": False,
        "candidate_inputs": [_candidate()], "can_execute": False,
    }
    headers = {"Idempotency-Key": "ci-agent-runtime-e2e"}
    first = client.post("/wow/runs", json=body, headers=headers)
    assert first.status_code == 202, first.text
    run_id = first.json()["run_id"]

    duplicate = client.post("/wow/runs", json=body, headers=headers)
    assert duplicate.status_code == 202
    assert duplicate.json()["run_id"] == run_id

    terminal = None
    for _ in range(100):
        response = client.get(f"/wow/runs/{run_id}/manifest")
        assert response.status_code == 200, response.text
        manifest = response.json()
        if manifest["terminal"]:
            terminal = manifest
            break
        time.sleep(0.2)

    assert terminal is not None, "run did not terminalize within the polling window"
    assert terminal["status"] == "COMPLETED_WITH_BLOCKERS"
    assert terminal["reconciliation"] == {"rows_in": 1, "rows_completed": 0, "rows_held": 0, "rows_rejected": 1, "balanced": True}
    assert terminal["candidates"][0]["terminal_label"] == "MODEL_UNAVAILABLE"
    assert terminal["candidates"][0]["can_execute"] is False

    with psycopg.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from public.wow_agent_terminal_decisions where run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Append-only engineering attempt receipts (Charter §8)
# migrations/20261009_v17_engineering_attempt_receipts.sql, executed against
# the same real Postgres 16 service. Lives in this file so it runs in the
# existing integration job without modifying the protected CI trust root.
# ---------------------------------------------------------------------------
import json  # noqa: E402
import uuid  # noqa: E402

RECEIPTS_MIGRATION = Path(__file__).parent / "migrations" / "20261009_v17_engineering_attempt_receipts.sql"
RECEIPTS_TABLE = "public.wow_engineering_attempt_receipts"
SHA_A = "a" * 40
SHA_B = "b" * 40


def _receipts_conn():
    return psycopg.connect(_pg_dsn(), autocommit=True)


@pytest.fixture(scope="module")
def receipts_schema():
    with _receipts_conn() as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(
                f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') "
                f"then create role {role} nologin; end if; end $$;"
            )
        cur.execute("alter role service_role bypassrls")
        # Pre-existing table whose excess client grants this migration revokes.
        cur.execute("create table if not exists public.wow_engineering_backlog (ticket_id text)")
        cur.execute("grant all on public.wow_engineering_backlog to anon, authenticated, service_role")
        sql = RECEIPTS_MIGRATION.read_text()
        cur.execute(sql)
        cur.execute(sql)  # idempotent re-apply


def _receipt_insert(cur, **overrides):
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
    cur.execute(f"insert into {RECEIPTS_TABLE} ({cols}) values ({params}) returning receipt_id", list(row.values()))
    return cur.fetchone()[0]


def _receipt_seed_row() -> uuid.UUID:
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        return _receipt_insert(cur)


def test_receipts_service_role_can_insert_and_read(receipts_schema):
    rid = _receipt_seed_row()
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        cur.execute(f"select can_execute from {RECEIPTS_TABLE} where receipt_id=%s", (rid,))
        assert cur.fetchone() == (False,)


@pytest.mark.parametrize("role", [None, "service_role"])
@pytest.mark.parametrize("stmt", ["update {t} set next_action='mutated' where receipt_id=%s",
                                  "delete from {t} where receipt_id=%s"])
def test_receipts_existing_rows_are_immutable_for_every_role(receipts_schema, role, stmt):
    rid = _receipt_seed_row()
    with _receipts_conn() as conn, conn.cursor() as cur:
        if role:
            cur.execute(f"set role {role}")
        with pytest.raises(psycopg.Error) as exc:
            cur.execute(stmt.format(t=RECEIPTS_TABLE), (rid,))
        # service_role lacks the grant; owner/superuser hits the trigger.
        assert ("WOW_ENGINEERING_ATTEMPT_RECEIPT_APPEND_ONLY" in str(exc.value)
                or "permission denied" in str(exc.value))
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select next_action from {RECEIPTS_TABLE} where receipt_id=%s", (rid,))
        assert cur.fetchone() == (None,)


def test_receipts_truncate_fails_closed_even_for_owner(receipts_schema):
    _receipt_seed_row()
    with _receipts_conn() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error, match="WOW_ENGINEERING_ATTEMPT_RECEIPT_APPEND_ONLY"):
            cur.execute(f"truncate {RECEIPTS_TABLE}")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_receipts_client_roles_have_no_access(receipts_schema, role):
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute(f"set role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(f"select 1 from {RECEIPTS_TABLE}")


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
def test_receipts_invalid_receipts_rejected(receipts_schema, overrides, constraint):
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
            _receipt_insert(cur, **overrides)


def test_receipts_valid_terminal_receipts_and_supersession(receipts_schema):
    prior = _receipt_seed_row()
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute("set role service_role")
        _receipt_insert(cur, disposition="PR_CREATED", pr_number=7, head_sha=SHA_B, supersedes_receipt_id=prior)
        _receipt_insert(cur, disposition="FIXED_AND_VERIFIED", deployed_sha=SHA_A, qa_decision="PASS")
        _receipt_insert(cur, disposition="BLOCKED_WITH_EXACT_REASON", typed_blocker="POLICY_DENIAL",
                blocker_owner="repository_owner", revisit_trigger="owner grants release identity")


def test_receipts_backlog_client_grants_revoked(receipts_schema):
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from information_schema.role_table_grants "
            "where table_name='wow_engineering_backlog' and grantee in ('anon','authenticated')"
        )
        assert cur.fetchone() == (0,)


def test_receipts_writer_round_trip_through_real_postgrest(receipts_schema):
    """v17/engineering_receipts.py writes and read-back-verifies through the real
    service-role PostgREST path, and the append-only triggers still hold there."""
    import time as _time
    from urllib.request import Request as _Request, urlopen as _urlopen
    from urllib.error import HTTPError as _HTTPError

    from v17 import engineering_receipts as er

    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute("grant usage on schema public to service_role")
        cur.execute("notify pgrst, 'reload schema'")
    receipt = dict(incident_id="1554", provider="claude", role="implementer",
                   worker_run_id="ci-postgrest", disposition="PR_CREATED",
                   pr_number=1552, head_sha=SHA_B, change_class="B", priority="P2",
                   tests=[{"suite": "receipts", "result": "pass"}])
    stored = None
    for _ in range(20):  # wait for the schema-cache reload
        try:
            stored = er.record_receipt(url, key, receipt)
            break
        except er.ReceiptError as exc:
            if exc.code != "RECEIPT_PERSISTENCE_REJECTED":
                raise
            _time.sleep(0.5)
    assert stored is not None and stored["can_execute"] is False

    # Constraint violations surface as a typed rejection, not "unavailable".
    with pytest.raises(er.ReceiptError, match="RECEIPT_PERSISTENCE_REJECTED"):
        er._call(er._request(er._endpoint(url), key, method="POST",
                             body=json.dumps({**receipt, "disposition": "MODEL_UNAVAILABLE"}).encode()), 10)

    # PATCH through the same service-role path is refused.
    patch = _Request(er._endpoint(url) + "?receipt_id=eq." + stored["receipt_id"],
                     data=b'{"next_action":"mutated"}', method="PATCH",
                     headers={"apikey": key, "Authorization": "Bearer " + key,
                              "Content-Type": "application/json"})
    with pytest.raises(_HTTPError):
        _urlopen(patch, timeout=10)
    with _receipts_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select next_action from {RECEIPTS_TABLE} where receipt_id=%s", (stored["receipt_id"],))
        assert cur.fetchone() == (None,)
