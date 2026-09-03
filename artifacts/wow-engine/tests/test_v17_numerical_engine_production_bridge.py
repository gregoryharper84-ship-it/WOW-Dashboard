from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.certified_numerical_engine import ModelFamily, V17Lane
from v17.numerical_engine_production_bridge import (
    attach_prop_numerical_certification,
    attach_team_event_numerical_certification,
    certify_native_probability,
)


def test_native_certification_never_changes_probability():
    payload = certify_native_probability(
        candidate_id="NBA:PLAYER:PTS:23.5:MORE",
        lane=V17Lane.PROP,
        sport="NBA",
        market_or_stat="POINTS",
        controlling_specialist="WOW_PROP_FITTED_MODEL_V1",
        model_version="artifact-v4",
        model_family=ModelFamily.DISCRETE_PMF,
        probability=0.64125,
        computation_method="CERTIFIED_DISCRETE_PMF_NATIVE",
        computation_version="V17_PROP_PMF_BRIDGE_V1",
    )
    assert payload["status"] == "PASS"
    result = payload["numerical_result"]
    assert result["raw_probability"] == pytest.approx(0.64125)
    assert result["unconditional_probability"] == pytest.approx(0.64125)
    assert result["model_family"] == "DISCRETE_PMF"
    assert result["computation_engine"] == "PYTHON_PRIMARY"
    assert result["creates_sporting_probability"] is False
    assert result["can_execute"] is False


def test_prop_bridge_certifies_both_sides_without_mutating_distribution():
    bundle = SimpleNamespace(model_artifact_version="prop-artifact-v7")
    artifact = SimpleNamespace(bundle=bundle, model_family="NEGATIVE_BINOMIAL")
    request = SimpleNamespace(sport="MLB", stat_type="PITCHER_STRIKEOUTS")
    inference = SimpleNamespace(artifact=artifact, request=request)
    lp = SimpleNamespace(probability_more=0.613, probability_less=0.351, push_probability=0.036)
    row = SimpleNamespace(prediction_id="pred-1")
    result = SimpleNamespace(inference=inference, line_probabilities=lp, row=row)

    bridged = attach_prop_numerical_certification(result, seed=19)

    assert bridged.line_probabilities.probability_more == pytest.approx(0.613)
    assert bridged.line_probabilities.probability_less == pytest.approx(0.351)
    cert = bridged.v17_numerical_engine
    assert cert["MORE"]["numerical_result"]["raw_probability"] == pytest.approx(0.613)
    assert cert["LESS"]["numerical_result"]["raw_probability"] == pytest.approx(0.351)
    assert cert["MORE"]["numerical_result"]["distribution_diagnostics"]["native_model_family"] == "NEGATIVE_BINOMIAL"
    assert cert["MORE"]["numerical_result"]["model_family"] == "DISCRETE_PMF"
    assert cert["MORE"]["can_execute"] is False


def test_team_event_bridge_certifies_home_and_away_without_changing_model_output():
    req = SimpleNamespace(sport="MLB", official_event_id="824069")
    original = {
        "code": "MLB_EVENT_MODEL_PROBABILITY_AVAILABLE",
        "raw_home_probability": 0.584,
        "raw_away_probability": 0.416,
        "calibrated_home_probability": 0.571,
        "calibrated_away_probability": 0.429,
        "model_artifact_version": "mlb-event-v3",
        "provider_identity": "WOW_MLB_GAME_WIN_PROBABILITY_EXPERT",
        "simulation_count": 50000,
        "can_execute": False,
    }
    bridged = attach_team_event_numerical_certification(original, req=req)
    assert bridged["raw_home_probability"] == pytest.approx(original["raw_home_probability"])
    assert bridged["raw_away_probability"] == pytest.approx(original["raw_away_probability"])
    assert bridged["calibrated_home_probability"] == pytest.approx(original["calibrated_home_probability"])
    assert bridged["calibrated_away_probability"] == pytest.approx(original["calibrated_away_probability"])
    cert = bridged["v17_numerical_engine"]
    assert cert["status"] == "PASS"
    assert cert["home"]["numerical_result"]["raw_probability"] == pytest.approx(0.584)
    assert cert["away"]["numerical_result"]["raw_probability"] == pytest.approx(0.416)
    assert cert["probabilities_modified_by_bridge"] is False
    assert cert["can_execute"] is False
    assert bridged["can_execute"] is False


def test_missing_numeric_package_never_manufactures_probability():
    req = SimpleNamespace(sport="TENNIS", official_event_id="event-1")
    bridged = attach_team_event_numerical_certification(
        {"code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False},
        req=req,
    )
    cert = bridged["v17_numerical_engine"]
    assert cert["status"] == "NOT_APPLICABLE_NO_COMPLETED_NUMERIC_PACKAGE"
    assert cert["creates_sporting_probability"] is False
    assert "raw_home_probability" not in bridged
    assert "raw_away_probability" not in bridged
    assert bridged["can_execute"] is False


def test_invalid_native_probability_is_typed_not_clamped():
    payload = certify_native_probability(
        candidate_id="x",
        lane=V17Lane.TEAM_EVENT_ML,
        sport="NFL",
        market_or_stat="moneyline",
        controlling_specialist="llp.nfl",
        model_version="v1",
        model_family=ModelFamily.SPORT_SPECIFIC_EVENT_SIMULATION,
        probability=1.2,
        computation_method="native",
        computation_version="v1",
    )
    assert payload["status"] == "MODEL_OUTPUT_INVALID"
    assert payload["creates_sporting_probability"] is False
    assert payload["can_execute"] is False
