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


def test_oct_9_iowa_hawkeyes_matches_only_cfbd_iowa_not_iowa_state():
    # The 2026-10-09 held Iowa @ Washington event had no canonical ID
    # because the old five-character prefix floor excluded "Iowa".
    event_at = "2026-10-10T01:00:00Z"
    source = _Client([{
        "id": 401900001,
        "startDate": event_at,
        "homeTeam": "Washington",
        "awayTeam": "Iowa",
    }, {
        "id": 401900002,
        "startDate": event_at,
        "homeTeam": "Washington",
        "awayTeam": "Iowa State",
    }])
    result = identity.resolve_ncaaf_current_event_identity(
        event_start_time=event_at,
        home_team="Washington Huskies",
        away_team="Iowa Hawkeyes",
        client=source,
    )
    assert result["event_id"] == "401900001"
    assert result["market_features_used"] is False
    assert result["can_execute"] is False


def test_oct_9_byu_cougars_short_school_alias_is_exact():
    # The 2026-10-09 Iowa State @ BYU hold likewise failed at length 3.
    event_at = "2026-10-10T02:15:00Z"
    result = identity.resolve_ncaaf_current_event_identity(
        event_start_time=event_at,
        home_team="BYU Cougars",
        away_team="Iowa State Cyclones",
        client=_Client([{
            "id": 401900003,
            "startDate": event_at,
            "homeTeam": "BYU",
            "awayTeam": "Iowa State",
        }]),
    )
    assert result["event_id"] == "401900003"
    assert result["prediction_authority"] is False


@pytest.mark.parametrize(("alias", "canonical"), [
    ("Iowa State Cyclones", "Iowa"),
    ("Iowa Hawkeyes", "Iowa State"),
    ("BYU Bobcats", "BYU"),
    ("UCF Knights", "UCF"),
    ("", "Iowa"),
])
def test_short_school_alias_does_not_accept_unsafe_prefixes(alias, canonical):
    assert identity._name_match(alias, canonical) is False


def test_known_short_aliases_do_not_bypass_unique_event_gate():
    event_at = "2026-10-10T01:00:00Z"
    games = [
        {"id": i, "startDate": event_at,
         "homeTeam": "Washington", "awayTeam": "Iowa"}
        for i in (401900004, 401900005)
    ]
    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time=event_at,
            home_team="Washington Huskies",
            away_team="Iowa Hawkeyes",
            client=_Client(games),
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_AMBIGUOUS"


def test_short_school_mascot_identity_does_not_bypass_start_tolerance():
    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T01:00:00Z",
            home_team="Washington Huskies",
            away_team="Iowa Hawkeyes",
            client=_Client([{
                "id": 401900006,
                "startDate": "2026-10-10T04:00:00Z",
                "homeTeam": "Washington",
                "awayTeam": "Iowa",
            }]),
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


def test_production_cross_sport_handoff_assigns_verified_short_school_id(monkeypatch):
    from datetime import datetime, timezone
    from v17.cross_sport_winner_discovery import normalize_discovered_event
    from v17.team_event_sport_parity import canonicalize_ncaaf_discovery_identity

    utc_start = "2026-10-10T01:00:00Z"
    monkeypatch.setattr(identity, "_season_rows", lambda year, **_kwargs: [{
        "id": 401900007, "startDate": utc_start,
        "homeTeam": "Washington", "awayTeam": "Iowa",
    }])
    provider = normalize_discovered_event(
        {
            "provider_event_id": "espn-synthetic-alias",
            "start_time": utc_start,
            "home_team": "Washington Huskies",
            "away_team": "Iowa Hawkeyes",
            "status": "scheduled",
        },
        sport="NCAAF", sport_key="football_ncaaf", source="DISCOVERY_FEED",
        now=datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
    )
    assert provider.official_event_id is None
    resolved = canonicalize_ncaaf_discovery_identity(provider)
    assert resolved.official_event_id == "401900007"
    assert resolved.raw["provider_event_id"] == "espn-synthetic-alias"
    assert resolved.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert resolved.raw["canonical_identity_market_features_used"] is False


def test_production_cross_sport_handoff_keeps_unsafe_short_alias_held(monkeypatch):
    from datetime import datetime, timezone
    from v17.cross_sport_winner_discovery import normalize_discovered_event
    from v17.team_event_sport_parity import canonicalize_ncaaf_discovery_identity

    utc_start = "2026-10-10T01:00:00Z"
    monkeypatch.setattr(identity, "_season_rows", lambda year, **_kwargs: [{
        "id": 401900008, "startDate": utc_start,
        "homeTeam": "Washington", "awayTeam": "Iowa State",
    }])
    provider = normalize_discovered_event(
        {
            "provider_event_id": "espn-another-synthetic-alias",
            "start_time": utc_start,
            "home_team": "Washington Huskies",
            "away_team": "Iowa Hawkeyes",
            "status": "scheduled",
        },
        sport="NCAAF", sport_key="football_ncaaf", source="DISCOVERY_FEED",
        now=datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
    )
    resolved = canonicalize_ncaaf_discovery_identity(provider)
    assert resolved.official_event_id is None
    assert resolved.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert resolved.raw["canonical_identity_blocker"] == "NCAAF_CANONICAL_EVENT_NOT_FOUND"
