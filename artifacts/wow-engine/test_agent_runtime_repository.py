from agent_runtime import repository
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
    job = repository.enqueue_job(
        client, run_id=run_row["run_id"], candidate_id=None, worker_id="wow.discovery",
        worker_version="1.0.0", idempotency_key="job-1", required=True, input_hash="h",
    )
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
    job = repository.enqueue_job(
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
