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
    """Mirrors the real Supabase role model closely enough for the real
    supabase-py PostgREST client to work against a plain Postgres+PostgREST
    pair in CI: service_role needs BYPASSRLS (Supabase grants this on every
    real project) plus ordinary table/sequence GRANTs, since RLS-with-no-
    policies only blocks the *policy* check — a role still needs the base
    privilege grant to touch a table at all, and a freshly created table has
    none by default."""
    schema_sql = Path("agent_runtime_schema.sql").read_text()
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(
                f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') "
                f"then create role {role} nologin; end if; end $$;"
            )
        cur.execute("alter role service_role bypassrls")
        cur.execute(schema_sql)
        cur.execute("grant usage on schema public to service_role")
        cur.execute("grant all on all tables in schema public to service_role")
        cur.execute("grant all on all sequences in schema public to service_role")
        cur.execute("alter default privileges in schema public grant all on tables to service_role")
        cur.execute("alter default privileges in schema public grant all on sequences to service_role")


def test_schema_applies_cleanly_to_real_postgres():
    """Running the migration twice must be idempotent (every statement uses
    IF NOT EXISTS / CREATE OR REPLACE / ON CONFLICT), and the seeded worker
    registry must land exactly as agent_runtime/registry.py expects."""
    _apply_schema()
    _apply_schema()  # idempotency check

    with psycopg.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute("select worker_id, authority_ceiling from public.wow_agent_worker_registry where enabled = true order by worker_id")
        rows = dict(cur.fetchall())

    from agent_runtime.registry import WORKERS

    assert len(rows) == len(WORKERS)
    for worker_id, spec in WORKERS.items():
        assert rows[worker_id] == spec.authority_ceiling


def test_wow_agent_complete_job_rpc_is_atomic_and_rejects_duplicates():
    """Exercises the plpgsql function directly against real Postgres — the
    one piece of this schema a fake client can never actually validate."""
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
    assert output_hash == "oh"  # the duplicate's payload never applied
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
    """Real HTTP -> real PostgREST-backed repository -> real Celery worker
    subprocess (started by the CI workflow, not this test) -> real Redis ->
    coordinator -> terminal reducer -> reconciliation, polled to completion."""
    _apply_schema()
    monkeypatch.setenv("WOW_ACTION_API_KEY", "ci-agent-runtime-key")

    from fastapi.testclient import TestClient
    import api_ncaaf_acceptance as prod

    client = TestClient(prod.app)
    ready = client.get("/health/ready")
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
