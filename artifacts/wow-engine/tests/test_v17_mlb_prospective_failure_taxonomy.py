from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mlb_event_specialist_v16 import ProspectiveModelUnavailable, _feature_map, _required_numeric_inputs, _stat_float, _player_ops, _weather_context, _lineup_adjustment
from mlb_event_prospective_runtime import _typed_prospective_failure
from v17 import mlb_event_bridge_repair as bridge
from v17.mlb_prospective_failure_taxonomy import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    classify_mlb_prospective_failure,
)


def test_input_contract_failure_is_not_collapsed_to_model_unavailable():
    exc = ProspectiveModelUnavailable("feature_vector_mismatch")
    failure = classify_mlb_prospective_failure(exc)
    assert failure.code == MODEL_INPUTS_INSUFFICIENT
    assert failure.status_code == 422
    assert failure.can_execute is False

    routed = _typed_prospective_failure(exc)
    assert routed.status_code == 422
    assert routed.detail["code"] == MODEL_INPUTS_INSUFFICIENT
    assert routed.detail["reason"] == "feature_vector_mismatch"
    assert routed.detail["rank_eligible"] is False
    assert routed.detail["probability_publishable"] is False
    assert routed.detail["can_execute"] is False


@pytest.mark.parametrize(
    ("reason", "expected_code", "expected_status"),
    [
        ("prospective_bounds_invalid", MODEL_OUTPUT_INVALID, 409),
        ("immutable_specialist_snapshot_write_failed", MODEL_SCORER_FAILED, 503),
        ("prospective_certified_artifact_missing", MODEL_UNAVAILABLE, 409),
        ("calibration_health_not_pass", MODEL_INPUTS_INSUFFICIENT, 422),
        ("confirmed_strict_pregame_lineup_missing", MODEL_INPUTS_INSUFFICIENT, 422),
        ("prospective_path_requires_held_fitted_baseline", MODEL_INPUTS_INSUFFICIENT, 422),
        ("wow_mlb_forward_score_snapshots:required_row_missing", MODEL_INPUTS_INSUFFICIENT, 422),
        ("wow_mlb_v2d_intercept_calibration:required_row_missing", MODEL_INPUTS_INSUFFICIENT, 422),
        ("wow_mlb_v2b_distribution_state:required_row_missing", MODEL_UNAVAILABLE, 409),
    ],
)
def test_known_prospective_failures_map_to_v17_taxonomy(reason, expected_code, expected_status):
    failure = classify_mlb_prospective_failure(ProspectiveModelUnavailable(reason))
    assert (failure.code, failure.status_code) == (expected_code, expected_status)
    assert failure.can_execute is False


def test_calibration_rejection_preserves_established_blocker_contract():
    for reason in (
        "calibration_health_not_pass",
        "wow_mlb_v2d_calibration_health:required_row_missing",
        "wow_mlb_v2d_intercept_calibration:required_row_missing",
    ):
        failure = classify_mlb_prospective_failure(ProspectiveModelUnavailable(reason))
        assert failure.code == MODEL_INPUTS_INSUFFICIENT
        assert failure.status_code == 422
        assert failure.blocker_code == "CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE"
        assert failure.can_execute is False



def test_null_required_numeric_input_is_typed_before_float_conversion():
    with pytest.raises(ProspectiveModelUnavailable) as caught:
        _required_numeric_inputs(
            {"home_mu": None, "away_mu": 4.2},
            ("home_mu", "away_mu"),
            source="score",
        )
    assert str(caught.value) == "prospective_required_numeric_input_invalid:score.home_mu"
    failure = classify_mlb_prospective_failure(caught.value)
    assert failure.code == MODEL_INPUTS_INSUFFICIENT
    assert failure.status_code == 422
    assert failure.blocker_code == "SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT"
    assert failure.can_execute is False



