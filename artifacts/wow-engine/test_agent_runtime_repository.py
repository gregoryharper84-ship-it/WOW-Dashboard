from datetime import datetime, timedelta, timezone

from agent_runtime import repository
from agent_runtime.registry import WORKERS
from agent_runtime.state_machine import IllegalTransitionError
from agent_runtime_test_support import FakeSupabaseClient

import pytest


def _client():
    return FakeSupabaseClient()


def test_create_run_inserts_new_row():
    client = _client()
    row, reused = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    assert reused is False
    assert row["status"] == "CREATED"
    assert row["stage"] == "INTAKE"
    assert row["can_execute"] is False
    assert row["rows_in"] == 0


def test_create_run_same_key_and_hash_is_idempotent():
    client = _client()
    row1, reused1 = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    row2, reused2 = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    assert reused1 is False
    assert reused2 is True
    assert row1["run_id"] == row2["run_id"]
    assert len(client._store["wow_agent_runs"]) == 1


def test_create_run_different_hash_same_key_creates_separate_run():
    client = _client()
    row1, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    row2, reused2 = repository.create_run(
        client, idempotency_key="k1", request_hash="h2", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    assert reused2 is False
    assert row1["run_id"] != row2["run_id"]


def test_create_run_recovers_from_losing_insert_race(monkeypatch):
    client = _client()
    calls = {"n": 0}
    winning_row = {"run_id": "winner-run-id", "idempotency_key": "k1", "request_hash": "h1", "status": "CREATED"}

    def _fake_find_existing(_client, *, idempotency_key, request_hash):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # nothing exists yet, proceed to insert
        return winning_row  # a concurrent writer beat us to it

    monkeypatch.setattr(repository, "find_existing_run", _fake_find_existing)
    # client.table(...) returns a fresh _FakeTable instance on every call, so
    # the insert-failure has to be patched on the class, not one instance.
    import agent_runtime_test_support as support
    monkeypatch.setattr(
        support._FakeTable, "insert",
        lambda self, payload: (_ for _ in ()).throw(RuntimeError("simulated unique-constraint violation")),
    )

    row, reused = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    assert reused is True
    assert row == winning_row
    assert calls["n"] == 2


def test_transition_run_cas_applies_on_matching_status():
    client = _client()
    row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    result = repository.transition_run(client, row["run_id"], expected_status="CREATED", next_status="VALIDATING_REQUEST", stage="VALIDATE")
    assert result.applied is True
    assert result.row["status"] == "VALIDATING_REQUEST"


def test_transition_run_cas_no_op_on_stale_expected_status():
    client = _client()
    row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    repository.transition_run(client, row["run_id"], expected_status="CREATED", next_status="VALIDATING_REQUEST", stage="VALIDATE")
    # Someone else already moved it; a second caller still expecting CREATED must not apply.
    stale_result = repository.transition_run(client, row["run_id"], expected_status="CREATED", next_status="FAILED", stage="FAILED")
    assert stale_result.applied is False
    current = repository.get_run(client, row["run_id"])
    assert current["status"] == "VALIDATING_REQUEST"


def test_transition_run_rejects_illegal_transition_before_touching_db():
    client = _client()
    row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    with pytest.raises(IllegalTransitionError):
        repository.transition_run(client, row["run_id"], expected_status="CREATED", next_status="COMPLETED", stage="DONE")
    # Unchanged — the illegal transition must not have mutated the row.
    assert repository.get_run(client, row["run_id"])["status"] == "CREATED"


def test_try_transition_job_cas_prevents_double_advance():
    client = _client()
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    job, created = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.discovery",
        worker_version="1.0.0", idempotency_key="job-1", required=True, input_hash="h",
    )
    assert created is True
    # Two "workers" race to claim the same QUEUED job.
    first = repository.try_transition_job(client, job["job_id"], expected_status="QUEUED", next_status="RUNNING")
    second = repository.try_transition_job(client, job["job_id"], expected_status="QUEUED", next_status="RUNNING")
    assert first.applied is True
    assert second.applied is False


