from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from gate_engine.mlb_v2_features import FEATURE_NAMES
from gate_engine import mlb_v2_runtime as runtime


ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "gate_engine" / "mlb_v2_artifacts"


def _mean_feature_vector() -> list[float]:
    schema = json.loads((ARTIFACT_DIR / "mlb_v2_feature_schema.json").read_text())
    support = schema["feature_support"]
    assert [s["name"] for s in support] == FEATURE_NAMES
    return [float(s["mean"]) for s in support]


def _import_app_without_manifest_boot(monkeypatch):
    """Isolate route behavior from the unrelated DB-backed boot readiness gate.

    Production app startup remains fail-closed when the daily manifest database is
    unavailable. These endpoint unit tests stub only that boot prerequisite before
    importing app.py so they can exercise the route contract itself.
    """
    import gate_engine.daily_run_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "ensure_manifest_ready", lambda: None)
    sys.modules.pop("app", None)
    import app as app_module

    return app_module


def test_artifact_health_available_for_2026_and_execution_locked():
    runtime.reset_artifact_cache_for_tests()
    health = runtime.artifact_health(date(2026, 8, 27))
    assert health["healthy"] is True
    assert health["probability_capability"] == "AVAILABLE"
    assert health["model_id"] == "mlb-moneyline-v2-rolling-2026"
    assert health["blockers"] == []
    assert health["can_execute"] is False
    assert runtime.can_execute is False
    assert runtime.can_approve_bets is False


def test_artifact_expires_fail_closed_before_2027_season():
    health = runtime.artifact_health(date(2027, 3, 1))
    assert health["healthy"] is False
    assert health["probability_capability"] == "UNAVAILABLE"
    assert any("EXPIRED" in b or "SEASON_MISMATCH" in b for b in health["blockers"])
    assert health["can_execute"] is False


def test_real_artifact_scores_41_features_and_probabilities_reconcile():
    result = runtime.score_home_probability(_mean_feature_vector(), as_of=date(2026, 8, 27))
    assert result["ok"] is True
    assert result["probability_publishable"] is True
    assert result["model_id"] == "mlb-moneyline-v2-rolling-2026"
    assert result["native_calibrated"] is True
    assert result["point_estimate_locked"] is True
    assert result["market_weight_in_point_probability"] == 0.0
    assert 0.0 < result["home_probability"] < 1.0
    assert 0.0 < result["away_probability"] < 1.0
    assert result["home_probability"] + result["away_probability"] == pytest.approx(1.0, abs=1e-12)
    assert result["home_probability_lower_bound"] <= result["home_probability"] <= result["home_probability_upper_bound"]
    assert result["can_execute"] is False
    assert result["can_approve_bets"] is False


def test_artifact_reload_is_prediction_identical():
    features = _mean_feature_vector()
    first = runtime.score_home_probability(features, as_of=date(2026, 8, 27))
    runtime.reset_artifact_cache_for_tests()
    second = runtime.score_home_probability(features, as_of=date(2026, 8, 27))
    assert first["home_probability"] == second["home_probability"]
    assert first["away_probability"] == second["away_probability"]
    assert first["home_probability_lower_bound"] == second["home_probability_lower_bound"]
    assert first["home_probability_upper_bound"] == second["home_probability_upper_bound"]


def test_bad_feature_count_fails_closed_without_probability():
    result = runtime.score_home_probability([0.0] * 40, as_of=date(2026, 8, 27))
    assert result["ok"] is False
    assert result["probability_publishable"] is False
    assert any("FEATURE_COUNT_MISMATCH" in b for b in result["blockers"])
    assert "home_probability" not in result
    assert result["can_execute"] is False


def test_nonfinite_feature_fails_closed_without_probability():
    values = _mean_feature_vector()
    values[0] = float("nan")
    result = runtime.score_home_probability(values, as_of=date(2026, 8, 27))
    assert result["ok"] is False
    assert result["probability_publishable"] is False
    assert "MLB_V2_FEATURE_VECTOR_NONFINITE" in result["blockers"]
    assert "home_probability" not in result


def test_extreme_out_of_distribution_vector_fails_closed():
    schema = json.loads((ARTIFACT_DIR / "mlb_v2_feature_schema.json").read_text())
    values = _mean_feature_vector()
    # Force more than the hard-OOD publication limit outside training support.
    for i in range(3):
        values[i] = float(schema["feature_support"][i]["max"]) + 1000.0
    result = runtime.score_home_probability(values, as_of=date(2026, 8, 27))
    assert result["ok"] is False
    assert result["probability_publishable"] is False
    assert any("HARD_OOD_FEATURES" in b for b in result["blockers"])


def test_registry_points_mlb_to_v2_and_stays_execution_locked():
    from gate_engine.moneyline_probability import get_model_for_sport

    model = get_model_for_sport("MLB")
    assert model["model_id"] == "mlb-moneyline-v2-rolling-2026"
    assert model["status"] == "ACTIVE"
    assert model["probability_capability"] == "AVAILABLE"
    assert model["can_execute"] is False
    assert model["can_approve_bets"] is False


