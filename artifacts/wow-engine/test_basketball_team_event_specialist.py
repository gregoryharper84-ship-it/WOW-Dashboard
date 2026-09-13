from datetime import date, timedelta

import pytest

from basketball_team_event_specialist import (
    BasketballSpecialistError,
    TrainingGame,
    build_pregame_features,
    certification_decision,
    train_specialist,
)


def _games(sport: str, n: int = 360):
    teams = [f"{sport}-T{i}" for i in range(12)]
    start = date(2024, 1, 1) if sport == "NBA" else date(2024, 5, 1)
    out = []
    for i in range(n):
        home = teams[i % len(teams)]
        away = teams[(i * 5 + 3) % len(teams)]
        if home == away:
            away = teams[(teams.index(away) + 1) % len(teams)]
        # Deterministic non-market score process with home-court signal and changing team strength.
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
    assert {r.game_id for r in nba_rows} == {r.game_id for r in wnba_rows}  # ids may overlap; sport keeps them isolated


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