def test_insert_candidate_and_list_run_candidates():
    client = _client()
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    repository.insert_candidate(
        client, run_id=run_row["run_id"], canonical_key="MLB:GAME1:PITCHER_KS",
        sport="MLB", participant="Test Pitcher", market_family="PLAYER_PROPS", period="FULL_GAME",
    )
    candidates = repository.list_run_candidates(client, run_row["run_id"])
    assert len(candidates) == 1
    assert candidates[0]["canonical_key"] == "MLB:GAME1:PITCHER_KS"
    assert candidates[0]["can_execute"] is False


def test_record_job_output_and_audit_event():
    client = _client()
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    job, _ = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.discovery",
        worker_version="1.0.0", idempotency_key="job-1", required=True, input_hash="h",
    )
    output = repository.record_job_output(
        client, job_id=job["job_id"], run_id=run_row["run_id"], candidate_id=None,
        worker_id="wow.discovery", worker_version="1.0.0", contract_version="wow.agent-output.v1",
        output={"candidates_found": 3}, output_hash="outhash",
    )
    assert output["output"] == {"candidates_found": 3}

    repository.record_audit_event(client, event_type="JOB_SUCCEEDED", actor="wow.discovery", run_id=run_row["run_id"], job_id=job["job_id"])
    events = client.table("wow_agent_audit_events").select("*").eq("run_id", run_row["run_id"]).execute().data
    assert len(events) == 1
    assert events[0]["event_type"] == "JOB_SUCCEEDED"
    assert events[0]["can_execute"] is False


def _running_job(client):
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    job, _ = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.parallel-discovery-router",
        worker_version="1.0.0", idempotency_key="job-1", required=True, input_hash="h",
    )
    repository.try_transition_job(client, job["job_id"], expected_status="QUEUED", next_status="RUNNING")
    return run_row, job


def test_complete_job_atomically_inserts_output_and_transitions_status():
    client = _client()
    run_row, job = _running_job(client)

    applied = repository.complete_job(
        client, job_id=job["job_id"], run_id=run_row["run_id"], candidate_id=None,
        worker_id="wow.parallel-discovery-router", worker_version="1.0.0",
        contract_version="wow.agent-output.v1", evidence_snapshot_id=None,
        output={"candidates": []}, output_hash="h1", status="SUCCEEDED",
        ceiling="RESEARCH_INTEREST", blockers=[],
    )
    assert applied is True
    updated = client.table("wow_agent_jobs").select("*").eq("job_id", job["job_id"]).execute().data[0]
    assert updated["status"] == "SUCCEEDED"
    assert updated["ceiling"] == "RESEARCH_INTEREST"
    outputs = client.table("wow_agent_job_outputs").select("*").eq("job_id", job["job_id"]).execute().data
    assert len(outputs) == 1
    assert outputs[0]["output"] == {"candidates": []}


def test_complete_job_duplicate_delivery_is_a_noop_not_an_error():
    client = _client()
    run_row, job = _running_job(client)

    first = repository.complete_job(
        client, job_id=job["job_id"], run_id=run_row["run_id"], candidate_id=None,
        worker_id="wow.parallel-discovery-router", worker_version="1.0.0",
        contract_version="wow.agent-output.v1", evidence_snapshot_id=None,
        output={"candidates": []}, output_hash="h1", status="SUCCEEDED",
        ceiling="RESEARCH_INTEREST", blockers=[],
    )
    second = repository.complete_job(
        client, job_id=job["job_id"], run_id=run_row["run_id"], candidate_id=None,
        worker_id="wow.parallel-discovery-router", worker_version="1.0.0",
        contract_version="wow.agent-output.v1", evidence_snapshot_id=None,
        output={"candidates": ["should not land"]}, output_hash="h2", status="SUCCEEDED",
        ceiling="RESEARCH_INTEREST", blockers=[],
    )
    assert first is True
    assert second is False
    outputs = client.table("wow_agent_job_outputs").select("*").eq("job_id", job["job_id"]).execute().data
    assert len(outputs) == 1
    assert outputs[0]["output"] == {"candidates": []}  # the duplicate never landed


def test_complete_job_unknown_job_raises():
    client = _client()
    with pytest.raises(RuntimeError, match="JOB_NOT_FOUND"):
        repository.complete_job(
            client, job_id="does-not-exist", run_id="r", candidate_id=None,
            worker_id="w", worker_version="1.0.0", contract_version="wow.agent-output.v1",
            evidence_snapshot_id=None, output={}, output_hash="h", status="SUCCEEDED",
            ceiling="RESEARCH_INTEREST", blockers=[],
        )


