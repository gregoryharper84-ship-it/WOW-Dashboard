"""Contract tests for the *inactive-list* proof; never grants scoring. #2131."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from nfl_confirmed_inactives_attestation import (
    CANDIDATE_READY,
    UNAVAILABLE,
    UNVERIFIED,
    verify_confirmed_game_inactives,
)

NOW = datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)
START = NOW + timedelta(minutes=55)
GAME_ID = 23456
OFFICIAL = "https://www.nfl.com/news/example-game-inactives"


def _team(code, players):
    at = (NOW - timedelta(minutes=12)).isoformat()
    return {
        "team_abbr": code,
        "status": "confirmed",
        "published_at": at,
        "source_url": OFFICIAL,
        "players": [
            {"name": name, "canonical_name": name, "player_key": f"{code}-{i}",
             "player_id": 1000 + i, "identity_status": "resolved"}
            for i, name in enumerate(players)
        ],
        "report": {
            "game_id": GAME_ID,
            "team_abbr": code,
            "report_kind": "inactives",
            "coverage_status": "complete",
            "latest_observed_coverage": "complete",
            "last_check_status": "succeeded",
            "retained_previous_report": False,
            "revision": "revision1",
            "source_url": OFFICIAL,
            "source_published_at": at,
            "last_checked_at": (NOW - timedelta(minutes=1)).isoformat(),
            "source_row_count": len(players),
            "identity_resolved_count": len(players),
        },
    }


def _response():
    return {
        "meta": {"game_id": GAME_ID},
        "data": {
            "game_id": GAME_ID,
            "teams": [
                _team("PHI", ["Reserve Player", "Other Inactive"]),
                _team("DAL", ["Visiting Inactive"]),
            ],
        },
    }


def _verify(body=None, **kwargs):
    settings = {
        "provider_game_id": GAME_ID,
        "home_team": "PHI",
        "away_team": "DAL",
        "player_name": "Eligible Candidate",
        "player_team": "PHI",
        "event_start": START,
        "now": NOW,
    }
    settings.update(kwargs)
    return verify_confirmed_game_inactives(_response() if body is None else body, **settings)


def test_full_fresh_official_report_is_only_candidate_not_permission_to_score():
    out = _verify()
    assert out["code"] == CANDIDATE_READY
    assert out["reason"].endswith("NOT_ADOPTED")
    assert out["can_execute"] is False
    assert out["probability_publishable"] is False


def test_reported_inactive_is_definitively_unavailable():
    out = _verify(player_name="Reserve Player")
    assert out["code"] == UNAVAILABLE
    assert out["reason"] == "OFFICIAL_GAME_DAY_INACTIVE"


@pytest.mark.parametrize("mutation,reason", [
    (lambda d: d["data"].update(game_id=99999), "PROVIDER_GAME_ID_MISMATCH"),
    (lambda d: d["meta"].update(game_id=99999), "PROVIDER_GAME_ID_MISMATCH"),
    (lambda d: d["data"]["teams"].pop(), "BOTH_TEAMS_REPORTS_REQUIRED"),
    (lambda d: d["data"]["teams"][1].update(team_abbr="PHI"), "TEAM_REPORT_MATCH_FAILED"),
    (lambda d: d["data"]["teams"][0].update(status="not_available"), "GAME_DAY_INACTIVES_NOT_CONFIRMED"),
    (lambda d: d["data"]["teams"][0]["report"].update(coverage_status="partial"), "REPORT_COVERAGE_OR_REVISION_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0]["report"].update(latest_observed_coverage="partial"), "REPORT_COVERAGE_OR_REVISION_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0]["report"].update(retained_previous_report=True), "REPORT_COVERAGE_OR_REVISION_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0]["report"].update(last_check_status="unknown"), "REPORT_COVERAGE_OR_REVISION_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0].update(source_url="https://evil.example.com/report"), "OFFICIAL_SOURCE_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0].update(published_at=None), "PUBLICATION_TIME_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0].update(published_at=(NOW - timedelta(days=2)).isoformat()), "PUBLICATION_TIME_UNVERIFIED"),
    (lambda d: d["data"]["teams"][0]["report"].update(last_checked_at=(NOW - timedelta(hours=3)).isoformat()), "SOURCE_CHECK_NOT_FRESH"),
    (lambda d: d["data"]["teams"][0]["report"].update(identity_resolved_count=1), "INACTIVE_POPULATION_INCOMPLETE"),
    (lambda d: d["data"]["teams"][0]["players"][0].update(identity_status="ambiguous"), "INACTIVE_IDENTITY_UNRESOLVED"),
    (lambda d: d["data"]["teams"][0]["players"][0].update(player_key=None), "INACTIVE_IDENTITY_UNRESOLVED"),
    (lambda d: d["data"]["teams"][0]["players"][0].update(canonical_name="Other Inactive"), "DUPLICATE_OR_MISSING_PLAYER_IDENTITY"),
])
def test_completeness_freshness_identity_or_source_failure_never_green(mutation, reason):
    body = deepcopy(_response())
    mutation(body)
    out = _verify(body)
    assert out["code"] == UNVERIFIED
    assert out["reason"] == reason
    assert out["probability_publishable"] is False


@pytest.mark.parametrize("minutes", [-1, 0, 130])
def test_early_or_started_games_not_admitted(minutes):
    out = _verify(event_start=NOW + timedelta(minutes=minutes))
    assert out["code"] == UNVERIFIED
    assert out["reason"] == "OUTSIDE_GAME_DAY_ADMISSION_WINDOW"


def test_fake_team_identity_is_not_authenticated():
    out = _verify(player_team="WAS")
    assert out["code"] == UNVERIFIED
    assert out["reason"] == "PLAYER_EVENT_IDENTITY_MISMATCH"


def test_absent_report_field_is_not_interpreted_as_empty_inactives():
    body = deepcopy(_response())
    body["data"]["teams"][0].pop("players")
    out = _verify(body)
    assert out["code"] == UNVERIFIED
    assert out["reason"] == "INACTIVE_POPULATION_INCOMPLETE"


def test_unconfirmed_second_team_never_enables_first_team():
    body = deepcopy(_response())
    body["data"]["teams"][1]["status"] = "not_available"
    out = _verify(body)
    assert out["code"] == UNVERIFIED
    assert out["reason"] == "GAME_DAY_INACTIVES_NOT_CONFIRMED"
