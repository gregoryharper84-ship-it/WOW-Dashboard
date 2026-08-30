from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI

from live_probability_runtime import (
    LiveScoreRequest,
    _apply_calibrator,
    _apply_live_bounds,
    _request_blockers,
    _score_mlb,
    _server_role_blockers,
    _snapshot_binding_blockers,
    _state_hash,
    install_live_probability_routes,
)


def request(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        research_run_id="r1",
        official_event_id="123",
        sport="MLB",
        league="MLB",
        exact_selection="Away",
        event_status="IN_PROGRESS",
        settlement_rule="FULL_GAME_OUTRIGHT",
        source_snapshot_id=UUID("00000000-0000-0000-0000-000000000001"),
        live_snapshot_timestamp=now,
        latest_material_update_at=now,
        market_role="UNDERDOG",
        market_role_source="governed-market-snapshot",
        market_role_timestamp=now,
        market_role_confidence=0.90,
    )
    values.update(overrides)
    return LiveScoreRequest(**values)


def state(req=None):
    req = req or request()
    return {
        "home_team": "Home",
        "away_team": "Away",
        "home_score": 2,
        "away_score": 1,
        "inning": 6,
        "half": "TOP",
        "outs": 1,
        "home_remaining_runs_mean": 1.1,
        "away_remaining_runs_mean": 1.3,
        "home_extra_inning_win_probability": 0.54,
        "market_role": req.market_role,
        "market_role_source": req.market_role_source,
        "market_role_timestamp": req.market_role_timestamp.isoformat(),
        "market_role_confidence": req.market_role_confidence,
        "feature_provenance": {
            "home_remaining_runs_mean": "certified_live_feature_pipeline",
            "away_remaining_runs_mean": "certified_live_feature_pipeline",
            "home_extra_inning_win_probability": "certified_live_feature_pipeline",
        },
    }


def test_scheduled_event_cannot_enter_live_lane():
    assert "LIVE_EVENT_NOT_IN_PROGRESS" in _request_blockers(
        request(event_status="SCHEDULED"), datetime.now(timezone.utc)
    )


def test_settlement_must_be_exact():
    assert "LIVE_SETTLEMENT_RULE_MISMATCH" in _request_blockers(
        request(settlement_rule="MONEYLINE"), datetime.now(timezone.utc)
    )


def test_only_current_live_underdog_is_rank_eligible():
    assert "NOT_CURRENT_LIVE_UNDERDOG" in _request_blockers(
        request(market_role="FAVORITE"), datetime.now(timezone.utc)
    )


def test_stale_state_fails_closed():
    now = datetime.now(timezone.utc)
    req = request(
        live_snapshot_timestamp=now - timedelta(seconds=21),
        latest_material_update_at=now - timedelta(seconds=21),
    )
    assert "LIVE_STATE_STALE" in _request_blockers(req, now)


def test_material_update_after_snapshot_requires_rerun():
    now = datetime.now(timezone.utc)
    req = request(
        live_snapshot_timestamp=now - timedelta(seconds=5),
        latest_material_update_at=now,
    )
    assert "LIVE_SNAPSHOT_PREDATES_MATERIAL_UPDATE" in _request_blockers(req, now)


def test_server_role_must_correspond_to_caller_claim():
    req = request()
    server = state(req)
    server["market_role"] = "FAVORITE"
    blockers = _server_role_blockers(req, server)
    assert "SERVER_LIVE_ROLE_NOT_UNDERDOG" in blockers
    assert "CALLER_SERVER_MARKET_ROLE_MISMATCH" in blockers


def test_mlb_model_is_deterministic_and_strict_probability():
    req = request()
    s = state(req)
    a = _score_mlb(req, s, _state_hash(s))
    b = _score_mlb(req, s, _state_hash(s))
    assert a["blockers"] == []
    assert a["raw_probability"] == b["raw_probability"]
    assert 0 < a["raw_probability"] < 1
    assert abs(a["failure_path_score"] - (1 - a["raw_probability"])) < 1e-12
    assert a["simulation_draws"] >= 50000


