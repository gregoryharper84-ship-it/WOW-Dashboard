"""NHL city-only discovery aliases: exact-source identity regressions."""
from __future__ import annotations

import pytest

from v17.nhl_event_identity import (
    NHLEventIdentityError,
    resolve_nhl_current_event_identity,
    team_abbreviation,
)


@pytest.mark.parametrize("label,expected", [
    ("Anaheim", "ANA"), ("Boston", "BOS"), ("Buffalo", "BUF"),
    ("Calgary", "CGY"), ("Carolina", "CAR"), ("Chicago", "CHI"),
    ("Colorado", "COL"), ("Columbus", "CBJ"), ("Dallas", "DAL"),
    ("Detroit", "DET"), ("Edmonton", "EDM"), ("Florida", "FLA"),
    ("Los Angeles", "LAK"), ("Minnesota", "MIN"), ("Montreal", "MTL"),
    ("Nashville", "NSH"), ("New Jersey", "NJD"), ("Ottawa", "OTT"),
    ("Philadelphia", "PHI"), ("Pittsburgh", "PIT"), ("San Jose", "SJS"),
    ("Seattle", "SEA"), ("St. Louis", "STL"), ("Tampa Bay", "TBL"),
    ("Toronto", "TOR"), ("Utah", "UTA"), ("Vancouver", "VAN"),
    ("Vegas", "VGK"), ("Washington", "WSH"), ("Winnipeg", "WPG"),
    ("NY Rangers", "NYR"), ("NY Islanders", "NYI"),
])
def test_unique_discovery_city_alias(label, expected):
    assert team_abbreviation(label) == expected


@pytest.mark.parametrize("name", [
    "New York", "NY", "Dallas Cowboys", "New York Knicks",
    "Seattle Seahawks", "Florida Atlantic", "Utah State",
])
def test_ambiguous_or_other_sport_alias_stays_unresolved(name):
    assert team_abbreviation(name) is None


class _Session:
    status_code = 200
    def __init__(self):
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self
    def json(self):
        return {"games": [{
            "id": 2026020201, "gameType": 2, "gameState": "FUT",
            "startTimeUTC": "2026-11-06T01:00:00Z",
            "homeTeam": {"abbrev": "WPG"},
            "awayTeam": {"abbrev": "VAN"},
        }]}


def test_city_only_identity_still_requires_exact_official_schedule():
    session = _Session()
    found = resolve_nhl_current_event_identity(
        event_start_time="2026-11-06T01:00:00Z",
        home_team="Winnipeg", away_team="Vancouver", session=session,
    )
    assert found["event_id"] == "2026020201"
    assert found["can_execute"] is False
    assert "/club-schedule-season/WPG/20262027" in session.calls[0][0]
    with pytest.raises(NHLEventIdentityError) as error:
        resolve_nhl_current_event_identity(
            event_start_time="2026-11-06T04:01:00Z",
            home_team="Winnipeg", away_team="Vancouver", session=_Session(),
        )
    assert error.value.code == "NHL_CANONICAL_EVENT_NOT_FOUND"


def test_ambiguous_city_does_not_contact_official_source():
    session = _Session()
    with pytest.raises(NHLEventIdentityError) as error:
        resolve_nhl_current_event_identity(
            event_start_time="2026-11-06T01:00:00Z",
            home_team="New York", away_team="Vancouver", session=session,
        )
    assert error.value.code == "NHL_EVENT_PARTICIPANT_IDENTITY_UNRESOLVED"
    assert session.calls == []


def test_city_alias_still_rejects_ambiguous_official_fixture_ids():
    class DuplicateSource(_Session):
        def json(self):
            payload = super().json()
            payload["games"].append({**payload["games"][0], "id": 2026020202})
            return payload

    with pytest.raises(NHLEventIdentityError) as error:
        resolve_nhl_current_event_identity(
            event_start_time="2026-11-06T01:00:00Z",
            home_team="Winnipeg", away_team="Vancouver",
            session=DuplicateSource(),
        )
    assert error.value.code == "NHL_CANONICAL_EVENT_AMBIGUOUS"
