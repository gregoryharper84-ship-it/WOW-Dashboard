from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_runtime import repository
from agent_runtime.coordinator_scout_research import Coordinator
from agent_runtime.registry import WORKERS
from agent_runtime.runner_scout_research import execute_envelope
from agent_runtime.schemas import WorkerJobEnvelope
from agent_runtime.scout_research import (
    RESEARCH_RECONCILER,
    RESEARCH_WORKERS,
    scout_lane,
    validate_non_predictive_output,
)
from agent_runtime.state_machine import check_run_transition


AS_OF = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _payload(market_family: str) -> dict:
    prop = market_family == "PLAYER_PROP"
    row = {
        "canonical_key": f"TEST:{market_family}:1",
        "sport": "WNBA" if prop else "MLB",
        "league": "WNBA" if prop else "MLB",
        "official_event_id": "event-1",
        "participant": "Test Player" if prop else "New York Yankees",
        "opponent": "Test Opponent",
        "market_family": market_family,
        "period": "FULL_GAME",
        "event_start_utc": "2026-09-01T19:00:00+00:00",
        "evidence": {
            "candidate_identity": {"official_event_id": "event-1"},
            "official_event": {"status": "SCHEDULED"},
            "exact_market_identity": {"market_family": market_family},
            "game_log": [1, 2, 3],
            "box_score_log": [{"minutes": 30}],
            "role_status": "CONFIRMED",
            "role_timestamp": "2026-09-01T12:00:00+00:00",
            "source_attempts": [{"source": "TEST_FIXTURE"}],
        },
    }
    if prop:
        row.update({"stat_family": "REBOUNDS", "exact_line": 7.5, "side": "MORE"})
    return row


def _env(worker_id: str, payload: dict | None = None) -> WorkerJobEnvelope:
    spec = WORKERS[worker_id]
    return WorkerJobEnvelope(
        run_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        candidate_id="33333333-3333-3333-3333-333333333333",
        worker_id=worker_id,
        worker_version=spec.worker_version,
        required=True,
        as_of=AS_OF.isoformat(),
        input_hash="test-input-hash",
        payload=payload or {},
        can_execute=False,
    )


def test_state_machine_requires_research_before_evidence():
    assert check_run_transition("ROUTING", "RESEARCH_QUEUED").allowed is True
    assert check_run_transition("ROUTING", "EVIDENCE_QUEUED").allowed is False
    assert check_run_transition("RESEARCH_RUNNING", "EVIDENCE_QUEUED").allowed is True


def test_only_controlling_model_has_fitted_model_authority():
    fitted = [worker_id for worker_id, spec in WORKERS.items() if spec.implementation_type == "FITTED_MODEL"]
    assert fitted == ["wow.controlling-model"]
    for worker_id, spec in WORKERS.items():
        if "scout" in worker_id or "research" in worker_id:
            assert spec.authority_ceiling == "RESEARCH_INTEREST"


@pytest.mark.parametrize(
    ("market_family", "expected_lane"),
    [("PLAYER_PROP", "PROP"), ("OUTRIGHT_WINNER", "ML_EVENT")],
)
def test_scout_lane_separates_prop_and_ml(market_family: str, expected_lane: str):
    assert scout_lane(_payload(market_family)) == expected_lane


def test_scout_research_authority_leakage_is_detected():
    violations = validate_non_predictive_output({
        "nested": {"calibrated_probability": 0.71},
        "stake": 10,
        "can_execute": True,
    })
    assert "nested.calibrated_probability" in violations
    assert "stake" in violations
    assert "can_execute" in violations


def test_global_scout_is_non_predictive():
    out = execute_envelope(_env("wow.global-scout-coordinator", {"candidate": _payload("PLAYER_PROP")}))
    assert out.status == "SUCCEEDED"
    assert out.ceiling == "RESEARCH_INTEREST"
    assert out.output["prediction_authority"] is False
    assert out.output["can_execute"] is False
    assert validate_non_predictive_output(out.output) == []


def test_reconciler_fails_closed_without_evidence():
    reports = [{"research_status": "PARTIAL"} for _ in RESEARCH_WORKERS]
    out = execute_envelope(_env(RESEARCH_RECONCILER, {
        "research_reports": reports,
        "evidence_present": False,
        "event_start_present": True,
    }))
    assert out.status == "BLOCKED"
    assert "EVIDENCE_SNAPSHOT_MISSING" in out.blockers
    assert out.output["prediction_authority"] is False