def test_sport_model_uses_v2_as_sole_mlb_submodel(monkeypatch):
    from gate_engine.moneyline import sport_model

    class Orientation:
        resolved = True
        is_home = True

        def to_dict(self):
            return {"resolved": True, "is_home": True}

    fake = {
        "ok": True,
        "probability_publishable": True,
        "model_id": "mlb-moneyline-v2-rolling-2026",
        "schema_version": "MLB_PREGAME_V2_20260827",
        "raw_pre_platt_home_probability": 0.57,
        "home_probability": 0.55,
        "away_probability": 0.45,
        "home_probability_lower_bound": 0.48,
        "home_probability_upper_bound": 0.62,
        "empirical_interval": {"source": "TEST"},
        "drift": {"status": "PASS"},
    }
    monkeypatch.setattr(runtime, "score_home_probability", lambda *a, **k: fake)
    row = {"sport": "MLB", "game_date": "2026-08-27"}
    enrichment = {"mlb_v2_feature_vector": [0.0] * 41}
    out = sport_model.compute_independent_probability(row, enrichment, orientation=Orientation())
    assert out["independent_probability"] == pytest.approx(0.55)
    assert out["home_probability"] == pytest.approx(0.55)
    assert out["away_probability"] == pytest.approx(0.45)
    assert out["submodels_active"] == ["mlb_v2_rolling"]
    assert out["ensemble_weights_used"] == {"mlb_v2_rolling": 1.0}
    assert out["native_calibrated"] is True
    assert out["point_estimate_locked"] is True
    assert out["market_weight_in_point_probability"] == 0.0
    assert out["can_execute"] is False


def test_score_event_success_contract(monkeypatch):
    monkeypatch.setenv("SCORING_API_KEY", "test-key")
    monkeypatch.setenv("API_KEY", "test-key")

    import gate_engine.mlb_v2_hydrator as hydrator
    import gate_engine.mlb_v2_runtime as rt

    app_module = _import_app_without_manifest_boot(monkeypatch)

    monkeypatch.setattr(rt, "artifact_health", lambda *a, **k: {
        "healthy": True, "probability_capability": "AVAILABLE", "blockers": [], "can_execute": False
    })
    monkeypatch.setattr(hydrator, "hydrate_mlb_v2_enrichment", lambda row, enrichment=None: {
        "mlb_v2_feature_vector": [0.0] * 41,
        "mlb_v2_hydration": {
            "status": "FEATURES_READY",
            "game_date": "2026-08-27",
            "home_team": "NYA",
            "away_team": "HOU",
            "gamePk": 123,
            "strict_prior_date_only": True,
            "same_day_results_used": False,
            "blockers": [],
        },
    })
    monkeypatch.setattr(rt, "score_home_probability", lambda *a, **k: {
        "ok": True,
        "probability_publishable": True,
        "model_id": "mlb-moneyline-v2-rolling-2026",
        "schema_version": "MLB_PREGAME_V2_20260827",
        "home_probability": 0.58,
        "away_probability": 0.42,
        "home_probability_lower_bound": 0.50,
        "home_probability_upper_bound": 0.65,
        "empirical_interval": {"source": "TEST"},
        "drift": {"status": "PASS"},
    })

    client = app_module.app.test_client()
    response = client.post(
        "/wow/score-event",
        json={
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "team": "New York Yankees",
            "opponent": "Houston Astros",
            "game_date": "2026-08-27",
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["code"] == "GOVERNED_EVENT_MODEL_AVAILABLE"
    assert data["governed_probability_capability"] == "AVAILABLE"
    assert data["governed_probability_status"] == "PRODUCED"
    assert data["probability_publishable"] is True
    assert data["probability"] == pytest.approx(0.58)
    assert data["market_weight_in_point_probability"] == 0.0
    assert data["can_execute"] is False
    assert data["can_approve_bets"] is False


def test_score_event_failure_returns_no_probability_fields(monkeypatch):
    monkeypatch.setenv("SCORING_API_KEY", "test-key")
    monkeypatch.setenv("API_KEY", "test-key")

    import gate_engine.mlb_v2_runtime as rt

    app_module = _import_app_without_manifest_boot(monkeypatch)

    monkeypatch.setattr(rt, "artifact_health", lambda *a, **k: {
        "healthy": False,
        "probability_capability": "UNAVAILABLE",
        "blockers": ["MLB_V2_ARTIFACT_MISSING:test"],
        "can_execute": False,
    })
    client = app_module.app.test_client()
    response = client.post(
        "/wow/score-event",
        json={"sport": "MLB", "market_family": "OUTRIGHT_WINNER", "team": "New York Yankees", "opponent": "Houston Astros"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 409
    data = response.get_json()
    assert data["code"] == "GOVERNED_EVENT_MODEL_UNAVAILABLE"
    assert data["probability_publishable"] is False
    assert data["fallback"] == "SECTION_8A_MANUAL_ESTIMATE_LANE"
    assert "probability" not in data
    assert "home_probability" not in data
    assert "away_probability" not in data
    assert data["can_execute"] is False
    assert data["can_approve_bets"] is False
