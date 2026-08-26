from gate_engine.cross_sport_ranker import rank


def _row(name, lower, calibrated, market=None):
    row = {
        "player": name, "sport": "WNBA", "stat_key": "PTS", "line": 20.5,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "calibrated_probability_lower_bound": lower,
        "calibrated_probability": calibrated,
    }
    if market is not None:
        row["market_probability"] = market
    return row


def test_existing_ranker_keeps_clb_probability_and_market_edge_ordering_distinct():
    output = rank([
        _row("CLB First", 0.72, 0.74, 0.70),
        _row("Central First", 0.68, 0.81, 0.82),
        _row("Best Edge", 0.64, 0.70, 0.55),
    ], top_n=3).to_dict()

    assert output["highest_hit_probability"][0]["player_name"] == "CLB First"
    assert [item["rank"] for item in output["highest_hit_probability"]] == [1, 2, 3]
    assert output["highest_calibrated_prob"][0]["player_name"] == "Central First"
    assert [item["rank"] for item in output["highest_calibrated_prob"]] == [1, 2, 3]
    assert output["best_edge"][0]["player_name"] == "Best Edge"
    assert all(item["can_execute"] is False for item in output["highest_hit_probability"])