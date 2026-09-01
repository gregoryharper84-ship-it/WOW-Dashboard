from mlb_event_specialist_v16 import (
    _lineup_adjustment,
    _weather_context,
    _simulate,
    _bounds,
    LineupAdjustment,
    MIN_SIMULATIONS,
    TERMINAL_CEILING,
)


def _player(pid, season_ops, split_ops, pa, split_desc="vs Right"):
    return {
        "id": pid,
        "stats": [
            {"type": {"displayName": "season"}, "splits": [{"stat": {"ops": str(season_ops)}}]},
            {"type": {"displayName": "statSplits"}, "splits": [{"split": {"description": split_desc}, "stat": {"ops": str(split_ops), "plateAppearances": pa}}]},
        ],
    }


def test_lineup_platoon_shrink_uses_current_order():
    order = list(range(1, 10))
    players = {i: _player(i, .750, .900 if i <= 4 else .700, 180) for i in order}
    adj = _lineup_adjustment(order, "R", players)
    assert adj.valid_hitters == 9
    assert 0.93 <= adj.ratio <= 1.07
    assert adj.platoon_ops != adj.season_ops


def test_weather_official_feed_context():
    feed = {"gameData": {"weather": {"temp": "77", "wind": "10 mph, Out To CF", "condition": "Clear"}, "venue": {"fieldInfo": {"roofType": "Open"}}}}
    wx = _weather_context(feed)
    assert wx.factor > 1.0
    assert wx.disruption_probability == 0.02


def test_shared_simulation_50000_and_reconciles():
    lineup = LineupAdjustment(1.0, 9, .750, .750, ())
    wx = _weather_context({"gameData": {"weather": {"temp": "70", "wind": "0 mph", "condition": "Clear"}, "venue": {"fieldInfo": {"roofType": "Open"}}}})
    features = {"opp_starter_era": 4.0, "opp_starter_bb_rate": .09, "opp_starter_prior_starts": 20, "opp_starter_pitches_last3": 250, "opp_bp_era": 4.0, "opp_bp_bb_rate": .09, "opp_bp_pitches_3d": 180, "opp_errors_pg": .55}
    result = _simulate(home_mu=4.4, away_mu=4.1, home_alpha=.31, away_alpha=.32, extra_home_win=.48, lineup_home=lineup, lineup_away=lineup, weather=wx, home_features=features, away_features=features, seed=12345, simulation_count=MIN_SIMULATIONS, favorite="HOME")
    assert abs(result["raw_home_probability"] + result["raw_away_probability"] - 1) < 1e-12
    assert 0 < result["tie_after_9_probability"] < 1
    assert result["favorite_failure_paths"]["normal_regime_probability"] != result["favorite_failure_paths"]["unconditional_probability"]
    assert result["favorite_failure_paths"]["favorite_failure_path_probability"] > 0


def test_bounds_widen_existing_dynamic_bounds_and_complement():
    score = {"calibrated_home_probability": .52, "home_lower_bound": .44, "home_upper_bound": .59}
    h_lo, h_hi, a_lo, a_hi = _bounds(score, .54, .015)
    assert h_lo < .54 < h_hi
    assert abs(a_lo - (1 - h_hi)) < 1e-12
    assert abs(a_hi - (1 - h_lo)) < 1e-12


def test_terminal_ceiling_is_hold():
    assert TERMINAL_CEILING == "MODEL_QUALIFIED_HOLD"