@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf"), True, False])
def test_nonfinite_or_boolean_mlb_numeric_evidence_is_input_blocked(bad_value):
    with pytest.raises(ProspectiveModelUnavailable) as caught:
        _required_numeric_inputs(
            {"home_mu": bad_value, "away_mu": 4.2},
            ("home_mu", "away_mu"),
            source="score",
        )
    assert str(caught.value) == "prospective_required_numeric_input_invalid:score.home_mu"
    failure = classify_mlb_prospective_failure(caught.value)
    assert failure.code == MODEL_INPUTS_INSUFFICIENT
    assert failure.status_code == 422
    assert failure.can_execute is False


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_feature_vector_fails_closed_as_input(bad_value):
    with pytest.raises(ProspectiveModelUnavailable, match="feature_vector_invalid") as caught:
        _feature_map({"feature_names": ["opp_starter_era"], "feature_vector": [bad_value]})
    failure = classify_mlb_prospective_failure(caught.value)
    assert failure.code == MODEL_INPUTS_INSUFFICIENT
    assert failure.can_execute is False


@pytest.mark.parametrize("boolean", [True, False])
def test_boolean_feature_vector_is_invalid_model_input(boolean):
    with pytest.raises(ProspectiveModelUnavailable, match="feature_vector_invalid") as caught:
        _feature_map({"feature_names": ["opp_starter_era"], "feature_vector": [boolean]})
    failure = classify_mlb_prospective_failure(caught.value)
    assert failure.code == MODEL_INPUTS_INSUFFICIENT
    assert failure.can_execute is False


def test_calibration_training_count_has_required_numeric_preflight():
    source = (Path(__file__).resolve().parents[1] / "mlb_event_specialist_v16.py").read_text()
    assert '("intercept_shift", "prior_games"), source="calibration"' in source
    assert 'cal_numeric["prior_games"].is_integer()' in source
    assert '"calibration_training_n": int(cal_numeric["prior_games"])' in source


def test_unknown_invoked_scorer_exception_fails_as_scorer_not_unavailable():
    failure = classify_mlb_prospective_failure(RuntimeError("unexpected_numeric_runtime"))
    assert failure.code == MODEL_SCORER_FAILED
    assert failure.status_code == 503
    assert failure.code != MODEL_UNAVAILABLE


def test_team_event_bridge_uses_same_classifier_for_specialist_input_failure(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "team_event_capability",
        lambda sport: SimpleNamespace(
            status="AVAILABLE",
            controlling_specialist="wow.mlb-game-win-probability-expert",
            blocker=None,
        ),
    )
    monkeypatch.setattr(
        bridge,
        "_resolve_bridge_payload",
        lambda req, event_api: {
            "ok": True,
            "bridge_payload": {"code": "REAL_FITTED_MODEL_PATH_PROVEN"},
            "client": object(),
            "source_snapshot_id": "snapshot-1",
            "source_snapshot_timestamp": "2026-09-24T15:00:00+00:00",
            "latest_material_update_timestamp": "2026-09-24T15:00:00+00:00",
        },
    )

    def scorer(**kwargs):
        raise ProspectiveModelUnavailable("feature_vector_mismatch")

    req = SimpleNamespace(source_snapshot_id="snapshot-1")
    with pytest.raises(HTTPException) as caught:
        bridge.score_event_v17_bridge(req, event_api=object(), scorer_override=scorer)

    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert detail["code"] == MODEL_INPUTS_INSUFFICIENT
    assert detail["blocker_code"] == "SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT"
    assert detail["scorer_error_code"] == "feature_vector_mismatch"
    assert detail["sport_model_invoked"] is True
    assert detail["rank_eligible"] is False
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_team_event_bridge_preserves_output_scorer_unavailable_and_calibration_taxonomy(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "team_event_capability",
        lambda sport: SimpleNamespace(
            status="AVAILABLE",
            controlling_specialist="wow.mlb-game-win-probability-expert",
            blocker=None,
        ),
    )
    monkeypatch.setattr(
        bridge,
        "_resolve_bridge_payload",
        lambda req, event_api: {
            "ok": True,
            "bridge_payload": {"code": "REAL_FITTED_MODEL_PATH_PROVEN"},
            "client": object(),
            "source_snapshot_id": "snapshot-1",
            "source_snapshot_timestamp": "2026-09-24T15:00:00+00:00",
            "latest_material_update_timestamp": "2026-09-24T15:00:00+00:00",
        },
    )
    req = SimpleNamespace(source_snapshot_id="snapshot-1")

    for reason, expected_code, expected_status, expected_blocker in (
        ("prospective_bounds_invalid", MODEL_OUTPUT_INVALID, 409, "SCORED_MODEL_VALIDATION_FAILED"),
        ("immutable_specialist_snapshot_write_failed", MODEL_SCORER_FAILED, 503, "EVENT_MODEL_BRIDGE_UNAVAILABLE"),
        ("prospective_certified_artifact_missing", MODEL_UNAVAILABLE, 409, "MLB_EVENT_MODEL_NOT_AVAILABLE"),
        ("calibration_health_not_pass", MODEL_INPUTS_INSUFFICIENT, 422, "CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE"),
    ):
        def scorer(**kwargs):
            raise ProspectiveModelUnavailable(reason)

        with pytest.raises(HTTPException) as caught:
            bridge.score_event_v17_bridge(req, event_api=object(), scorer_override=scorer)
        assert caught.value.status_code == expected_status
        assert caught.value.detail["code"] == expected_code
        assert caught.value.detail["blocker_code"] == expected_blocker
        assert caught.value.detail["can_execute"] is False


