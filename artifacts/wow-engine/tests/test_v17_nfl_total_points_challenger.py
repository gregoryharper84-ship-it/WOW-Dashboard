from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.nfl_total_points_challenger import (
    CAN_EXECUTE,
    NFLTotalPointsChallengerUnavailable,
    artifact_from_json,
    artifact_to_json,
    build_rows,
    score_tiebreaker,
    train_candidate,
)


TEAMS = ("ATL", "NO", "DAL", "PHI", "BUF", "MIA", "KC", "LV")


def _events() -> list[dict]:
    events: list[dict] = []
    base = datetime(2020, 9, 1, tzinfo=timezone.utc)
    for season in range(2020, 2027):
        for week in range(1, 13):
            for game_index in range(4):
                home = TEAMS[(2 * game_index + week + season) % len(TEAMS)]
                away = TEAMS[(2 * game_index + week + season + 1) % len(TEAMS)]
                home_score = 17 + ((season + week + game_index * 3) % 20)
                away_score = 14 + ((season * 2 + week * 3 + game_index) % 21)
                start = base.replace(year=season) + timedelta(days=(week - 1) * 7, hours=game_index)
                events.append(
                    {
                        "event_id": f"NFL:{season}_{week}_{away}_{home}",
                        "event_start_time": start.isoformat(),
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_team": home,
                        "away_team": away,
                        "home_score": home_score,
                        "away_score": away_score,
                        "home_attack_style_index": 0.01 * ((week + game_index) % 8),
                        "away_attack_style_index": 0.01 * ((week + game_index + 2) % 8),
                        "home_defense_style_index": 0.01 * ((week + game_index + 1) % 6),
                        "away_defense_style_index": 0.01 * ((week + game_index + 3) % 6),
                        "home_pace_or_tempo_index": 0.85 + 0.01 * (week % 7),
                        "away_pace_or_tempo_index": 0.84 + 0.01 * ((week + 2) % 7),
                        "home_turnovers": float((week + game_index) % 3),
                        "away_turnovers": float((week + game_index + 1) % 3),
                        "home_special_teams_epa": float((game_index - 1) / 5.0),
                        "away_special_teams_epa": float((1 - game_index) / 5.0),
                        "home_history_lineup_ids": [f"QB-{home}-{season}"],
                        "away_history_lineup_ids": [f"QB-{away}-{season}"],
                        "source_manifest": {"source": "SYNTHETIC_TEST"},
                        # Noise fields that must never affect the sporting model.
                        "sportsbook_total": 99.5,
                        "moneyline_probability": 0.99,
                        "spread_line": -42.0,
                    }
                )
    return sorted(events, key=lambda r: (r["event_start_time"], r["event_id"]))


def test_training_is_holdout_governed_and_non_executable():
    artifact, receipt = train_candidate(
        _events(),
        min_train_n=20,
        min_calibration_n=10,
        min_validation_n=10,
    )
    assert CAN_EXECUTE is False
    assert artifact.can_execute is False
    assert artifact.sport == "NFL"
    assert artifact.use_case == "PICKEM_TIEBREAKER_ONLY"
    assert receipt["status"] == "EXPERIMENT_CREATED"
    assert receipt["automatic_certification"] is False
    assert receipt["automatic_promotion"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["rank_eligible"] is False
    assert receipt["sportsbook_total_used"] is False
    assert receipt["market_probability_substitution_used"] is False
    assert receipt["moneyline_probability_used"] is False
    assert receipt["validation_rows"] >= 10
    assert receipt["calibration_rows"] >= 10
    assert len(receipt["worst_validation_errors"]) == 10


def test_sportsbook_noise_does_not_change_rows_or_artifact():
    events = _events()
    clean = [dict(row) for row in events]
    noisy = [dict(row) for row in events]
    for row in clean:
        row.pop("sportsbook_total", None)
        row.pop("moneyline_probability", None)
        row.pop("spread_line", None)
    for row in noisy:
        row["sportsbook_total"] = -1000.0
        row["moneyline_probability"] = 0.01
        row["spread_line"] = 100.0
    clean_rows, _ = build_rows(clean)
    noisy_rows, _ = build_rows(noisy)
    assert [r.features for r in clean_rows] == [r.features for r in noisy_rows]
    a1, _ = train_candidate(clean, min_train_n=20, min_calibration_n=10, min_validation_n=10)
    a2, _ = train_candidate(noisy, min_train_n=20, min_calibration_n=10, min_validation_n=10)
    assert a1.coefficients == pytest.approx(a2.coefficients)
    assert a1.intercept == pytest.approx(a2.intercept)


def test_artifact_roundtrip_and_future_tiebreaker_score():
    events = _events()
    artifact, _ = train_candidate(events, min_train_n=20, min_calibration_n=10, min_validation_n=10)
    restored = artifact_from_json(artifact_to_json(artifact))
    historical = [row for row in events if int(row["season"]) <= 2025]
    scored = score_tiebreaker(
        restored,
        historical_events=historical,
        event_id="NFL:2026_4_ATL_NO",
        event_start_time="2026-09-28T00:00:00+00:00",
        season=2026,
        week=4,
        home_team="NO",
        away_team="ATL",
    )
    assert scored["status"] == "MODEL_PROJECTED_HOLD"
    assert scored["projected_total_points"] > 0
    assert scored["prediction_interval"]["lower"] < scored["prediction_interval"]["upper"]
    assert scored["sportsbook_total_used"] is False
    assert scored["market_probability_used"] is False
    assert scored["moneyline_probability_used"] is False
    assert scored["probability_publishable"] is False
    assert scored["rank_eligible"] is False
    assert scored["can_execute"] is False


def test_incomplete_holdout_fails_closed():
    with pytest.raises(NFLTotalPointsChallengerUnavailable) as exc:
        train_candidate(_events()[:30])
    assert exc.value.code == "NFL_TOTAL_POINTS_SEASON_HOLDOUT_INSUFFICIENT"