@pytest.mark.parametrize(
    ("market_family", "expected_scout"),
    [("PLAYER_PROP", "wow.prop-scout-router"), ("OUTRIGHT_WINNER", "wow.ml-event-scout-router")],
)
def test_coordinator_routes_both_lanes_through_scout_then_research_then_evidence(
    monkeypatch, market_family: str, expected_scout: str,
):
    payload = _payload(market_family)
    candidate = {
        "candidate_id": "33333333-3333-3333-3333-333333333333",
        "run_id": "11111111-1111-1111-1111-111111111111",
        "canonical_key": payload["canonical_key"],
        "sport": payload["sport"],
        "market_family": payload["market_family"],
        "period": payload["period"],
        "stat_family": payload.get("stat_family"),
        "candidate_payload": payload,
        "terminal_label": None,
    }
    coord = Coordinator(client=object())
    queued: list[dict] = []
    transitions: list[tuple[str, str]] = []
    coord._queue = lambda **kwargs: queued.append(kwargs)
    coord._run_as_of = lambda run_id: AS_OF
    coord._finish_if_terminal = lambda run_id: None
    coord._terminal = lambda *args, **kwargs: pytest.fail(f"unexpected terminalization: {args} {kwargs}")

    def transition(_client, _run_id, *, expected_status, next_status, stage):
        transitions.append((expected_status, next_status))
        return SimpleNamespace(applied=True, row={"status": next_status})

    monkeypatch.setattr(repository, "transition_run", transition)
    monkeypatch.setattr(repository, "upsert_candidates", lambda _client, _run_id, rows: [candidate])
    monkeypatch.setattr(repository, "list_run_candidates", lambda _client, _run_id: [candidate])
    monkeypatch.setattr(repository, "get_candidate", lambda _client, _cid: candidate)

    # 1. Discovery must queue Global Scout, not identity/evidence/model directly.
    coord._after_discovery(
        SimpleNamespace(run_id=candidate["run_id"], payload={"rows": [payload], "discovery_enabled": False}),
        {"status": "SUCCEEDED", "output": {"candidates": [payload]}},
    )
    assert [item["worker_id"] for item in queued] == ["wow.global-scout-coordinator"]

    # 2. Successful Global Scout must route to the lane-specific Scout.
    queued.clear()
    coord._all_worker_terminal = lambda *_args, **_kwargs: True
    monkeypatch.setattr(repository, "list_jobs", lambda _client, _run_id, worker_id=None: (
        [{"candidate_id": candidate["candidate_id"], "worker_id": "wow.global-scout-coordinator", "status": "SUCCEEDED"}]
        if worker_id == "wow.global-scout-coordinator" else []
    ))
    monkeypatch.setattr(coord, "_maybe_queue_identity", lambda _run_id: None)
    coord._after_global_scout(SimpleNamespace(run_id=candidate["run_id"]), {"status": "SUCCEEDED"})
    assert [item["worker_id"] for item in queued] == [expected_scout]

    # 3. Identity success must queue all five researchers and must not queue
    # evidence hydration yet.
    queued.clear()
    monkeypatch.setattr(repository, "list_jobs", lambda _client, _run_id, worker_id=None: (
        [{"candidate_id": candidate["candidate_id"], "worker_id": "wow.slate-integrity-expert", "status": "SUCCEEDED"}]
        if worker_id == "wow.slate-integrity-expert" else []
    ))
    coord._after_identity(SimpleNamespace(run_id=candidate["run_id"]), {"status": "SUCCEEDED"})
    assert {item["worker_id"] for item in queued} == set(RESEARCH_WORKERS)
    assert "wow.evidence-hydration" not in {item["worker_id"] for item in queued}
    assert ("ROUTING", "RESEARCH_QUEUED") in transitions

    # 4. Only after the reconciler succeeds can evidence hydration be queued.
    queued.clear()
    monkeypatch.setattr(repository, "list_jobs", lambda _client, _run_id, worker_id=None: (
        [{"candidate_id": candidate["candidate_id"], "worker_id": RESEARCH_RECONCILER, "status": "SUCCEEDED"}]
        if worker_id == RESEARCH_RECONCILER else []
    ))
    coord._after_reconciler(SimpleNamespace(run_id=candidate["run_id"]), {"status": "SUCCEEDED"})
    assert [item["worker_id"] for item in queued] == ["wow.evidence-hydration"]
    assert ("RESEARCH_RUNNING", "EVIDENCE_QUEUED") in transitions
