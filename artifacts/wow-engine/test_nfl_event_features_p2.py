from copy import deepcopy

from nfl_event_features_p2 import FEATURE_ORDER, build_prior_feature_rows


def _game(i, day, home, away, hs, aws, season=2025, week=None):
    return {
        "game_id": f"g{i}", "season": season, "week": week or i,
        "gameday": day, "home_team": home, "away_team": away,
        "home_score": hs, "away_score": aws, "home_win": hs > aws,
        "tie": hs == aws, "schedule_content_sha256": "a" * 64,
    }


def _summary(game_id, team, opponent, is_home, epa, def_epa, success, turnovers=1):
    return {
        "game_id": game_id, "team": team, "opponent": opponent, "is_home": is_home,
        "offensive_plays": 60, "offensive_epa_sum": epa * 60,
        "defensive_epa_mean": def_epa, "success_plays": int(success * 60),
        "turnovers": turnovers, "sacks_allowed": 2,
        "special_teams_epa_sum": 0.5, "pbp_content_sha256": "b" * 64,
    }


def _fixture():
    games = []
    summaries = []
    for i in range(1, 7):
        games.append(_game(i, f"2025-09-{i:02d}", "AAA", "BBB", 24 + i, 17, week=i))
        summaries.extend([
            _summary(f"g{i}", "AAA", "BBB", True, 0.10 + i / 100, -0.05, 0.50, 1),
            _summary(f"g{i}", "BBB", "AAA", False, -0.04, 0.10, 0.42, 2),
        ])
    games.append(_game(7, "2025-09-20", "AAA", "BBB", 13, 13, week=7))
    summaries.extend([
        _summary("g7", "AAA", "BBB", True, 9.99, 9.99, 0.99, 9),
        _summary("g7", "BBB", "AAA", False, -9.99, -9.99, 0.01, 9),
    ])
    return games, summaries


def test_target_game_stats_cannot_change_its_features():
    games, summaries = _fixture()
    rows1 = {r["game_id"]: r for r in build_prior_feature_rows(games, summaries)}
    changed = deepcopy(summaries)
    for row in changed:
        if row["game_id"] == "g7":
            row["offensive_epa_sum"] = -999999
            row["defensive_epa_mean"] = 999999
            row["turnovers"] = 99
    rows2 = {r["game_id"]: r for r in build_prior_feature_rows(games, changed)}
    assert rows1["g7"]["features"] == rows2["g7"]["features"]
    assert rows1["g7"]["feature_vector"] == rows2["g7"]["feature_vector"]


def test_current_game_score_is_label_only_not_feature_input():
    games, summaries = _fixture()
    first = {r["game_id"]: r for r in build_prior_feature_rows(games, summaries)}["g7"]
    changed_games = deepcopy(games)
    for game in changed_games:
        if game["game_id"] == "g7":
            game["home_score"] = 99
            game["away_score"] = 0
            game["home_win"] = True
            game["tie"] = False
    second = {r["game_id"]: r for r in build_prior_feature_rows(changed_games, summaries)}["g7"]
    assert first["features"] == second["features"]
    assert first["target_outcome"] == "TIE"
    assert second["target_outcome"] == "HOME_WIN"


def test_tie_is_preserved_and_temporal_cutoff_is_strict():
    games, summaries = _fixture()
    row = {r["game_id"]: r for r in build_prior_feature_rows(games, summaries)}["g7"]
    assert row["target_outcome"] == "TIE"
    assert row["max_prior_gameday"] < row["gameday"]
    assert row["training_eligible"] is True
    assert row["exclusion_reasons"] == []


def test_thin_history_is_held_not_imputed_as_fake_average():
    games, summaries = _fixture()
    row = build_prior_feature_rows(games[:2], summaries[:4])[0]
    assert row["training_eligible"] is False
    assert "HOME_PRIOR_SAMPLE_THIN" in row["exclusion_reasons"]
    assert "AWAY_PRIOR_SAMPLE_THIN" in row["exclusion_reasons"]


def test_feature_order_is_fixed_and_no_probability_authority_exists():
    games, summaries = _fixture()
    row = {r["game_id"]: r for r in build_prior_feature_rows(games, summaries)}["g7"]
    assert row["feature_order"] == list(FEATURE_ORDER)
    assert len(row["feature_vector"]) == len(FEATURE_ORDER)
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    forbidden = {"model_probability", "calibrated_probability", "lower_bound", "edge", "stake"}
    assert forbidden.isdisjoint(row)