def test_valid_probability_math_source_is_untouched_by_taxonomy_patch():
    root = Path(__file__).resolve().parents[1]
    specialist = (root / "mlb_event_specialist_v16.py").read_text()
    assert "def _simulate(" in specialist
    assert "def _bounds(" in specialist
    assert "class ProspectiveModelUnavailable" in specialist
    assert "classify_mlb_prospective_failure" not in specialist


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf"), True, False, "NaN", "Infinity", "bad"])
def test_official_player_ops_nonfinite_or_boolean_values_are_missing_not_model_inputs(bad_value):
    assert _stat_float({"ops": bad_value}, "ops") is None


def test_finite_official_player_ops_remains_unchanged():
    assert _stat_float({"ops": "0.912"}, "ops") == pytest.approx(0.912)


@pytest.mark.parametrize("bad_pa", [float("inf"), -float("inf"), float("nan"), True, False, "bogus", -9])
def test_official_split_plate_appearances_invalid_values_never_crash_or_count(bad_pa):
    player = {
        "stats": [{
            "type": {"displayName": "statSplits"},
            "splits": [{
                "split": {"description": "vs Right"},
                "stat": {"ops": "0.905", "plateAppearances": bad_pa},
            }],
        }]
    }
    season, split, appearances = _player_ops(player, "R")
    assert season is None
    assert split == pytest.approx(0.905)
    assert appearances == 0


@pytest.mark.parametrize("bad_temp", ["NaN", "inf", "-Infinity", True, False, "malformed"])
def test_official_weather_nonfinite_temperature_is_missing_not_nan_probability(bad_temp):
    feed = {
        "gameData": {
            "weather": {
                "temp": bad_temp,
                "wind": "8 mph, out to center field",
                "condition": "Sunny",
            },
            "venue": {"fieldInfo": {"roofType": "Open"}},
        }
    }
    weather = _weather_context(feed)
    import math
    assert weather.temperature_f is None
    assert math.isfinite(weather.factor)
    assert math.isfinite(weather.disruption_probability)
    assert 0.0 <= weather.disruption_probability <= 1.0


def test_nan_official_ops_is_excluded_from_lineup_instead_of_poisoning_ratio():
    import math
    players = {}
    for pid in range(1, 10):
        players[pid] = {
            "stats": [{
                "type": {"displayName": "season"},
                "splits": [{"stat": {"ops": "NaN" if pid == 9 else "0.850"}}],
            }],
        }
    adj = _lineup_adjustment(list(range(1, 10)), "R", players)
    assert adj.valid_hitters == 8
    assert adj.missing_ids == (9,)
    assert math.isfinite(adj.ratio)
    assert math.isfinite(adj.season_ops)
    assert math.isfinite(adj.platoon_ops)
    assert 0.93 <= adj.ratio <= 1.07
