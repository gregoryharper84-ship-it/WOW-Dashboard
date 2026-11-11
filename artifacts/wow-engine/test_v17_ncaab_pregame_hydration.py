"""Adversarial research-only pregame contract regressions, Issue #1644."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.ncaab_pregame_hydration import NCAABPregameHold, _hash, hydrate_pregame
from v17.ncaab_sportsdataverse_candidate import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY, SOURCE_POLICY_ID,
    _history_entry, _summary,
)

UTC = timezone.utc
TARGET = datetime(2026, 1, 20, 18, tzinfo=UTC)


def stamp(t):
    return t.isoformat()


def source(obj, name):
    obj["source_manifest"] = {
        "source": name, "source_policy": SOURCE_POLICY_ID,
        "game_id": obj["game_id"], "home_team_id": obj["home_team_id"],
        "away_team_id": obj["away_team_id"], "event_version": obj["event_version"],
        "market_features_used": False,
    }
    obj["source_manifest_sha256"] = _hash(obj["source_manifest"])
    obj["source_policy_id"] = SOURCE_POLICY_ID
    obj["market_features_used"] = False
    obj["can_execute"] = False
    return obj


def matchup():
    event = source({
        "game_id": "400099999", "official_event_id": "NCAAB:400099999",
        "event_version": 1, "home_team_id": "12", "away_team_id": "34",
        "season": 2026, "neutral_site": False,
        "event_start_time": stamp(TARGET), "source_as_of": stamp(TARGET - timedelta(hours=2)),
    }, "upcoming")
    games = []
    for i in range(6):
        start = TARGET - timedelta(days=17 - 2 * i)
        game = source({
            "game_id": str(400099000 + i), "event_version": 1, "season": 2026,
            "event_start_time": stamp(start),
            "settled_at": stamp(start + timedelta(hours=3)),
            "source_as_of": stamp(start + timedelta(hours=4)),
            "home_team_id": "12", "away_team_id": "34",
            "home": {
                "team_id": "12", "team_home_away": "home",
                "team_score": 80 + i, "total_rebounds": 30 + i,
                "turnovers": 10 + i, "field_goal_pct": 0.50,
                "three_point_field_goal_pct": 0.40,
            },
            "away": {
                "team_id": "34", "team_home_away": "away",
                "team_score": 60 + i, "total_rebounds": 20 + i,
                "turnovers": 12 + i, "field_goal_pct": 0.40,
                "three_point_field_goal_pct": 0.30,
            },
        }, f"settled:{i}")
        games.append(game)
    return event, games


def err(event, games, code):
    with pytest.raises(NCAABPregameHold) as exc:
        hydrate_pregame(event, games)
    assert exc.value.code == code


def test_valid_hydration_exact_feature_order_values_and_policy():
    event, games = matchup()
    data = hydrate_pregame(event, games)
    assert data["status"] == "RESEARCH_FEATURES_READY"
    assert data["feature_names"] == list(FEATURE_NAMES)
    assert tuple(data["features"]) == FEATURE_NAMES
    assert data["feature_values"] == [data["features"][n] for n in FEATURE_NAMES]
    assert data["model_family"] == MODEL_FAMILY
    assert data["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert data["can_execute"] is False
    assert data["probability_publishable"] is False
    assert data["market_features_used"] is False
    assert data["research_only"] is True
    assert len(data["input_sha256"]) == 64
    assert data["source_evidence"]["home_prior_event_ids"] == [g["game_id"] for g in games]
    assert data["source_evidence"]["away_prior_event_ids"] == [g["game_id"] for g in games]


def test_feature_statistics_match_original_training_summary_exactly():
    event, games = matchup()
    data = hydrate_pregame(event, games)
    h = [_history_entry(g["home"], g["away"], game_id=g["game_id"],
                        start=datetime.fromisoformat(g["event_start_time"])) for g in games]
    a = [_history_entry(g["away"], g["home"], game_id=g["game_id"],
                        start=datetime.fromisoformat(g["event_start_time"])) for g in games]
    hs, aws = _summary(h, TARGET), _summary(a, TARGET)
    assert data["features"]["home_recent_win_rate"] == hs["win_rate"]
    assert data["features"]["away_recent_point_diff"] == aws["point_diff"]
    assert data["features"]["home_games_prior_log"] == hs["games_prior_log"]
    assert data["features"]["away_rest_days_capped"] == aws["rest_days_capped"]
    assert data["features"]["home_recent_rebound_margin_proxy"] == hs["rebound_margin_proxy"]


def test_input_order_independent_and_manifest_tamper_detected():
    event, games = matchup()
    first = hydrate_pregame(event, games)
    second = hydrate_pregame(event, list(reversed(games)))
    assert first["input_sha256"] == second["input_sha256"]
    assert first["feature_values"] == second["feature_values"]
    modified = deepcopy(games)
    modified[2]["home"]["team_score"] += 1
    # A modified game score must change the immutable input identity, even when
    # a provider source-manifest digest was copied without modification.
    assert hydrate_pregame(event, modified)["input_sha256"] != first["input_sha256"]


@pytest.mark.parametrize("mutator,code", [
    (lambda e, g: e.update(home_team_id="Duke"), "NCAAB_PREGAME_CANONICAL_ID_REQUIRED"),
    (lambda e, g: e.update(away_team_id="12"), "NCAAB_PREGAME_TEAM_ID_COLLISION"),
    (lambda e, g: e.update(home_team_id="34", away_team_id="12"), "NCAAB_PREGAME_MANIFEST_IDENTITY_MISMATCH"),
    (lambda e, g: e.update(official_event_id="NCAAB:0"), "NCAAB_PREGAME_EVENT_ID_MISMATCH"),
    (lambda e, g: e.update(neutral_site=True), "NCAAB_PREGAME_NEUTRAL_SITE_REVIEW_REQUIRED"),
    (lambda e, g: e.update(source_as_of=stamp(TARGET)), "NCAAB_PREGAME_TARGET_NOT_PREGAME"),
    (lambda e, g: e.update(source_as_of=stamp(TARGET - timedelta(days=3))), "NCAAB_PREGAME_SOURCE_STALE"),
    (lambda e, g: e.update(home_score=90), "NCAAB_PREGAME_CURRENT_RESULT_LEAKAGE"),
    (lambda e, g: e["source_manifest"].update(source="tamper"), "NCAAB_PREGAME_SOURCE_TAMPERED"),
    (lambda e, g: e.update(market_features_used=True), "NCAAB_PREGAME_MARKET_INPUT_REJECTED"),
    (lambda e, g: e.update(can_execute=True), "NCAAB_PREGAME_EXECUTION_FLAG_REJECTED"),
    (lambda e, g: e.update(event_version=0), "NCAAB_PREGAME_EVENT_VERSION_INVALID"),
    (lambda e, g: e.update(source_policy_id="OTHER"), "NCAAB_PREGAME_SOURCE_POLICY_MISMATCH"),
    (lambda e, g: g[2]["away"].update(team_id="12"), "NCAAB_PREGAME_TEAM_SIDE_MISMATCH"),
    (lambda e, g: g[2]["away"].update(team_home_away="home"), "NCAAB_PREGAME_TEAM_SIDE_MISMATCH"),
    (lambda e, g: g[2]["home"].update(team_score=None), "NCAAB_PREGAME_HISTORY_STAT_MISSING"),
    (lambda e, g: g[2]["home"].update(field_goal_pct=float("nan")), "NCAAB_PREGAME_HISTORY_STAT_INVALID"),
    (lambda e, g: g[2].update(settled_at=stamp(TARGET + timedelta(days=1))), "NCAAB_PREGAME_HISTORY_FUTURE_LEAKAGE"),
    (lambda e, g: g[2].update(source_as_of=stamp(TARGET + timedelta(hours=2))), "NCAAB_PREGAME_HISTORY_FUTURE_LEAKAGE"),
    (lambda e, g: g[2].update(event_version=0), "NCAAB_PREGAME_EVENT_VERSION_INVALID"),
    (lambda e, g: g[2].update(season=2027), "NCAAB_PREGAME_FUTURE_SEASON"),
    (lambda e, g: g[2]["source_manifest"].update(source="wrong"), "NCAAB_PREGAME_SOURCE_TAMPERED"),
    (lambda e, g: g[2].update(can_execute=True), "NCAAB_PREGAME_EXECUTION_FLAG_REJECTED"),
    (lambda e, g: g[2].update(market_features_used=True), "NCAAB_PREGAME_MARKET_INPUT_REJECTED"),
    (lambda e, g: g.append(deepcopy(g[0])), "NCAAB_PREGAME_DUPLICATE_GAME_VERSION"),
])
def test_negative_paths(mutator, code):
    event, games = matchup()
    mutator(event, games)
    err(event, games, code)


def test_early_season_hold_with_previous_season_available():
    event, games = matchup()
    for game in games[:2]:
        game["season"] = 2025
    err(event, games, "NCAAB_PREGAME_SMALL_SAMPLE_HOLD")


def test_history_game_id_cannot_equal_target_even_with_same_team():
    event, games = matchup()
    games[0]["game_id"] = event["game_id"]
    err(event, games, "NCAAB_PREGAME_TARGET_IN_HISTORY")


def test_timezone_offsets_compare_as_instants_not_strings():
    event, games = matchup()
    event["source_as_of"] = "2026-01-20T10:00:00-06:00"  # 16:00Z
    assert hydrate_pregame(event, games)["status"] == "RESEARCH_FEATURES_READY"


def test_no_current_game_predictions_or_trading_capabilities():
    event, games = matchup()
    result = hydrate_pregame(event, games)
    for forbidden in ("raw_probability", "model_probability", "model_probability_publishable",
                      "prediction", "order", "wager", "calibration_certificate"):
        assert forbidden not in result
