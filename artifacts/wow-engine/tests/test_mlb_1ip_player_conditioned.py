import pytest

from mlb_1ip_player_conditioned import (
    ARTIFACT_FORMAT,
    MODEL_FAMILY,
    score_player_conditioned_1ip,
)


AGGREGATE = {
    "model_family": "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1",
    "artifact_format": "JSON_CONDITIONAL_DISCRETE_PMF_V1",
    "artifact_checksum": "aggregate-test",
    "bf_weights": {"3": 0.34, "4": 0.31, "5_PLUS": 0.35},
    "conditional_total_pitch_counts": {
        "3": {"12": 50, "14": 40, "16": 10},
        "4": {"14": 10, "16": 40, "18": 50},
        "5_PLUS": {"16": 5, "18": 20, "22": 75},
    },
    "training_rows": 300,
    "probability_publishable": False,
    "can_execute": False,
}

ARTIFACT = {
    "model_family": MODEL_FAMILY,
    "artifact_format": ARTIFACT_FORMAT,
    "aggregate_artifact_checksum": "aggregate-test",
    "aggregate_artifact_payload": AGGREGATE,
    "bf_model_family": "MLB_1IP_BF_DIRICHLET_SHRINKAGE_V1",
    "bf_alpha": 8.0,
    "bf_league_prior": {"3": 0.34, "4": 0.31, "5_PLUS": 0.35},
    "recent_history_limit": 10,
    "probability_publishable": False,
    "can_execute": False,
}


def test_same_line_different_pitchers_get_different_probability_and_bound():
    clean = score_player_conditioned_1ip(
        artifact_payload=ARTIFACT,
        pitcher_id=1,
        recent_batters_faced=[3, 3, 3, 3, 3, 3, 4, 3, 3, 3],
        line_value=15.5,
        side="MORE",
    )
    extended = score_player_conditioned_1ip(
        artifact_payload=ARTIFACT,
        pitcher_id=2,
        recent_batters_faced=[5, 4, 5, 5, 4, 5, 5, 4, 5, 5],
        line_value=15.5,
        side="MORE",
    )
    assert clean["selected_probability"] < extended["selected_probability"]
    assert clean["lower_bound"] < extended["lower_bound"]
    assert clean["player_discrimination_status"] == "PLAYER_CONDITIONED_BF_MIXTURE"
    assert clean["can_execute"] is False
    assert clean["probability_publishable"] is False


def test_half_point_more_less_partition():
    more = score_player_conditioned_1ip(
        artifact_payload=ARTIFACT,
        pitcher_id=5,
        recent_batters_faced=[3, 4, 5, 4, 5],
        line_value=15.5,
        side="MORE",
    )
    less = score_player_conditioned_1ip(
        artifact_payload=ARTIFACT,
        pitcher_id=5,
        recent_batters_faced=[3, 4, 5, 4, 5],
        line_value=15.5,
        side="LESS",
    )
    assert abs(more["selected_probability"] + less["selected_probability"] - 1.0) < 1e-12
    assert more["lower_bound"] <= more["selected_probability"] <= more["upper_bound"]
    assert less["lower_bound"] <= less["selected_probability"] <= less["upper_bound"]


def test_thin_history_fails_closed():
    with pytest.raises(ValueError, match="MLB_1IP_PLAYER_CONDITIONING_HISTORY_INSUFFICIENT"):
        score_player_conditioned_1ip(
            artifact_payload=ARTIFACT,
            pitcher_id=9,
            recent_batters_faced=[3, 4, 5, 4],
            line_value=15.5,
            side="MORE",
        )
