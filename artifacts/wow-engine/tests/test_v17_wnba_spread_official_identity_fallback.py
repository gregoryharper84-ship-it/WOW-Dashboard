from __future__ import annotations

from datetime import datetime, timedelta, timezone

import v17.spread_forward_auto_canary as canary
import v17.wnba_spread_event_identity as identity


NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
TARGET = NOW + timedelta(days=1)


class _Resp:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _official_schedule(*, game_id="1022600180", start=None):
    start = start or TARGET.isoformat()
    return {
        "leagueSchedule": {
            "gameDates": [{
                "gameDate": TARGET.date().isoformat(),
                "games": [{
                    "gameId": game_id,
                    "gameDateTimeUTC": start,
                    "gameStatus": 1,
                    "homeTeam": {
                        "teamId": "1611661324",
                        "teamTricode": "MIN",
                        "teamCity": "Minnesota",
                        "teamName": "Lynx",
                    },
                    "awayTeam": {
                        "teamId": "1611661317",
                        "teamTricode": "PHX",
                        "teamCity": "Phoenix",
                        "teamName": "Mercury",
                    },
                }],
            }],
        },
    }


def test_official_discovery_maps_stats_identity_to_historical_espn_aliases(monkeypatch):
    monkeypatch.setattr(
        canary,
        "_official_schedule_for_date",
        lambda *_args, **_kwargs: _official_schedule(),
    )
    result = canary.discover_future_wnba_official_event(
        fetcher=lambda *_a, **_k: _Resp(200),
        now=NOW,
        horizon_days=2,
    )
    assert result is not None
    assert result["raw_event_id"] == "1022600180"
    assert result["home_team_id"] == "8"
    assert result["away_team_id"] == "11"
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["identity_acquisition_location"] == "BACKEND_RUNTIME"
    assert result["can_execute"] is False


def test_auto_canary_recovers_from_espn_403_via_official_scoreboard(monkeypatch):
    official = {
        "sport": "WNBA",
        "raw_event_id": "1022600180",
        "event_start_time": TARGET.isoformat(),
        "home_team": "Minnesota Lynx",
        "away_team": "Phoenix Mercury",
        "home_team_id": "8",
        "away_team_id": "11",
        "identity_provider": "WNBA_STATS_SCOREBOARD_V3",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        "can_execute": False,
    }
    monkeypatch.setattr(
        canary,
        "discover_future_espn_event",
        lambda *_a, **_k: (_ for _ in ()).throw(
            canary.SpreadChallengerUnavailable(
                "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE",
                "backend ESPN schedule acquisition failed: HTTP_403",
            )
        ),
    )
    monkeypatch.setattr(
        canary,
        "discover_future_wnba_official_event",
        lambda **_kwargs: official,
    )
    seen = {}

    def shadow(db, **kwargs):
        seen["db"] = db
        seen.update(kwargs)
        return {
            "status": "EXPERIMENT_CREATED",
            "code": "WNBA_SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "WNBA",
            "p_cover": 0.49,
            "p_push": 0.01,
            "p_not_cover": 0.50,
        }

    monkeypatch.setattr(canary, "run_wnba_forward_shadow", shadow)
    db = object()
    result = canary.run_wnba_spread_auto_canary(db)
    assert seen["db"] is db
    assert seen["event_id"] == "wnba-stats-1022600180"
    assert seen["home_team_id"] == "espn-8"
    assert seen["away_team_id"] == "espn-11"
    assert seen["home_spread"] == 0.0
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_identity_resolver_uses_official_schedule_when_espn_is_blocked(monkeypatch):
    monkeypatch.setattr(
        identity,
        "_official_schedule_for_date",
        lambda *_args, **_kwargs: _official_schedule(),
    )
    result = identity.resolve_wnba_current_event_identity(
        event_id="wnba-stats-1022600180",
        event_start_time=TARGET.isoformat(),
        home_team_id="espn-8",
        away_team_id="espn-11",
        fetcher=lambda *_a, **_k: _Resp(403),
    )
    assert result["event_id"] == "wnba-stats-1022600180"
    assert result["home_team_id"] == "espn-8"
    assert result["away_team_id"] == "espn-11"
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
    assert result["market_features_used"] is False
    assert result["can_execute"] is False


def test_legacy_espn_event_can_fallback_by_time_and_team_pair(monkeypatch):
    monkeypatch.setattr(
        identity,
        "_official_schedule_for_date",
        lambda *_args, **_kwargs: _official_schedule(),
    )
    result = identity.resolve_wnba_current_event_identity(
        event_id="espn-401999999",
        event_start_time=TARGET.isoformat(),
        home_team_id="espn-8",
        away_team_id="espn-11",
        fetcher=lambda *_a, **_k: _Resp(403),
    )
    assert result["event_id"] == "wnba-stats-1022600180"
    assert result["identity_provider"] == "WNBA_STATS_SCOREBOARD_V3"
