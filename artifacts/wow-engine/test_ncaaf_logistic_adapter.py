from __future__ import annotations

import pytest

from ncaaf_feature_transform import model_features_from_snapshot
from ncaaf_fitted_provider import NCAAFInferenceRequest, ResolvedNCAAFArtifact
from ncaaf_logistic_adapter import logistic_adapter
from ncaaf_trainer import FEATURES


def snapshot():
    row = {
        "official_event_id": "evt-1", "feature_schema_version": "NCAAF_FEATURES_V1",
        "feature_as_of": "2026-09-05T20:00:00+00:00", "home_team": "Home", "away_team": "Away", "neutral_site": False,
        "home_power_rating": 2.0, "away_power_rating": 1.0,
        "home_off_epa": .2, "away_off_epa": .1, "home_def_epa": -.1, "away_def_epa": .1,
        "home_success_rate": .5, "away_success_rate": .4, "home_explosiveness": 1.2, "away_explosiveness": 1.0,
        "home_qb_value": 1.0, "away_qb_value": .5, "home_qb_certainty": 1.0, "away_qb_certainty": .75,
        "home_ol_health": .9, "away_ol_health": .8, "home_def_front_health": .9, "away_def_front_health": .7,
        "home_skill_availability": .95, "away_skill_availability": .8, "home_rest_days": 7.0, "away_rest_days": 6.0,
        "travel_distance_miles": 500.0, "home_tempo": .6, "away_tempo": .5,
        "home_turnover_volatility": .2, "away_turnover_volatility": .3,
        "home_special_teams_rating": .4, "away_special_teams_rating": .1,
        "weather_wind_mph": 8.0, "weather_precip_probability": .1,
    }
    return row


def artifact():
    return ResolvedNCAAFArtifact(
        artifact_id="a1", model_family="NCAAF_LOGISTIC_V1", artifact_format="STANDARDIZED_LOGISTIC_JSON_V1",
        artifact_payload={"feature_names": list(FEATURES), "scaler_mean": [0.0]*len(FEATURES), "scaler_scale": [1.0]*len(FEATURES), "intercept": 0.0, "coefficients": [0.1]*len(FEATURES)},
        model_artifact_version="v1", feature_schema_version="NCAAF_FEATURES_V1", feature_transform_version="V1",
        specialist_version="s1", certification_id="c1", lifecycle_state="PROSPECTIVE_CERTIFIED", training_dataset_hash="h",
        training_code_sha="sha", artifact_checksum="sum", training_rows=300, validation_metrics={}, calibration_method="EMPIRICAL_WILSON_BINS_V1",
        calibrator_version="cal1", calibration_training_n=60,
    )


def request():
    return NCAAFInferenceRequest(official_event_id="evt-1", feature_schema_version="NCAAF_FEATURES_V1", feature_as_of="2026-09-05T20:00:00+00:00", home_team="Home", away_team="Away")


def test_shared_transform_matches_trainer_schema_exactly():
    transformed = model_features_from_snapshot(snapshot())
    assert tuple(transformed.keys()) == FEATURES
    assert transformed["power_delta"] == 1.0
    assert transformed["rest_days_delta"] == 1.0
    assert transformed["neutral_site"] == 0.0


def test_logistic_adapter_returns_strict_normalized_probability():
    result = logistic_adapter(artifact(), request(), snapshot())
    assert 0.0 < result.home_probability < 1.0
    assert 0.0 < result.away_probability < 1.0
    assert result.home_probability + result.away_probability == pytest.approx(1.0)


def test_logistic_adapter_rejects_team_identity_mismatch():
    bad = snapshot(); bad["home_team"] = "Wrong"
    with pytest.raises(Exception) as exc:
        logistic_adapter(artifact(), request(), bad)
    assert getattr(exc.value, "code", None) == "NCAAF_TEAM_IDENTITY_MISMATCH"