def test_registry_matches_true_when_db_mirrors_code_registry():
    client = _client()
    for spec in WORKERS.values():
        client.table("wow_agent_worker_registry").insert({
            "worker_id": spec.worker_id, "worker_version": spec.worker_version,
            "contract_version": spec.contract_version, "implementation_type": spec.implementation_type,
            "authority_ceiling": spec.authority_ceiling, "enabled": True,
        }).execute()
    assert repository.registry_matches(client) is True


def test_registry_matches_false_when_db_missing_a_worker():
    client = _client()
    for spec in list(WORKERS.values())[:-1]:  # omit the last worker
        client.table("wow_agent_worker_registry").insert({
            "worker_id": spec.worker_id, "worker_version": spec.worker_version,
            "contract_version": spec.contract_version, "implementation_type": spec.implementation_type,
            "authority_ceiling": spec.authority_ceiling, "enabled": True,
        }).execute()
    assert repository.registry_matches(client) is False


def test_registry_matches_false_when_empty_registry_table():
    client = _client()
    assert repository.registry_matches(client) is False


def test_set_candidate_terminal_is_compare_and_set_first_decision_wins():
    client = _client()
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    candidate = repository.insert_candidate(
        client, run_id=run_row["run_id"], canonical_key="MLB:G1:PITCHER_KS",
        sport="MLB", participant="Test Pitcher", market_family="PLAYER_PROPS", period="FULL_GAME",
    )
    first = repository.set_candidate_terminal(
        client, candidate["candidate_id"], terminal_label="FINAL_APPROVED",
        terminal_ceiling="FINAL_APPROVED", blockers=[],
    )
    # A duplicate/racing completion tries to downgrade the same candidate —
    # must not win.
    second = repository.set_candidate_terminal(
        client, candidate["candidate_id"], terminal_label="MODEL_UNAVAILABLE",
        terminal_ceiling="MODEL_UNAVAILABLE", blockers=["SHOULD_NOT_APPLY"],
    )
    assert first is True
    assert second is False
    current = repository.get_candidate(client, candidate["candidate_id"])
    assert current["terminal_label"] == "FINAL_APPROVED"
    assert current["blockers"] == []


def test_enqueue_job_same_idempotency_key_returns_existing_job():
    client = _client()
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    first, created1 = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.parallel-discovery-router",
        worker_version="1.0.0", idempotency_key="same-key", required=True, input_hash="h",
    )
    second, created2 = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.parallel-discovery-router",
        worker_version="1.0.0", idempotency_key="same-key", required=True, input_hash="h",
    )
    assert created1 is True
    assert created2 is False
    assert first["job_id"] == second["job_id"]
    assert len(client._store["wow_agent_jobs"]) == 1


def _running_job_with_heartbeat_age(client, *, heartbeat_age_seconds: float) -> dict:
    run_row, _ = repository.create_run(
        client, idempotency_key="k1", request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    job, _ = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.parallel-discovery-router",
        worker_version="1.0.0", idempotency_key="k", required=True, input_hash="h",
    )
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)).isoformat()
    stored = next(row for row in client._store["wow_agent_jobs"] if row["job_id"] == job["job_id"])
    stored["status"] = "RUNNING"
    stored["heartbeat_at"] = stale_heartbeat
    return stored


def test_claim_job_reclaims_a_running_job_whose_lease_expired():
    client = _client()
    job = _running_job_with_heartbeat_age(client, heartbeat_age_seconds=181)  # older than the default 180s lease
    claimed = repository.claim_job(client, job["job_id"])
    assert claimed is True
    refreshed = repository.get_job(client, job["job_id"])
    assert refreshed["status"] == "RUNNING"
    assert refreshed["attempt"] == 1


def test_claim_job_refuses_a_running_job_whose_lease_has_not_expired():
    client = _client()
    job = _running_job_with_heartbeat_age(client, heartbeat_age_seconds=5)  # well inside the default 180s lease
    claimed = repository.claim_job(client, job["job_id"])
    assert claimed is False
