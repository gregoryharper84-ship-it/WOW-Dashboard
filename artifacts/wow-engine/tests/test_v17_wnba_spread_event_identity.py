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
    )
    assert result["event_id"] == "espn-401999999"
    assert result["home_team_id"] == "espn-11"
    assert result["away_team_id"] == "espn-17"
    assert result["identity_provider"] == "ESPN_SCOREBOARD"
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
