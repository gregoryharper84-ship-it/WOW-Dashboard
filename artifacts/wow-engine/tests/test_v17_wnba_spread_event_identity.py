from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from v17.spread_margin_challenger import SpreadChallengerUnavailable
from v17.wnba_spread_event_identity import resolve_wnba_current_event_identity


class _Resp:
    status_code = 200
    def __init__(self, event): self.event = event
    def json(self): return {"events": [self.event]}


def _event(start):
    return {
        "id": "401999999",
        "date": start,
        "status": {"type": {"completed": False, "state": "pre", "name": "STATUS_SCHEDULED"}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "11"}},
            {"homeAway": "away", "team": {"id": "17"}},
        ]}],
    }


class _OfficialResp:
    status_code = 200
    def __init__(self, target, *, home="PHX", away="LVA", game_id="1042600999"):
        self.target = target
        self.home = home
        self.away = away
        self.game_id = game_id
    def json(self):
        return {
            "leagueSchedule": {
                "gameDates": [{
                    "games": [{
                        "gameId": self.game_id,
                        "gameDateTimeUTC": self.target,
                        "gameStatus": 1,
                        "homeTeam": {"teamId": "1611661317", "teamTricode": self.home},
                        "awayTeam": {"teamId": "1611661319", "teamTricode": self.away},
                    }]
                }]
            }
        }


def test_resolver_verifies_exact_event_team_and_time_identity():
    target = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    event = _event(target.isoformat().replace("+00:00", "Z"))
    seen = {}
    def fetcher(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return _Resp(event)
    result = resolve_wnba_current_event_identity(
        event_id="espn-401999999", event_start_time=target.isoformat(),
        home_team_id="espn-11", away_team_id="espn-17", fetcher=fetcher,
        official_fetcher=lambda *_a, **_k: _OfficialResp(
            target.isoformat().replace("+00:00", "Z")
        ),
    )
    assert result["event_id"] == "wnba-stats-1042600999"
    assert result["provider_event_alias"] == "espn-401999999"
    assert result["home_team_id"] == "espn-11"
    assert result["away_team_id"] == "espn-17"
    assert result["identity_provider"] == "WNBA_OFFICIAL_SCHEDULE_API"
    assert result["identity_alias_provider"] == "ESPN_SCOREBOARD"
    assert result["market_features_used"] is False
    assert result["can_execute"] is False
    assert seen["params"]["limit"] == 100


def test_resolver_fails_closed_on_team_mismatch():
    target = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    event = _event(target.isoformat().replace("+00:00", "Z"))
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        resolve_wnba_current_event_identity(
            event_id="espn-401999999", event_start_time=target.isoformat(),
            home_team_id="espn-6", away_team_id="espn-17", fetcher=lambda *_a, **_k: _Resp(event),
        )
    assert exc.value.code == "WNBA_SPREAD_FORWARD_TEAM_IDENTITY_MISMATCH"


def test_resolver_fails_closed_when_espn_alias_has_no_official_wnba_match():
    target = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    event = _event(target.isoformat().replace("+00:00", "Z"))

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        resolve_wnba_current_event_identity(
            event_id="espn-401999999",
            event_start_time=target.isoformat(),
            home_team_id="espn-11",
            away_team_id="espn-17",
            fetcher=lambda *_a, **_k: _Resp(event),
            official_fetcher=lambda *_a, **_k: _OfficialResp(
                target.isoformat().replace("+00:00", "Z"),
                home="NYL",
                away="LVA",
            ),
        )
    assert exc.value.code == "WNBA_SPREAD_FORWARD_CANONICAL_EVENT_NOT_FOUND"
