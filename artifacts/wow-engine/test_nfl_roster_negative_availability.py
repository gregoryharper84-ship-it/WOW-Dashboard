"""P0 #2131 negative-only ESPN roster regression: no self-attested positives."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nfl_roster_negative_availability import read_espn_nfl_roster_negative

NOW = datetime(2026, 10, 26, 21, 10, tzinfo=timezone.utc)


def _roster(*, label="injuredReserveOrOut", timestamp=None, team="PHI", athlete_id="4040715", status_type="day-to-day"):
    return {
        "timestamp": timestamp or (NOW - timedelta(minutes=5)).isoformat(),
        "team": {"abbreviation": team},
        "athletes": [
            {"position": label, "items": [
                {"id": athlete_id, "displayName": "Jalen Hurts", "status": {"type": status_type}},
            ]},
        ],
    }


def _read(payload, *, response_status=200, now=NOW, team="PHI", athlete_id="4040715"):
    requests = []
    def get(url, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(status_code=response_status, content=b"{}", json=lambda: payload)
    result = read_espn_nfl_roster_negative(
        team=team, espn_athlete_id=athlete_id, http_get=get, now=now
    )
    return result, requests


def test_real_hurts_ir_group_is_negative_only():
    out, requests = _read(_roster())
    assert out["code"] == "PLAYER_UNAVAILABLE"
    assert out["reason"] == "ESPN_ROSTER_IR_OR_OUT"
    assert out["can_execute"] is False
    assert out["probability_publishable"] is False
    assert len(requests) == 1
    assert requests[0][0] == "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/phi/roster"
    assert requests[0][1]["timeout"] == 3.0


@pytest.mark.parametrize("label,status_type", [
    ("offense", "active"), ("defense", "active"), ("specialTeam", "active"),
    ("offense", "day-to-day"), ("offense", "questionable")
])
def test_active_roster_or_ambiguous_injury_never_attests_participation(label, status_type):
    out, _ = _read(_roster(label=label, status_type=status_type))
    assert out["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"


@pytest.mark.parametrize("status_type", ["out", "injured-reserve", "suspended", "inactive"])
def test_explicit_individual_roster_unavailable_tag(status_type):
    out, _ = _read(_roster(label="offense", status_type=status_type))
    assert out["code"] == "PLAYER_UNAVAILABLE"


@pytest.mark.parametrize("payload,reason", [
    (_roster(team="DAL"), "SOURCE_TEAM_MISMATCH"),
    (_roster(timestamp=(NOW - timedelta(days=3)).isoformat()), "SOURCE_TIMESTAMP_STALE_OR_MISSING"),
    (_roster(timestamp=(NOW + timedelta(hours=1)).isoformat()), "SOURCE_TIMESTAMP_STALE_OR_MISSING"),
    (_roster(timestamp="not a date"), "SOURCE_TIMESTAMP_STALE_OR_MISSING"),
    (_roster(athlete_id="99999"), "ATHLETE_NOT_UNIQUELY_PRESENT"),
    ({"timestamp": NOW.isoformat(), "team": {"abbreviation": "PHI"}}, "SOURCE_SCHEMA_INVALID"),
])
def test_bad_identity_timestamp_or_schema_fails_closed(payload, reason):
    out, _ = _read(payload)
    assert out["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert out["reason"] == reason


def test_duplicate_athlete_in_conflicting_groups_not_authoritative():
    payload = _roster()
    payload["athletes"].append({
        "position": "offense",
        "items": [{"id": "4040715", "status": {"type": "active"}}],
    })
    out, _ = _read(payload)
    assert out["reason"] == "ATHLETE_NOT_UNIQUELY_PRESENT"


@pytest.mark.parametrize("team,athlete_id", [
    ("PHI/../../", "4040715"), ("", "4040715"), ("PHI", "bad-id"),
    ("PHI", "../4040715"),
])
def test_user_supplied_ids_cannot_change_source_host_or_path(team, athlete_id):
    out, requests = _read(_roster(), team=team, athlete_id=athlete_id)
    assert out["reason"] == "ATHLETE_TEAM_IDENTITY_MISSING"
    assert requests == []


def test_transport_error_cannot_upgrade_to_active():
    def broken(*args, **kwargs):
        raise TimeoutError("provider")
    out = read_espn_nfl_roster_negative(
        team="PHI", espn_athlete_id="4040715", http_get=broken, now=NOW
    )
    assert out["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert out["reason"] == "SOURCE_TRANSPORT_UNAVAILABLE"


def test_provider_http_500_is_unverified():
    out, _ = _read(_roster(), response_status=500)
    assert out["reason"] == "SOURCE_HTTP_NOT_OK"
    assert out["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"