def test_state_requires_provenance_for_model_features():
    req = request()
    s = state(req)
    del s["feature_provenance"]
    assert _score_mlb(req, s, _state_hash(s))["blockers"] == ["LIVE_FEATURE_PROVENANCE_MISSING"]


def test_selection_must_match_event():
    req = request(exact_selection="Not A Team")
    s = state(req)
    assert _score_mlb(req, s, _state_hash(s))["blockers"] == ["SELECTION_EVENT_IDENTITY_MISMATCH"]


def test_platt_calibration_uses_safe_scalars_only():
    calibrated = _apply_calibrator(
        {"calibration_method": "PLATT_TIME_SPLIT_V1", "platt_a": 0.1, "platt_b": 0.9},
        0.55,
    )
    assert 0 < calibrated < 1


def test_serialized_isotonic_calibrator_is_rejected():
    with pytest.raises(ValueError, match="LIVE_CALIBRATION_METHOD_UNSUPPORTED_SAFE_RUNTIME"):
        _apply_calibrator(
            {"calibration_method": "ISOTONIC_V1", "isotonic_artifact_b64": "not-loaded"},
            0.50,
        )


def test_live_bounds_are_artifact_driven():
    calibrator = {
        "live_bounds_json": [
            {
                "p_min": 0.40,
                "p_max": 0.60,
                "max_state_age_seconds": 20,
                "lower_delta": 0.04,
                "upper_delta": 0.05,
                "confidence_level": "LIVE_80",
            }
        ]
    }
    lower, upper, confidence = _apply_live_bounds(calibrator, 0.50, 10)
    assert lower == pytest.approx(0.46)
    assert upper == pytest.approx(0.55)
    assert confidence == "LIVE_80"


def test_missing_live_bounds_artifact_blocks():
    with pytest.raises(ValueError, match="LIVE_PREDICTIVE_BOUNDS_ARTIFACT_MISSING"):
        _apply_live_bounds({}, 0.50, 5)


def test_snapshot_feature_binding_must_match_certified_live_artifact():
    snapshot = {
        "feature_model_family": "MLB_LIVE_REMAINING_RUNS_V1",
        "feature_model_artifact_version": "live-v1",
        "feature_schema_version": "MLB_LIVE_STATE_FEATURES_V1",
        "feature_artifact_checksum": "abc",
    }
    artifact = {"model_artifact_version": "live-v1", "artifact_checksum": "abc"}
    gate = {"serving_model_version": "live-v1"}
    assert _snapshot_binding_blockers(snapshot, artifact, gate) == []
    snapshot["feature_artifact_checksum"] = "wrong"
    assert _snapshot_binding_blockers(snapshot, artifact, gate) == [
        "LIVE_FEATURE_ARTIFACT_CHECKSUM_MISMATCH"
    ]


def test_route_install_is_idempotent_and_execution_remains_disabled():
    app = FastAPI()

    class DummyDB:
        pass

    auth = Depends(lambda: True)
    install_live_probability_routes(app, auth_dependency=auth, db_client_fn=DummyDB)
    install_live_probability_routes(app, auth_dependency=auth, db_client_fn=DummyDB)
    routes = [
        (route.path, tuple(sorted(route.methods or [])), getattr(route, "operation_id", None))
        for route in app.router.routes
    ]
    live_health = [r for r in routes if r[0] == "/live-probability/health" and "GET" in r[1]]
    live_score = [r for r in routes if r[0] == "/score-live-event" and "POST" in r[1]]
    assert len(live_health) == 1
    assert len(live_score) == 1
    assert live_health[0][2] == "getWowLiveProbabilityHealth"
    assert live_score[0][2] == "scoreWowLiveEvent"
