"""Phase 1 integration/contract tests, hitting the real HTTP routes on the
actual production app composition (api_prod.app), with a fake Supabase
client injected — same pattern as the existing test_api_prod.py suite.

Since the convergence pass with PR #33 (feature/wow-agent-runtime-v1),
POST /wow/runs starts real orchestration, and conftest.py runs Celery
eagerly (in-process, no real broker) for this whole test suite — so a run
created through the API can already be terminal by the time the response
comes back, unlike a real async deployment. Tests that need to observe or
act on a *nonterminal* run construct it directly via
agent_runtime.repository.create_run(), bypassing the API's orchestration
side effect, rather than through POST /wow/runs.

test_synchronous_fake_worker_fixture_completes_with_balanced_reconciliation
is Phase 1's exit criterion from the packet (section 22): a synchronous
fake-worker fixture completes with balanced reconciliation, proven through
the real run ledger, state machine, idempotency, reducer, and reconciliation
code — surfaced through the real polling endpoint, not just unit-tested in
isolation. It builds the run directly through the repository for the same
reason as above: this test manually drives fake jobs job-by-job and needs
the run to stay exactly where it puts it, not race ahead through real
orchestration.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import agent_runtime_api
import api_prod
from agent_runtime import idempotency, repository
from agent_runtime.reconciliation import reconcile_from_ceilings
from agent_runtime.reducer import RequiredJobResult, reduce_candidate
from agent_runtime.registry import WORKERS
from agent_runtime_test_support import FakeSupabaseClient

client = TestClient(api_prod.app)

_RUN_PAYLOAD = {
    "run_type": "FULL_MODEL",
    "as_of": "2026-08-29T00:00:00Z",
    "user_timezone": "America/Chicago",
    "lanes": ["PLAYER_PROPS"],
    "sports": ["MLB"],
    "discovery_enabled": False,
    "candidate_inputs": [],
}


def _seed_registry(fake: FakeSupabaseClient) -> None:
    for spec in WORKERS.values():
        fake.table("wow_agent_worker_registry").insert({
            "worker_id": spec.worker_id, "worker_version": spec.worker_version,
            "contract_version": spec.contract_version, "implementation_type": spec.implementation_type,
            "authority_ceiling": spec.authority_ceiling, "enabled": True,
        }).execute()


def _created_run(fake: FakeSupabaseClient, idempotency_key: str = "k1") -> dict:
    """Build a run directly through the repository, bypassing POST
    /wow/runs's orchestration side effect, so it stays exactly at CREATED
    for tests that need a controllable nonterminal fixture."""
    row, _ = repository.create_run(
        fake, idempotency_key=idempotency_key, request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    return row


def test_health_live_has_no_dependencies():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "can_execute": False}


def test_health_ready_all_dependencies_ok(monkeypatch):
    fake = FakeSupabaseClient()
    _seed_registry(fake)
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setenv("REDIS_URL", "redis://fake-for-test/0")

    class _FakePing:
        def ping(self):
            return True

    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: _FakePing())

    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "database": "ok", "queue": "ok", "worker_registry": "ok", "can_execute": False}


def test_health_ready_fails_closed_when_database_unreachable(monkeypatch):
    def _broken_client():
        raise RuntimeError("no database configured")

    monkeypatch.setattr(agent_runtime_api, "get_client", _broken_client)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["database"] == "unreachable"


def test_health_ready_fails_closed_when_registry_mismatched(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setenv("REDIS_URL", "redis://fake-for-test/0")
    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: type("P", (), {"ping": lambda self: True})())

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["worker_registry"] == "mismatch_or_unreachable"


def test_create_run_rejects_can_execute_true(monkeypatch):
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: FakeSupabaseClient())
    response = client.post(
        "/wow/runs", json={**_RUN_PAYLOAD, "can_execute": True},
        headers={"Idempotency-Key": "should-not-matter"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EXECUTION_PROHIBITED"


def test_create_run_requires_idempotency_key_header(monkeypatch):
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: FakeSupabaseClient())
    response = client.post("/wow/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_create_run_happy_path_returns_202_and_pollable_run_id(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setattr("ledger.get_client", lambda: fake)
    response = client.post("/wow/runs", json=_RUN_PAYLOAD, headers={"Idempotency-Key": "run-happy-1"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["terminal"] is True
    assert body["reused"] is False
    assert body["can_execute"] is False
    assert body["poll_url"] == f"/wow/runs/{body['run_id']}/manifest"


def test_repeated_create_run_same_key_and_body_is_idempotent(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setattr("ledger.get_client", lambda: fake)
    first = client.post("/wow/runs", json=_RUN_PAYLOAD, headers={"Idempotency-Key": "run-dup-1"})
    second = client.post("/wow/runs", json=_RUN_PAYLOAD, headers={"Idempotency-Key": "run-dup-1"})
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["reused"] is True
    assert len(fake._store["wow_agent_runs"]) == 1


def test_get_run_not_found_is_404(monkeypatch):
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: FakeSupabaseClient())
    response = client.get("/wow/runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_nonterminal_manifest_is_never_reported_as_zero_picks_means_no_play(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    created = _created_run(fake, "run-nonterminal-1")

    manifest = client.get(f"/wow/runs/{created['run_id']}/manifest")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["terminal"] is False
    assert body["status"] == "CREATED"
    assert "reconciliation" not in body


def test_cancel_run_transitions_to_canceled(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    created = _created_run(fake, "run-cancel-1")

    cancel = client.post(f"/wow/runs/{created['run_id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json() == {"run_id": created["run_id"], "status": "CANCELED", "terminal": True, "can_execute": False}

    manifest = client.get(f"/wow/runs/{created['run_id']}/manifest")
    assert manifest.json()["status"] == "CANCELED"
    assert manifest.json()["terminal"] is True


def test_cancel_already_terminal_run_is_a_no_op(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    created = _created_run(fake, "run-cancel-2")
    client.post(f"/wow/runs/{created['run_id']}/cancel")

    second_cancel = client.post(f"/wow/runs/{created['run_id']}/cancel")
    assert second_cancel.status_code == 200
    assert second_cancel.json()["status"] == "CANCELED"


_RUN_WALK = [
    ("CREATED", "VALIDATING_REQUEST", "VALIDATE"),
    ("VALIDATING_REQUEST", "DISCOVERY_QUEUED", "DISCOVERY"),
    ("DISCOVERY_QUEUED", "DISCOVERY_RUNNING", "DISCOVERY"),
    ("DISCOVERY_RUNNING", "ROUTING", "ROUTING"),
    ("ROUTING", "RESEARCH_QUEUED", "RESEARCH"),
    ("RESEARCH_QUEUED", "RESEARCH_RUNNING", "RESEARCH"),
    ("RESEARCH_RUNNING", "EVIDENCE_QUEUED", "EVIDENCE"),
    ("EVIDENCE_QUEUED", "EVIDENCE_RUNNING", "EVIDENCE"),
    ("EVIDENCE_RUNNING", "MODELING_QUEUED", "MODELING"),
    ("MODELING_QUEUED", "MODELING_RUNNING", "MODELING"),
    ("MODELING_RUNNING", "AUDIT_QUEUED", "AUDIT"),
    ("AUDIT_QUEUED", "AUDIT_RUNNING", "AUDIT"),
    ("AUDIT_RUNNING", "FINAL_REFRESH", "FINAL_REFRESH"),
    ("FINAL_REFRESH", "RECONCILING", "RECONCILING"),
]


def test_synchronous_fake_worker_fixture_completes_with_balanced_reconciliation(monkeypatch):
    """Packet section 22, Phase 1 exit criterion, verbatim: a synchronous
    fake-worker fixture completes with balanced reconciliation.

    Three candidates, one required job each, run through a fake in-process
    worker (claim -> execute -> record output -> finish), reduced through
    the real deterministic reducer, reconciled through the real
    rows_in = completed + held + rejected invariant, and read back through
    the real GET /wow/runs/{id}/manifest endpoint. The run is built directly
    through the repository (see module docstring) so this test keeps manual
    control instead of racing against real eager-mode orchestration.
    """
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)

    created = _created_run(fake, "run-fixture-1")
    run_id = created["run_id"]

    scenarios = [
        ("MLB:G1:PITCHER_KS", "wow.mlb-strikeout-expert", "FINAL_APPROVED", "SUCCEEDED"),
        ("MLB:G2:PITCHER_KS", "wow.mlb-strikeout-expert", "MODEL_UNAVAILABLE", "BLOCKED"),
        ("MLB:G3:PITCHER_KS", "wow.mlb-strikeout-expert", "MODEL_QUALIFIED_HOLD", "SUCCEEDED"),
    ]
    decisions = []
    for canonical_key, worker_id, ceiling, job_terminal_status in scenarios:
        candidate = repository.insert_candidate(
            fake, run_id=run_id, canonical_key=canonical_key, sport="MLB",
            participant="Test Pitcher", market_family="PLAYER_PROPS", period="FULL_GAME",
        )
        job, _ = repository.enqueue_job(
            fake, run_id=run_id, candidate_id=candidate["candidate_id"],
            worker_id=worker_id, worker_version="1.0.0",
            idempotency_key=f"job-{canonical_key}", required=True,
            input_hash=idempotency.input_hash({"canonical_key": canonical_key}),
        )

        claim = repository.try_transition_job(fake, job["job_id"], expected_status="QUEUED", next_status="RUNNING")
        assert claim.applied is True
        repository.record_job_output(
            fake, job_id=job["job_id"], run_id=run_id, candidate_id=candidate["candidate_id"],
            worker_id=worker_id, worker_version="1.0.0", contract_version="wow.agent-output.v1",
            output={"ceiling": ceiling}, output_hash=idempotency.input_hash({"ceiling": ceiling}),
        )
        finish = repository.try_transition_job(
            fake, job["job_id"], expected_status="RUNNING", next_status=job_terminal_status, ceiling=ceiling,
        )
        assert finish.applied is True
        duplicate_finish = repository.try_transition_job(
            fake, job["job_id"], expected_status="RUNNING", next_status=job_terminal_status, ceiling=ceiling,
        )
        assert duplicate_finish.applied is False

        decision = reduce_candidate(
            controlling_worker_id=worker_id,
            controlling_job_status=job_terminal_status,
            required_jobs=[RequiredJobResult(worker_id=worker_id, status=job_terminal_status, ceiling=ceiling)],
        )
        repository.set_candidate_terminal(
            fake, candidate["candidate_id"], terminal_label=decision.label,
            terminal_ceiling=decision.ceiling, blockers=list(decision.blockers),
            controlling_worker_id=worker_id,
        )
        decisions.append(decision)

    reconciliation = reconcile_from_ceilings(
        rows_in=len(decisions), terminal_ceilings=[d.ceiling for d in decisions],
    )
    assert reconciliation.status == "BALANCED"
    assert reconciliation.rows_completed == 1
    assert reconciliation.rows_held == 1
    assert reconciliation.rows_rejected == 1

    repository.set_run_row_counts(
        fake, run_id, rows_in=reconciliation.rows_in, rows_completed=reconciliation.rows_completed,
        rows_held=reconciliation.rows_held, rows_rejected=reconciliation.rows_rejected,
        reconciliation_status=reconciliation.status,
    )

    for current, next_state, stage in _RUN_WALK:
        result = repository.transition_run(fake, run_id, expected_status=current, next_status=next_state, stage=stage)
        assert result.applied is True
    final = repository.transition_run(
        fake, run_id, expected_status="RECONCILING", next_status="COMPLETED_WITH_BLOCKERS", stage="DONE",
    )
    assert final.applied is True

    manifest = client.get(f"/wow/runs/{run_id}/manifest")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["terminal"] is True
    assert body["status"] == "COMPLETED_WITH_BLOCKERS"
    assert body["reconciliation"] == {
        "rows_in": 3, "rows_completed": 1, "rows_held": 1, "rows_rejected": 1, "balanced": True,
    }
    assert len(body["candidates"]) == 3
    assert body["can_execute"] is False
