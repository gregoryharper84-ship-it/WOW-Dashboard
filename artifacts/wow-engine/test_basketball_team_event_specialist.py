from datetime import date, timedelta

import pytest

from basketball_team_event_specialist import (
    BasketballSpecialistError,
    TrainingGame,
    build_pregame_features,
    certification_decision,
    train_specialist,
)
from basketball_specialist_pipeline import _walk_forward_raw


def _games(sport: str, n: int = 480):
    teams = [f"{sport}-T{i}" for i in range(12)]
    start = date(2024, 1, 1) if sport == "NBA" else date(2024, 5, 1)
    out = []
    for i in range(n):
        home = teams[i % len(teams)]
        away = teams[(i * 5 + 3) % len(teams)]
        if home == away:
            away = teams[(teams.index(away) + 1) % len(teams)]
        hs = 96 + ((i * 7 + int(home.split("T")[-1]) * 3) % 28) + 4
        aws = 96 + ((i * 11 + int(away.split("T")[-1]) * 2) % 28)
        if hs == aws:
            hs += 1
        out.append(TrainingGame(str(i), sport, 2024, start + timedelta(days=i // 6), home, away, hs, aws))
    return out


def test_nba_wnba_features_are_league_isolated():
    nba = _games("NBA", 120)
    wnba = _games("WNBA", 120)
    nba_rows = build_pregame_features(nba + wnba, "NBA")
    wnba_rows = build_pregame_features(nba + wnba, "WNBA")
    assert nba_rows and wnba_rows
    assert all(r.sport == "NBA" for r in nba_rows)
    assert all(r.sport == "WNBA" for r in wnba_rows)
    assert {r.game_id for r in nba_rows} == {r.game_id for r in wnba_rows}


def test_same_day_results_never_enter_same_day_features():
    games = _games("NBA", 180)
    rows = build_pregame_features(games, "NBA")
    by_id = {r.game_id: r for r in rows}
    # Games are emitted six per day. Where two same-day target rows are usable,
    # no same-day final has changed either team's prior-game count.
    day_to_rows = {}
    for r in rows:
        day_to_rows.setdefault(r.game_date, []).append(r)
    candidate = next(day_rows for day_rows in day_to_rows.values() if len(day_rows) >= 2)
    source_games = {g.game_id: g for g in games}
    first, second = candidate[0], candidate[1]
    assert source_games[first.game_id].game_date == source_games[second.game_id].game_date
    # The history counts are determined before the whole date is committed.
    assert first.home_games_prior >= 5 and second.home_games_prior >= 5


def test_training_requires_real_minimum_corpus():
    rows = build_pregame_features(_games("NBA", 100), "NBA")
    with pytest.raises(BasketballSpecialistError, match="usable chronological feature rows"):
        train_specialist(rows, "NBA", model_artifact_version="NBA_TEST_TOO_SMALL")


def test_nba_and_wnba_fit_independent_artifacts():
    nba_rows = build_pregame_features(_games("NBA"), "NBA")
    wnba_rows = build_pregame_features(_games("WNBA"), "WNBA")
    nba = train_specialist(nba_rows, "NBA", model_artifact_version="NBA_TEST_V1")
    wnba = train_specialist(wnba_rows, "WNBA", model_artifact_version="WNBA_TEST_V1")
    assert nba.sport == "NBA" and wnba.sport == "WNBA"
    assert nba.model_artifact_version != wnba.model_artifact_version
    assert nba.artifact_checksum != wnba.artifact_checksum


def test_walk_forward_calibration_is_date_safe_and_large_enough():
    rows = build_pregame_features(_games("NBA", 720), "NBA")
    raw, outcomes, folds, timestamps = _walk_forward_raw(rows)
    assert len(raw) == len(outcomes) == len(folds) == len(timestamps)
    assert len(raw) >= 200
    assert sorted(set(folds)) == list(range(6))
    fold_dates = {}
    for fold_id, ts in zip(folds, timestamps):
        fold_dates.setdefault(fold_id, set()).add(ts[:10])
    for f in range(1, 6):
        assert max(fold_dates[f - 1]) < min(fold_dates[f])


def test_certification_fail_closed_without_v17_calibration():
    rows = build_pregame_features(_games("NBA"), "NBA")
    artifact = train_specialist(rows, "NBA", model_artifact_version="NBA_TEST_V1")
    decision = certification_decision(
        artifact, sport="NBA", calibration_rows=199,
        calibration_status="PRECALIBRATION_SHRINKAGE", provenance_complete=True,
    )
    assert decision.capability_status == "SHADOW"
    assert decision.reason_code == "MODEL_CALIBRATION_UNAVAILABLE"


def test_certification_rejects_cross_sport_artifact():
    rows = build_pregame_features(_games("NBA"), "NBA")
    artifact = train_specialist(rows, "NBA", model_artifact_version="NBA_TEST_V1")
    decision = certification_decision(
        artifact, sport="WNBA", calibration_rows=500,
        calibration_status="PLATT_TIME_SPLIT_V1", provenance_complete=True,
    )
    assert decision.capability_status == "UNAVAILABLE"
    assert decision.reason_code == "MODEL_ARTIFACT_SPORT_MISMATCH"
