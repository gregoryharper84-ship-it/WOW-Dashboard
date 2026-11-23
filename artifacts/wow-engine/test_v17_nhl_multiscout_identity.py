"""Official NHL Multiscout identity mapping regressions."""
from __future__ import annotations

import json

from v17.nhl_multiscout_identity import resolve_multiscout_nhl_identity


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        # Identity is matched from these bytes and their SHA-256 is recorded, so
        # a schedule fixture has to provide the body, not only a parsed payload.
        self.content = json.dumps(payload).encode()

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = _Response(self.payload)
        response.status_code = self.status_code
        return response


def _nhl_row(**overrides):
    row = {
        "sport_key": "icehockey_nhl",
        "official_event_id": "espn-401999999",
        "provider_event_id": "espn-401999999",
        "provider_event_source": "ODDS_API",
        "official_event_identity_verified": False,
        "canonical_event_id": None,
        "canonical_identity_status": "PROVIDER_EVENT_ID_UNVERIFIED",
        "commence_time": "2026-11-24T00:30:00Z",
        "home_team": "New York Islanders",
        "away_team": "Toronto Maple Leafs",
    }
    row.update(overrides)
    return row


def _schedule_payload():
    return {
        "games": [
            {
                "id": 2026020381,
                "season": 20262027,
                "gameType": 2,
                "gameState": "FUT",
                "startTimeUTC": "2026-11-24T00:30:00Z",
                "homeTeam": {"abbrev": "NYI"},
                "awayTeam": {"abbrev": "TOR"},
            }
        ]
    }


def test_provider_alias_maps_to_distinct_official_nhl_event_id():
    session = _Session(_schedule_payload())
    # Provider commence differs within the match window; official start wins.
    out = resolve_multiscout_nhl_identity(
        _nhl_row(commence_time="2026-11-24T00:45:00Z"),
        session=session,
    )
    assert out["provider_event_id"] == "espn-401999999"
    assert out["provider_event_source"] == "ODDS_API"
    assert out["official_event_id"] == "2026020381"
    assert out["canonical_event_id"] == "2026020381"
    assert out["official_event_identity_verified"] is True
    assert out["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out["canonical_identity_source"] == "NHL_PUBLIC_WEB_API"
    assert out["provider_commence_time"] == "2026-11-24T00:45:00Z"
    assert out["commence_time"] == "2026-11-24T00:30:00+00:00"
    assert out["event_start_utc"] == "2026-11-24T00:30:00+00:00"
    assert out["canonical_identity_event_start_utc"] == "2026-11-24T00:30:00+00:00"
    assert out["canonical_identity_receipt"]["event_id"] == "2026020381"
    assert out["canonical_identity_receipt"]["can_execute"] is False
    assert out["can_execute"] is False
    assert "/club-schedule-season/NYI/" in session.calls[0][0]


def test_forged_or_unmatched_event_never_verifies():
    session = _Session(_schedule_payload())
    out = resolve_multiscout_nhl_identity(
        _nhl_row(home_team="Boston Bruins", away_team="Buffalo Sabres"),
        session=session,
    )
    assert out["official_event_identity_verified"] is False
    assert out["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out["canonical_identity_blocker"] == "NHL_CANONICAL_EVENT_NOT_FOUND"
    assert out["official_event_id"] == "espn-401999999"
    assert out["can_execute"] is False


def test_non_nhl_rows_are_untouched():
    row = _nhl_row(sport_key="basketball_nba")
    out = resolve_multiscout_nhl_identity(row, session=_Session(_schedule_payload()))
    assert out == row
