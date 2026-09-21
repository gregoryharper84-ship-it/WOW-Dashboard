from __future__ import annotations

from datetime import datetime, timezone

import nfl_prop_auto_hydration as nfl


class _Response:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def _event() -> dict:
    return {
        "id": "401872945",
        "date": "2026-09-21T00:20:00Z",
        "season": {"year": 2026},
        "week": {"number": 2},
        "status": {"type": {"state": "pre", "completed": False}},
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": "KC"}},
                    {"homeAway": "away", "team": {"abbreviation": "IND"}},
                ]
            }
        ],
    }


def test_target_event_finds_sunday_night_game_on_prior_espn_slate_date() -> None:
    requested_dates: list[str] = []

    def http_get(url, *, params, headers, timeout, follow_redirects):
        del url, headers, timeout, follow_redirects
        date_key = str(params["dates"])
        requested_dates.append(date_key)
        # Colts-Chiefs is a Sep 20 local slate game but 00:20 UTC Sep 21.
        payload = {"events": [_event()]} if date_key == "20260920" else {"events": []}
        return _Response(payload)

    result = nfl._target_event(
        event_start=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc),
        team="KC",
        opponent="Indianapolis Colts",
        http_get=http_get,
    )

    assert requested_dates == ["20260920", "20260921", "20260922"]
    assert result == {
        "event_id": "401872945",
        "team": "KC",
        "opponent": "IND",
        "provider_season": 2026,
        "provider_week": 2,
        "provider_home_team": "KC",
        "provider_away_team": "IND",
        "canonical_home_team": "KC",
        "canonical_away_team": "IND",
        "verified_canonical_event_id": "2026_02_IND_KC",
    }


def test_target_event_dedupes_same_espn_alias_across_adjacent_dates() -> None:
    def http_get(url, *, params, headers, timeout, follow_redirects):
        del url, headers, timeout, follow_redirects
        # ESPN may surface the same event on adjacent scoreboard dates. It must
        # remain one provider alias, not create a false two-candidate conflict.
        return _Response({"events": [_event()]})

    result = nfl._target_event(
        event_start=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc),
        team="KC",
        opponent="IND",
        http_get=http_get,
    )

    assert result["event_id"] == "401872945"
    assert result["opponent"] == "IND"
    assert result["verified_canonical_event_id"] == "2026_02_IND_KC"
