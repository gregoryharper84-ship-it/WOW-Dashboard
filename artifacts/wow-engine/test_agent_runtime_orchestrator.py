"""End-to-end test of the ported Celery/coordinator/orchestrator pipeline
(packet sections 5, 14-16). conftest.py sets Celery's task_always_eager
session-wide, so apply_async() runs the task synchronously, in-process,
through the real durable_runner/coordinator/reducer code — no real Redis
broker needed. This is the test that actually proves the Task 12 port
(queue.py, orchestrator.py, coordinator.py, durable_runner.py, runner.py)
works together, not just that each module imports cleanly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime import repository
from agent_runtime.orchestrator import Orchestrator
from agent_runtime_test_support import FakeSupabaseClient


@pytest.fixture
def client(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr("ledger.get_client", lambda: fake)
    return fake


def _no_specialist_candidate(canonical_key: str = "WNBA:G1:REB") -> dict:
    """A candidate whose sport/market never matches the governed MLB event
    bridge and carries no capability record — the only path this runtime can
    prove end-to-end without a live fitted-model artifact wired in (that's
    Phase 4). It must terminalize as MODEL_UNAVAILABLE, never invent a
    probability."""
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=3)
    return {
        "canonical_key": canonical_key,
        "sport": "WNBA", "official_event_id": "wnba-g1", "participant": "Test Player",
        "market_family": "PLAYER_PROP", "stat_family": "REBOUNDS", "period": "FULL_GAME",
        "exact_line": 7.5, "side": "MORE",
        "event_start_utc": start.isoformat(),
        "evidence": {
            "candidate_identity": {}, "official_event": {}, "exact_market_identity": {},
            "game_log": [6, 8, 7], "box_score_log": [{"min": 30}], "role_status": "CONFIRMED",
            "role_timestamp": now.isoformat(), "source_attempts": [{"source": "test"}],
        },
    }


def test_run_with_no_candidates_reconciles_to_completed(client):
    row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of=datetime.now(timezone.utc).isoformat(), user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    result = Orchestrator(client).start_run(run=row, request={
        "run_type": "FULL_MODEL", "as_of": row["requested_as_of"], "user_timezone": "America/Chicago",
        "candidate_inputs": [], "discovery_enabled": False,
    })
    assert result["started"] is True
    final = repository.get_run(client, row["run_id"])
    assert final["status"] == "COMPLETED"
    assert final["reconciliation_status"] == "BALANCED"
    assert final["rows_in"] == 0


def test_run_with_one_ungoverned_candidate_reaches_model_unavailable(client):
    row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of=datetime.now(timezone.utc).isoformat(), user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    candidate = _no_specialist_candidate()
    result = Orchestrator(client).start_run(run=row, request={
        "run_type": "FULL_MODEL", "as_of": row["requested_as_of"], "user_timezone": "America/Chicago",
        "candidate_inputs": [candidate], "discovery_enabled": False,
    })
    assert result["started"] is True

    final = repository.get_run(client, row["run_id"])
    assert final["status"] == "COMPLETED_WITH_BLOCKERS"
    assert final["reconciliation_status"] == "BALANCED"
    assert final["rows_in"] == 1
    assert final["rows_rejected"] == 1

    candidates = repository.list_run_candidates(client, row["run_id"])
    assert len(candidates) == 1
    assert candidates[0]["terminal_label"] == "MODEL_UNAVAILABLE"
    assert candidates[0]["can_execute"] is False

    decisions = client.table("wow_agent_terminal_decisions").select("*").eq("run_id", row["run_id"]).execute().data
    assert len(decisions) == 1
    assert decisions[0]["probability_publishable"] is False


def test_duplicate_job_delivery_is_ignored_not_reapplied(client):
    """Redelivering an already-terminal job's message (Celery's at-least-once
    semantics) must not re-run the coordinator continuation or duplicate the
    terminal decision."""
    row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of=datetime.now(timezone.utc).isoformat(), user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    Orchestrator(client).start_run(run=row, request={
        "run_type": "FULL_MODEL", "as_of": row["requested_as_of"], "user_timezone": "America/Chicago",
        "candidate_inputs": [_no_specialist_candidate()], "discovery_enabled": False,
    })

    from agent_runtime.durable_runner import run_durable_body

    discovery_jobs = repository.list_jobs(client, row["run_id"], worker_id="wow.parallel-discovery-router")
    assert len(discovery_jobs) == 1
    job = discovery_jobs[0]
    assert job["status"] == "SUCCEEDED"

    envelope = {
        "contract_version": "wow.agent-job.v1", "run_id": row["run_id"], "job_id": job["job_id"],
        "candidate_id": None, "worker_id": "wow.parallel-discovery-router", "worker_version": "1.0.0",
        "as_of": row["requested_as_of"], "input_hash": job["input_hash"], "payload": {},
    }
    payload, retry_exc = run_durable_body(client, envelope)
    assert retry_exc is None
    assert payload["status"] == "DUPLICATE_DELIVERY_IGNORED"

    decisions = client.table("wow_agent_terminal_decisions").select("*").eq("run_id", row["run_id"]).execute().data
    assert len(decisions) == 1  # unchanged by the redelivery
