"""Per-worker handler tests (packet sections 6-14), adapted from PR #33's
test_agent_runtime_handlers.py during the convergence pass to this module's
WorkerJobEnvelope/execute_envelope.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.runner import execute_envelope
from agent_runtime.schemas import WorkerJobEnvelope


def _env(worker_id: str, payload: dict, *, candidate_id: str | None = "c1", evidence_snapshot_id: str | None = None) -> WorkerJobEnvelope:
    return WorkerJobEnvelope(
        run_id="r1", job_id="j1", candidate_id=candidate_id, worker_id=worker_id, worker_version="1.0.0",
        evidence_snapshot_id=evidence_snapshot_id, as_of=datetime.now(timezone.utc).isoformat(),
        input_hash="h", payload=payload,
    )


def test_discovery_worker_dedupes_source_families():
    rows = [
        {
            "sport": "MLB", "official_event_id": "e1", "participant": "Pitcher A", "market_family": "PLAYER_PROP",
            "sources": [{"source_family": "espn"}, {"source_family": "espn"}, {"source_family": "mlb.com"}],
        },
        {
            "sport": "MLB", "official_event_id": "e1", "participant": "Pitcher A", "market_family": "PLAYER_PROP",
            "sources": [{"source_family": "nbcsports"}],
        },
    ]
    out = execute_envelope(_env("wow.parallel-discovery-router", {"rows": rows, "discovery_enabled": True}, candidate_id=None))
    assert out.status == "SUCCEEDED"
    assert out.output["candidate_count"] == 1
    assert set(out.output["candidates"][0]["source_families"]) == {"espn", "mlb.com", "nbcsports"}


def test_discovery_enabled_but_no_rows_is_blocked_not_a_silent_empty_success():
    out = execute_envelope(_env("wow.parallel-discovery-router", {"rows": [], "discovery_enabled": True}, candidate_id=None))
    assert out.status == "BLOCKED"
    assert "DISCOVERY_PROVIDER_UNAVAILABLE" in out.blockers


def test_identity_worker_requires_exact_prop_identity():
    incomplete = execute_envelope(_env("wow.slate-integrity-expert", {
        "candidate": {"sport": "MLB", "official_event_id": "e1", "participant": "P", "market_family": "PLAYER_PROP", "period": "FULL_GAME"},
    }))
    assert incomplete.status == "BLOCKED"
    assert "SLATE_IDENTITY_INCOMPLETE" in incomplete.blockers

    complete = execute_envelope(_env("wow.slate-integrity-expert", {
        "candidate": {
            "sport": "MLB", "official_event_id": "e1", "participant": "P", "market_family": "PLAYER_PROP",
            "period": "FULL_GAME", "stat_family": "STRIKEOUTS", "exact_line": 5.5, "side": "MORE",
        },
    }))
    assert complete.status == "SUCCEEDED"
    assert complete.ceiling == "IDENTITY_VERIFIED"


def test_evidence_worker_keeps_game_and_box_logs_separate():
    payload = {
        "candidate_identity": {}, "official_event": {}, "exact_market_identity": {},
        "game_log": [4, 5, 6], "box_score_log": [{"ip": 6}], "role_status": "CONFIRMED",
        "role_timestamp": datetime.now(timezone.utc).isoformat(), "source_attempts": [{"source": "x"}],
    }
    out = execute_envelope(_env("wow.evidence-hydration", {"evidence": payload}, evidence_snapshot_id="e1"))
    assert out.status == "SUCCEEDED"
    assert out.ceiling == "EVIDENCE_VERIFIED"
    assert out.output["sealed_evidence"]["game_log"] == [4, 5, 6]
    assert out.output["sealed_evidence"]["box_score_log"] == [{"ip": 6}]


def test_controlling_model_does_not_accept_envelope_probability_substitute():
    # No sport/market_family match the governed MLB event bridge path, and no
    # capability record is supplied — must fail closed, never invent a number.
    out = execute_envelope(_env("wow.controlling-model", {
        "sport": "WNBA", "market_family": "PLAYER_PROP", "period": "FULL_GAME",
        "raw_model_probability": 0.9,  # a hostile caller trying to smuggle a probability in
    }))
    assert out.status == "BLOCKED"
    assert out.output.get("probability_publishable") is False
    assert "raw_model_probability" not in out.output or out.output.get("probability_publishable") is False


def test_failure_path_worker_builds_unconditional_distribution():
    out = execute_envelope(_env("wow.failure-path-framework", {
        "components": [
            {"weight": 0.6, "pmf": {0: 0.5, 1: 0.5}},
            {"weight": 0.4, "pmf": {0: 0.2, 1: 0.8}},
        ],
    }))
    assert out.status == "SUCCEEDED"
    assert out.output["failure_path_applied"] is True
    pmf = {int(k): v for k, v in out.output["unconditional_pmf"].items()}
    assert abs(sum(pmf.values()) - 1.0) < 1e-9


def test_market_worker_preserves_model_market_objective_separation():
    held = execute_envelope(_env("wow.exact-line-market-auditor", {"exact_identity_match": True}))
    assert held.status == "BLOCKED"
    assert held.output["blocks_model_probability"] is False  # a market hold never erases the model's own probability


def test_final_refresh_rejects_started_event():
    now = datetime.now(timezone.utc)
    out = execute_envelope(_env("wow.final-refresh-governor", {
        "now": now.isoformat(), "event_start": (now - timedelta(minutes=5)).isoformat(),
        "event_status": "STARTED", "market_fresh": True, "critical_status_fresh": True,
    }))
    assert out.status == "REJECTED"
    assert "EVENT_NOT_PREGAME" in out.blockers


def test_terminal_reducer_cannot_upgrade_model_blocker():
    out = execute_envelope(_env("wow.terminal-ceiling-reducer", {
        "controlling_worker_id": "wow.controlling-model",
        "required_jobs": [
            {"worker_id": "wow.controlling-model", "status": "SUCCEEDED", "ceiling": "MODEL_QUALIFIED_HOLD", "blockers": []},
            {"worker_id": "wow.exact-line-market-auditor", "status": "SUCCEEDED", "ceiling": "MARKET_VERIFIED_HOLD", "blockers": []},
        ],
    }))
    assert out.status == "SUCCEEDED"
    assert out.output["final_terminal_ceiling"] == "MODEL_QUALIFIED_HOLD"


def test_worker_version_mismatch_is_blocked():
    env = WorkerJobEnvelope(
        run_id="r1", job_id="j1", candidate_id=None, worker_id="wow.parallel-discovery-router",
        worker_version="999.0.0", as_of=datetime.now(timezone.utc).isoformat(), input_hash="h", payload={"rows": []},
    )
    mismatched = execute_envelope(env)
    assert mismatched.status == "BLOCKED"
    assert "WORKER_VERSION_MISMATCH" in mismatched.blockers
