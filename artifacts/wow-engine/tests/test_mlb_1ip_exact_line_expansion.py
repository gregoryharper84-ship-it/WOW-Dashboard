import json
from pathlib import Path

import pytest

from mlb_1ip_empirical_specialist import score_mlb_1ip_empirical
from mlb_1ip_player_conditioned import ARTIFACT_FORMAT, MODEL_FAMILY


EXPANDED_LINES = [11.5, 13.5, 14.5, 15.5, 16.5, 17.5, 19.5, 21.5]


def _bundle():
    path = (
        Path(__file__).resolve().parents[1]
        / "research-fixtures"
        / "mlb_1ip_player_conditioning_dev_bundle_20260914.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _record(lines=EXPANDED_LINES):
    bundle = _bundle()
    aggregate = bundle["aggregate_artifact"]
    bf = bundle["bf_artifact"]
    payload = {
        "model_family": MODEL_FAMILY,
        "artifact_format": ARTIFACT_FORMAT,
        "aggregate_artifact_checksum": aggregate["artifact_checksum"],
        "aggregate_artifact_payload": aggregate["artifact_payload"],
        "bf_model_family": bf["model_family"],
        "bf_alpha": bf["alpha"],
        "bf_league_prior": bf["league_prior"],
        "recent_history_limit": bf["recent_history_limit"],
        "probability_publishable": False,
        "can_execute": False,
    }
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": "MLB_1IP_PLAYER_CONDITIONED_TEST_LINES8",
        "artifact_checksum": "test-player-conditioned-lines8",
        "certification_id": "PROP-CERT-TEST-MLB-1IP-LINES8",
        "artifact_payload": payload,
        "supported_line_min": min(lines),
        "supported_line_max": max(lines),
        "feature_schema_version": "PROP_FEATURES_V1",
        "validation_metrics": {"validated_lines": list(lines)},
        "probability_publishable": False,
        "can_execute": False,
    }


def _prior(pitcher_id, history):
    return {
        "status": "RESOLVED",
        "player_conditioning": {
            "pitcher_id": pitcher_id,
            "recent_1ip_batters_faced": list(history),
        },
    }


@pytest.mark.parametrize("line", [14.5, 16.5])
def test_new_exact_lines_are_model_evaluated_only_when_registry_certifies_them(line):
    result = score_mlb_1ip_empirical(
        artifact_record=_record(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=[],
        line_value=line,
        side="MORE",
        failure_path_prior=_prior(101, [3, 4, 4, 5, 3, 4, 5, 5, 4, 3]),
    )
    assert result["model_evaluated"] is True
    assert result["model_family"] == MODEL_FAMILY
    assert result["certified_supported_lines"] == EXPANDED_LINES
    assert 0.0 < result["calibrated_probability"] < 1.0
    assert result["calibrated_probability_lower_bound"] <= result["calibrated_probability"]
    assert result["calibrated_probability_upper_bound"] >= result["calibrated_probability"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_expansion_does_not_create_interpolation_authority():
    result = score_mlb_1ip_empirical(
        artifact_record=_record(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=[],
        line_value=12.5,
        side="MORE",
        failure_path_prior=_prior(101, [3, 4, 4, 5, 3, 4]),
    )
    assert result["model_evaluated"] is False
    assert result["code"] == "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT"
    assert result["supported_lines"] == EXPANDED_LINES
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("line", [14.5, 16.5])
def test_player_conditioning_preserves_same_line_probability_and_bound_discrimination(line):
    low_bf = score_mlb_1ip_empirical(
        artifact_record=_record(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=[],
        line_value=line,
        side="MORE",
        failure_path_prior=_prior(201, [3, 3, 3, 3, 3, 3, 3, 3, 3, 3]),
    )
    high_bf = score_mlb_1ip_empirical(
        artifact_record=_record(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=[],
        line_value=line,
        side="MORE",
        failure_path_prior=_prior(202, [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]),
    )
    assert low_bf["model_evaluated"] is True
    assert high_bf["model_evaluated"] is True
    assert low_bf["calibrated_probability"] != high_bf["calibrated_probability"]
    assert low_bf["calibrated_probability_lower_bound"] != high_bf["calibrated_probability_lower_bound"]
    assert low_bf["probability_publishable"] is False
    assert high_bf["can_execute"] is False
