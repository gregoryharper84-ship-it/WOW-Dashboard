from __future__ import annotations

import pytest

import v17.spread_forward_auto_canary as canary
import v17.wnba_prop_evidence_control_plane as control
from v17.wnba_spread_event_identity import resolve_wnba_current_event_identity
from v17.wnba_team_identity_aliases import (
    ESPN_WNBA_TEAM_ID_BY_ABBREVIATION,
    WNBA_STATS_TO_ESPN_ABBREVIATION,
    espn_team_id_for_wnba_stats_tricode,
)


def test_alias_registry_covers_current_2026_wnba_teams_without_duplicate_espn_ids():
    assert len(WNBA_STATS_TO_ESPN_ABBREVIATION) == 15
    assert len(ESPN_WNBA_TEAM_ID_BY_ABBREVIATION) == 15
    assert len(set(ESPN_WNBA_TEAM_ID_BY_ABBREVIATION.values())) == 15
    assert espn_team_id_for_wnba_stats_tricode("GSV") == "espn-129689"
    assert espn_team_id_for_wnba_stats_tricode("PDX") == "espn-132052"
    assert espn_team_id_for_wnba_stats_tricode("TOR") == "espn-131935"
    assert espn_team_id_for_wnba_stats_tricode("WAS") == "espn-16"


def test_espn_forbidden_circuits_immediately_to_stats_identity(monkeypatch):
    calls = []

    class Forbidden:
        status_code = 403

        def json(self):
            return {}

    def fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        return Forbidden()

    monkeypatch.setattr(
        canary,
        "discover_future_wnba_stats_event",
        lambda **_k: {
            "sport": "WNBA",
            "raw_event_id": "1042600122",
            "event_start_time": "2026-10-04T20:00:00+00:00",
            "home_team": "Minnesota Lynx",
            "away_team": "Washington Mystics",
            "home_team_id": "espn-8",
            "away_team_id": "espn-16",
            "home_team_tricode": "MIN",
            "away_team_tricode": "WAS",
            "identity_provider": "WNBA_STATS_SCOREBOARD_V3",
            "identity_acquisition_location": "BACKEND_RUNTIME",
            "can_execute": False,
        },
    )
    seen = {}

    def shadow(db, **kwargs):
        seen.update(kwargs)
        return {
            "status": "EXPERIMENT_CREATED",
            "code": "WNBA_SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "WNBA",
            "p_cover": 0.5,
            "p_push": 0.0,
            "p_not_cover": 0.5,
        }

    monkeypatch.setattr(canary, "run_wnba_forward_shadow", shadow)
    result = canary.run_wnba_spread_auto_canary(object(), fetcher=fetcher)
    assert len(calls) == 1
    assert seen["event_id"] == "wnba-stats-1042600122"
    assert seen["home_team_id"] == "espn-8"
    assert seen["away_team_id"] == "espn-16"
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_stats_identity_reverification_preserves_espn_keyed_training_ids(monkeypatch):
    monkeypatch.setattr(
        control,
        "_scoreboard_schedule_for_date",
        lambda *_a, **_k: {
            "leagueSchedule": {
                "gameDates": [
                    {
                        "games": [
                            {
                                "gameId": "1042600122",
                                "gameDateTimeUTC": "2026-10-04T20:00:00Z",
                                "gameStatus": 1,
                                "homeTeam": {
                                    "teamTricode": "MIN",
                                    "teamCity": "Minnesota",
                                    "teamName": "Lynx",
                                },
                                "awayTeam": {
                                    "teamTricode": "WAS",
                                    "teamCity": "Washington",
                                    "teamName": "Mystics",
                                },
                            }
                        ]
                    }
                ]
            }
        },
    )
    result = resolve_wnba_current_event_identity(
        event_id="wnba-stats-1042600122",
        event_start_time="2026-10-04T20:00:00+00:00",
        home_team_id="espn-8",
        away_team_id="espn-16",
        fetcher=lambda *_a, **_k: None,
    )
    assert result["event_id"] == "wnba-stats-1042600122"
    assert result["home_team_id"] == "espn-8"
    assert result["away_team_id"] == "espn-16"
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["market_features_used"] is False
    assert result["can_execute"] is False


def test_stats_identity_unmapped_team_fails_closed(monkeypatch):
    monkeypatch.setattr(
        control,
        "_scoreboard_schedule_for_date",
        lambda *_a, **_k: {
            "leagueSchedule": {
                "gameDates": [
                    {
                        "games": [
                            {
                                "gameId": "1042600122",
                                "gameDateTimeUTC": "2026-10-04T20:00:00Z",
                                "gameStatus": 1,
                                "homeTeam": {"teamTricode": "XXX"},
                                "awayTeam": {"teamTricode": "WAS"},
                            }
                        ]
                    }
                ]
            }
        },
    )
    with pytest.raises(Exception) as exc:
        resolve_wnba_current_event_identity(
            event_id="wnba-stats-1042600122",
            event_start_time="2026-10-04T20:00:00+00:00",
            home_team_id="espn-8",
            away_team_id="espn-16",
            fetcher=lambda *_a, **_k: None,
        )
    assert getattr(exc.value, "code", "") == "WNBA_SPREAD_FORWARD_TEAM_IDENTITY_UNMAPPED"
