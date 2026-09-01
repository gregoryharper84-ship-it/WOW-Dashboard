from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.registry import WORKERS
from agent_runtime.runner import _run_controlling_model
from agent_runtime.schemas import WorkerJobEnvelope
from nfl_event_model_contract import (
    CAPABILITY_KEY,
    CONTROLLING_SPECIALIST,
    FEATURE_SCHEMA_VERSION,
    PROVIDER_IDENTITY,
    REQUIRED_FEATURE_GROUPS,
    p0_readiness,
    validate_candidate_identity,
    validate_feature_packet,
)


def _model_env(payload: dict) -> WorkerJobEnvelope:
    spec = WORKERS["wow.controlling-model"]
    return WorkerJobEnvelope(
        run_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        candidate_id="33333333-3333-3333-3333-333333333333",
        worker_id="wow.controlling-model",
        worker_version=spec.worker_version,
        required=True,
        as_of=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).isoformat(),
        input_hash="nfl-p0-test",
        payload=payload,
        can_execute=False,
    )


def test_nfl_p0_identity_is_specific_and_non_executable():
    assert PROVIDER_IDENTITY == "WOW_NFL_EVENT_FITTED_MODEL_V1"
    assert CONTROLLING_SPECIALIST == "wow.nfl-game-win-probability-expert"
    assert CAPABILITY_KEY == "NFL_EVENT_PROBABILITY"
    assert FEATURE_SCHEMA_VERSION == "NFL_EVENT_FEATURES_V1"


def test_nfl_candidate_identity_contract_accepts_only_full_game_moneyline():
    good = validate_candidate_identity({
        "sport": "NFL",
        "official_event_id": "nfl-2026-week1-game1",
        "participant": "Team A",
        "opponent": "Team B",
        "market_family": "OUTRIGHT_WINNER",
        "period": "FULL_GAME_INCLUDING_OVERTIME",
    })
    assert good.ok is True
    assert good.blockers == ()

    bad = validate_candidate_identity({
        "sport": "NFL", "official_event_id": "x", "participant": "Team A",
        "market_family": "PLAYER_PROP", "period": "FIRST_HALF",
    })
    assert bad.ok is False
    assert "NFL_EVENT_MARKET_UNSUPPORTED" in bad.blockers
    assert "NFL_EVENT_PERIOD_UNSUPPORTED" in bad.blockers
    assert "NFL_EVENT_OPPONENT_MISSING" in bad.blockers


def test_nfl_feature_packet_fails_closed_without_every_feature_group():
    empty = validate_feature_packet({})
    assert empty.ok is False
    assert len(empty.blockers) == len(REQUIRED_FEATURE_GROUPS)

    complete = validate_feature_packet({group: {"source": "test"} for group in REQUIRED_FEATURE_GROUPS})
    assert complete.ok is True
    assert complete.blockers == ()


def test_p0_readiness_cannot_claim_model_available_without_artifact_calibrator_and_capability():
    state = p0_readiness(
        artifact_ready=False,
        calibrator_ready=False,
        capability_status="UNAVAILABLE",
    )
    assert state["model_status"] == "MODEL_UNAVAILABLE"
    assert state["probability_publishable"] is False
    assert state["can_execute"] is False
    assert {
        "NFL_FITTED_MODEL_ARTIFACT_UNAVAILABLE",
        "NFL_EVENT_CALIBRATOR_UNAVAILABLE",
        "NFL_EVENT_PROBABILITY_CAPABILITY_UNAVAILABLE",
    }.issubset(set(state["blockers"]))


def test_existing_controlling_model_runner_does_not_fallback_for_nfl():
    out = _run_controlling_model(_model_env({
        "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
        "period": "FULL_GAME_INCLUDING_OVERTIME",
        "capability": {
            "status": "UNAVAILABLE",
            "provider_identity": PROVIDER_IDENTITY,
        },
    }))
    assert out.status == "BLOCKED"
    assert out.ceiling == "RESEARCH_INTEREST"
    assert "MODEL_UNAVAILABLE" in out.blockers
    assert out.output["probability_publishable"] is False
    assert out.can_execute is False


def test_p0_migration_seeds_no_fake_artifact_and_registers_unavailable_capability():
    sql = (Path(__file__).parent / "migrations" / "20260901_nfl_event_model_p0.sql").read_text()
    normalized = " ".join(sql.split())
    assert "WOW_NFL_EVENT_FITTED_MODEL_V1" in sql
    assert "wow.nfl-game-win-probability-expert" in sql
    assert "NFL_EVENT_PROBABILITY" in sql
    assert "'UNAVAILABLE'" in sql
    assert "terminal_label_if_scored_now', 'MODEL_UNAVAILABLE'" in normalized
    # P0 creates the registry but never inserts a model artifact row.
    assert "insert into public.wow_nfl_event_fitted_model_artifacts" not in sql.lower()
