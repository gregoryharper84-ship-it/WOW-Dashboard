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


@pytest.mark.parametrize(
    "display_home,cfbd_home",
    [
        ("BYU Cougars", "Brigham Young"),
        ("Brigham Young Cougars", "BYU"),
        ("Iowa State Cyclones", "Iowa St."),
    ],
)
def test_explicit_school_aliases_require_exact_opponent_and_start(display_home, cfbd_home):
    client = _Client([{
        "id": 401999999,
        "startDate": "2026-10-10T02:15:00Z",
        "homeTeam": cfbd_home,
        "awayTeam": "Washington State",
    }])
    out = identity.resolve_ncaaf_current_event_identity(
        event_start_time="2026-10-10T02:15:00Z",
        home_team=display_home,
        away_team="Washington State Cougars",
        client=client,
    )
    assert out["event_id"] == "401999999"
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False

    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T04:00:00Z",
            home_team=display_home,
            away_team="Washington State Cougars",
            client=client,
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


def test_school_alias_does_not_conflate_iowa_and_iowa_state():
    client = _Client([{
        "id": 401999999,
        "startDate": "2026-10-10T02:15:00Z",
        "homeTeam": "Iowa State",
        "awayTeam": "Washington",
    }])
    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T02:15:00Z",
            home_team="Iowa Hawkeyes",
            away_team="Washington Huskies",
            client=client,
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


@pytest.mark.parametrize("false_alias", ["Iowa Starlings", "BYU County"])
def test_alias_prefix_without_school_boundary_does_not_match(false_alias):
    rows = [{
        "id": 401999998,
        "startDate": "2026-10-10T02:15:00Z",
        "homeTeam": "Iowa State" if false_alias.startswith("Iowa") else "Brigham Young",
        "awayTeam": "Washington",
    }]
    with pytest.raises(identity.NCAAFEventIdentityError) as exc:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T02:15:00Z",
            home_team=false_alias,
            away_team="Washington Huskies",
            client=_Client(rows),
        )
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


def test_short_iowa_canonical_name_resolves_without_matching_iowa_state():
    client = _Client([
        {
            "id": 401999997,
            "startDate": "2026-10-10T01:00:00Z",
            "homeTeam": "Washington",
            "awayTeam": "Iowa",
        },
        {
            "id": 401999996,
            "startDate": "2026-10-10T01:00:00Z",
            "homeTeam": "Washington",
            "awayTeam": "Iowa State",
        },
    ])
    out = identity.resolve_ncaaf_current_event_identity(
        event_start_time="2026-10-10T01:00:00Z",
        home_team="Washington Huskies",
        away_team="Iowa Hawkeyes",
        client=client,
    )
    assert out["event_id"] == "401999997"
    assert out["can_execute"] is False



@pytest.mark.parametrize(
    "short_school,distinct_school",
    [
        ("Michigan", "Michigan State"),
        ("Florida", "Florida State"),
        ("Georgia", "Georgia Southern"),
        ("Washington", "Washington State"),
        ("Texas", "Texas A&M"),
        ("Ohio", "Ohio State"),
        ("Virginia", "Virginia Tech"),
        ("Florida", "Florida Atlantic"),
        ("Louisiana", "Louisiana Tech"),
        ("Alabama", "Alabama State"),
    ],
)
def test_distinct_university_prefix_never_becomes_canonical_event(short_school, distinct_school):
    # Identical opponent and kickoff MUST NOT erase distinct school identity.
    client = _Client([{
        "id": 401999987,
        "startDate": "2026-10-10T18:00:00Z",
        "homeTeam": distinct_school,
        "awayTeam": "Oklahoma",
    }])
    with pytest.raises(identity.NCAAFEventIdentityError) as err:
        identity.resolve_ncaaf_current_event_identity(
            event_start_time="2026-10-10T18:00:00Z",
            home_team=short_school,
            away_team="Oklahoma Sooners",
            client=client,
        )
    assert err.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


def test_actual_school_mascot_suffix_remains_supported_after_collision_guard():
    client = _Client([{
        "id": 401999986,
        "startDate": "2026-10-10T18:00:00Z",
        "homeTeam": "Michigan State",
        "awayTeam": "Oklahoma",
    }])
    result = identity.resolve_ncaaf_current_event_identity(
        event_start_time="2026-10-10T18:00:00Z",
        home_team="Michigan State Spartans",
        away_team="Oklahoma Sooners",
        client=client,
    )
    assert result["event_id"] == "401999986"
    assert result["can_execute"] is False
