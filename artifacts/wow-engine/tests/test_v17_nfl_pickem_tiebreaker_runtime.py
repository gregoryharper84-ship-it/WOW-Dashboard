from __future__ import annotations

from datetime import datetime, timedelta, timezone

import v17.nfl_pickem_runtime as runtime


def _history():
    rows = []
    for season in (2025, 2026):
        weeks = range(1, 18) if season == 2025 else range(1, 4)
        base = datetime(season, 9, 1, tzinfo=timezone.utc)
        for week in weeks:
            for idx, (home, away) in enumerate((("ATL", "CAR"), ("NO", "TB"))):
                rows.append(
                    {
                        "event_id": f"{season}_{week}_{away}_{home}",
                        "event_start_time": (base + timedelta(days=(week - 1) * 7, hours=idx)).isoformat(),
                        "season": season,
                        "week": week,
                        "game_type": "REG",
                        "home_team": home,
                        "away_team": away,
                        "home_score": 20 + (week % 10),
                        "away_score": 17 + ((week + idx) % 9),
                    }
                )
    return rows


def _events():
    return [
        {
            "official_event_id": "2026_04_PIT_CLE",
            "gameday": "2026-10-01",
            "event_start_time_utc": "2026-10-02T00:15:00+00:00",
            "home_team": "CLE",
            "away_team": "PIT",
            "season": 2026,
            "week": 4,
        },
        {
            "official_event_id": "2026_04_ATL_NO",
            "gameday": "2026-10-05",
            "event_start_time_utc": "2026-10-06T00:15:00+00:00",
            "home_team": "NO",
            "away_team": "ATL",
            "season": 2026,
            "week": 4,
        },
    ]


def test_unique_latest_date_event_is_tiebreaker():
    event = runtime._resolve_tiebreaker_event(_events())
    assert event is not None
    assert event["official_event_id"] == "2026_04_ATL_NO"


def test_ambiguous_latest_date_fails_closed():
    events = _events()
    events.append({**events[-1], "official_event_id": "another"})
    assert runtime._resolve_tiebreaker_event(events) is None


def test_runtime_scores_tiebreaker_without_market_substitution(monkeypatch):
    monkeypatch.setattr(runtime, "_load_tiebreaker_history", lambda db: _history())
    result = runtime._score_tiebreaker(object(), _events())
    assert result["status"] == "TIEBREAKER_MODEL_QUALIFIED"
    assert result["event_id"] == "2026_04_ATL_NO"
    assert result["tiebreaker_publishable"] is True
    assert result["projected_total_points"] > 0
    assert result["sportsbook_total_used"] is False
    assert result["market_probability_used"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
