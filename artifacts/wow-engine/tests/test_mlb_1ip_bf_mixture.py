from mlb_1ip_bf_mixture import score_player_bf_mixture


ARTIFACT = {
    "model_family": "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1",
    "bf_weights": {"3": 0.34, "4": 0.31, "5_PLUS": 0.35},
    "conditional_total_pitch_counts": {
        "3": {"12": 50, "14": 40, "16": 10},
        "4": {"14": 10, "16": 40, "18": 50},
        "5_PLUS": {"16": 5, "18": 20, "22": 75},
    },
}
PRIOR = {"3": 0.34, "4": 0.31, "5_PLUS": 0.35}


def test_same_line_different_bf_histories_produce_different_probabilities():
    three_batter_pitcher = score_player_bf_mixture(
        aggregate_artifact_payload=ARTIFACT,
        bf_league_prior=PRIOR,
        bf_alpha=8.0,
        pitcher_id=1,
        recent_batters_faced=[3, 3, 3, 3, 3, 3, 4, 3, 3, 3],
        line_value=15.5,
        side="MORE",
    )
    extended_inning_pitcher = score_player_bf_mixture(
        aggregate_artifact_payload=ARTIFACT,
        bf_league_prior=PRIOR,
        bf_alpha=8.0,
        pitcher_id=2,
        recent_batters_faced=[5, 4, 5, 5, 4, 5, 5, 4, 5, 5],
        line_value=15.5,
        side="MORE",
    )
    assert three_batter_pitcher["aggregate_baseline_probability"] == extended_inning_pitcher["aggregate_baseline_probability"]
    assert three_batter_pitcher["selected_probability"] != extended_inning_pitcher["selected_probability"]
    assert three_batter_pitcher["selected_probability"] < extended_inning_pitcher["selected_probability"]


def test_identical_histories_remain_identical():
    a = score_player_bf_mixture(
        aggregate_artifact_payload=ARTIFACT,
        bf_league_prior=PRIOR,
        bf_alpha=50.0,
        pitcher_id=10,
        recent_batters_faced=[3, 4, 3, 5, 4],
        line_value=15.5,
        side="MORE",
    )
    b = score_player_bf_mixture(
        aggregate_artifact_payload=ARTIFACT,
        bf_league_prior=PRIOR,
        bf_alpha=50.0,
        pitcher_id=20,
        recent_batters_faced=[3, 4, 3, 5, 4],
        line_value=15.5,
        side="MORE",
    )
    assert a["selected_probability"] == b["selected_probability"]


def test_more_less_partition_half_point_line():
    more = score_player_bf_mixture(
        aggregate_artifact_payload=ARTIFACT,
        bf_league_prior=PRIOR,
        bf_alpha=12.0,
        pitcher_id=1,
        recent_batters_faced=[3, 4, 5, 4, 5],
        line_value=15.5,
        side="MORE",
    )
    less = score_player_bf_mixture(
        aggregate_artifact_payload=ARTIFACT,
        bf_league_prior=PRIOR,
        bf_alpha=12.0,
        pitcher_id=1,
        recent_batters_faced=[3, 4, 5, 4, 5],
        line_value=15.5,
        side="LESS",
    )
    assert abs(more["selected_probability"] + less["selected_probability"] - 1.0) < 1e-12
    assert more["can_execute"] is False
    assert more["probability_publishable"] is False
    assert more["rank_eligible"] is False
