from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.nfl_total_points_tiebreaker_specialist import (
    CAN_EXECUTE,
    MODEL_ARTIFACT_VERSION,
    NFLTotalPointsTiebreakerError,
    capability,
    score_tiebreaker,
)


def _history() -> list[dict]:
    rows: list[dict] = []
    teams = ("ATL", "NO", "CAR", "TB")
    for season in (2025, 2026):
        weeks = range(1, 18) if season == 2025 else range(1, 4)
        start = datetime(season, 9, 1, tzinfo=timezone.utc)
        for week in weeks:
            # Rotate opponents so ATL/NO each retain a normal regular-season history.
            games = (("ATL", "CAR"), ("NO", "TB")) if week % 2 else (("CAR", "ATL"), ("TB", "NO"))
            for idx, (home, away) in enumerate(games):
                home_score = 17 + ((week + idx + season) % 18)
                away_score = 14 + ((week * 2 + idx + season) % 17)
                rows.append(
                    {
                        "event_id": f"{season}_{week}_{away}_{home}",
                        "event_start_time": (start + timedelta(days=(week - 1) * 7, hours=idx)).isoformat(),
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_team": home,
                        "away_team": away,
                        "home_score": home_score,
                        "away_score": away_score,
                        "sportsbook_total": 999.5,
                        "spread_line": -99.0,
                        "moneyline_probability": 0.999,
                    }
                )
    return rows


def _score(history: list[dict]):
    return score_tiebreaker(
        history,
        event_id="2026_4_ATL_NO",
        event_start_time="2026-10-05T00:15:00+00:00",
        season=2026,
        week=4,
        home_team="NO",
        away_team="ATL",
    )


def test_specialist_is_tiebreaker_only_and_non_executable():
    result = _score(_history())
    assert CAN_EXECUTE is False
    assert result["status"] == "TIEBREAKER_MODEL_QUALIFIED"
    assert result["model_artifact_version"] == MODEL_ARTIFACT_VERSION
    assert result["use_case"] == "PICKEM_TIEBREAKER_ONLY"
    assert result["tiebreaker_publishable"] is True
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert result["sportsbook_total_used"] is False
    assert result["market_probability_used"] is False
    assert result["generic_llm_projection_used"] is False
    assert result["prediction_interval"]["lower"] < result["projected_total_points"] < result["prediction_interval"]["upper"]
    assert result["suggested_integer_tiebreaker"] == round(result["projected_total_points"])


def test_market_noise_cannot_change_projection():
    left = _history()
    right = [dict(row) for row in left]
    for row in right:
        row["sportsbook_total"] = -10000.0
        row["spread_line"] = 10000.0
        row["moneyline_probability"] = 0.001
    a = _score(left)
    b = _score(right)
    assert a["projected_total_points"] == pytest.approx(b["projected_total_points"])
    assert a["projected_home_points"] == pytest.approx(b["projected_home_points"])
    assert a["projected_away_points"] == pytest.approx(b["projected_away_points"])


def test_future_or_postseason_rows_cannot_leak_into_projection():
    history = _history()
    baseline = _score(history)
    history.extend(
        [
            {
                "event_id": "future",
                "event_start_time": "2026-10-06T00:00:00+00:00",
                "season": 2026,
                "week": 5,
                "game_type": "REG",
                "home_team": "ATL",
                "away_team": "NO",
                "home_score": 100,
                "away_score": 100,
            },
            {
                "event_id": "postseason",
                "event_start_time": "2025-12-01T00:00:00+00:00",
                "season": 2025,
                "week": 19,
                "game_type": "WC",
                "home_team": "ATL",
                "away_team": "NO",
                "home_score": 100,
                "away_score": 100,
            },
        ]
    )
    after = _score(history)
    assert after["projected_total_points"] == pytest.approx(baseline["projected_total_points"])


def test_artifact_is_season_scoped_and_fails_closed():
    with pytest.raises(NFLTotalPointsTiebreakerError) as exc:
        score_tiebreaker(
            _history(),
            event_id="2027_1_ATL_NO",
            event_start_time="2027-09-01T00:00:00+00:00",
            season=2027,
            week=1,
            home_team="NO",
            away_team="ATL",
        )
    assert exc.value.code == "NFL_TOTAL_POINTS_ARTIFACT_SEASON_UNSUPPORTED"


def test_missing_prior_season_history_is_typed_input_failure():
    only_current = [row for row in _history() if row["season"] == 2026]
    with pytest.raises(NFLTotalPointsTiebreakerError) as exc:
        _score(only_current)
    assert exc.value.code == "MODEL_INPUTS_INSUFFICIENT"


def test_capability_is_explicitly_non_betting():
    status = capability()
    assert status["status"] == "AVAILABLE"
    assert status["use_case"] == "PICKEM_TIEBREAKER_ONLY"
    assert status["sportsbook_total_substitution_allowed"] is False
    assert status["moneyline_probability_reuse_allowed"] is False
    assert status["probability_publishable"] is False
    assert status["rank_eligible"] is False
    assert status["can_execute"] is False
