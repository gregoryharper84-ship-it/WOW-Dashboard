from datetime import datetime, timezone

from agent_runtime.orchestrator import Orchestrator
from agent_runtime_test_support import FakeSupabaseClient


def test_job_idempotency_is_namespaced_by_worker_and_dedupes_exact_retry(monkeypatch):
    fake = FakeSupabaseClient()
    dispatched: list[str] = []

    from agent_runtime import durable_runner

    monkeypatch.setattr(
        durable_runner.execute_durable,
        "apply_async",
        lambda *args, **kwargs: dispatched.append(str(kwargs.get("task_id"))),
    )

    orchestrator = Orchestrator(fake)
    common = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "candidate_id": "22222222-2222-2222-2222-222222222222",
        "evidence_snapshot_id": None,
        "as_of": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        "payload": {"candidate": {"sport": "MLB", "market_family": "OUTRIGHT_WINNER"}},
        "required": True,
    }

    scout = orchestrator.queue_worker(worker_id="wow.ml-event-scout-router", **common)
    identity = orchestrator.queue_worker(worker_id="wow.slate-integrity-expert", **common)
    scout_retry = orchestrator.queue_worker(worker_id="wow.ml-event-scout-router", **common)

    assert scout["job_id"] != identity["job_id"]
    assert scout["input_hash"] != identity["input_hash"]
    assert scout_retry["job_id"] == scout["job_id"]
    assert scout_retry["input_hash"] == scout["input_hash"]
    assert len(fake._store["wow_agent_jobs"]) == 2
    assert len(dispatched) == 2
