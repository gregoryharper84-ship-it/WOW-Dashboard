from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import ncaaf_event_identity as identity


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def games(self, *, year, classification=None):
        self.calls.append((year, classification))
        return SimpleNamespace(rows=self.rows)


def test_resolves_espn_display_names_to_existing_cfbd_canonical_id():
    client = _Client([{
        "id": 401856817,
        "startDate": "2026-10-04T02:30:00Z",
        "homeTeam": "Arizona State",
        "awayTeam": "Baylor",
    }])
    out = identity.resolve_ncaaf_current_event_identity(
        event_start_time="2026-10-04T02:30:00Z",
        home_team="Arizona State Sun Devils",
        away_team="Baylor Bears",
        client=client,
    )
    assert out["event_id"] == "401856817"
    assert out["identity_provider"] == "CFBD:/games"
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False
    assert client.calls == [(2026, "fbs")]


def test_accent_normalization_is_identity_only():
    client = _Client([{
        "id": 401864513,
        "startDate": "2026-10-04T03:59:00Z",
        "homeTeam": "Hawai'i",
        "awayTeam": "San José State",
    }])
    out = identity.resolve_ncaaf_current_event_identity(
        event_start_time="2026-10-04T03:59:00Z",
        home_team="Hawai'i Rainbow Warriors",
        away_team="San Jose State Spartans",
        client=client,
    )
    assert out["event_id"] == "401864513"


def test_no_match_fails_closed():
    client = _Client([])
    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T18:00:00Z",
            home_team="Texas",
            away_team="Oklahoma",
            client=client,
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


def test_multiple_matches_fail_ambiguous():
    rows = [
        {
            "id": 1,
            "startDate": "2026-10-10T18:00:00Z",
            "homeTeam": "Texas",
            "awayTeam": "Oklahoma",
        },
        {
            "id": 2,
            "startDate": "2026-10-10T18:30:00Z",
            "homeTeam": "Texas",
            "awayTeam": "Oklahoma",
        },
    ]
    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T18:15:00Z",
            home_team="Texas Longhorns",
            away_team="Oklahoma Sooners",
            client=_Client(rows),
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_AMBIGUOUS"
