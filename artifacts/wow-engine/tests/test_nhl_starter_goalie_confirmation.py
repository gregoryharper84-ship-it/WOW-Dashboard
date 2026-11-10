from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_starter_goalie_confirmation import (
    NHLGoalieConfirmationError,
    confirm_nhl_starter_goalie,
)

NOW = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
EVENT_ID = "2026020219"
START = NOW + timedelta(hours=3)


def identity() -> dict:
    return {
        "event_id": EVENT_ID,
        "event_start_time": START.isoformat(),
        "home_team": "BOS",
        "away_team": "NYR",
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027",
        "identity_verified_at": NOW.isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def source(provider: str, snapshot: str, **overrides) -> dict:
    row = {
        "provider": provider,
        "source_kind": "STARTING_GOALIE_DESIGNATION",
        "source_snapshot_id": snapshot,
        "source_payload_sha256": "a" * 64,
        "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
        "event_id": EVENT_ID,
        "event_start_time_utc": START.isoformat(),
        "home_team": "BOS",
        "away_team": "NYR",
        "home_goalie_id": "8478402",
        "away_goalie_id": "8479318",
        "starting_status": "CONFIRMED",
    }
    row.update(overrides)
    return row


def test_two_independent_official_sources_confirm_both_starters():
    result = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source("NHL_PUBLIC_WEB_API", "snap-1"),
            source(
                "NHL_OFFICIAL_CLUB_FEED",
                "snap-2",
                source_payload_sha256="b" * 64,
                source_kind="OFFICIAL_ROSTER_DESIGNATION",
            ),
        ],
        as_of_utc=NOW,
    )
    assert result["status"] == "STARTER_GOALIE_CONFIRMED_RESEARCH_ONLY"
    assert result["goalie_status"] == "CONFIRMED"
    assert result["home_goalie_id"] == "8478402"
    assert result["away_goalie_id"] == "8479318"
    assert len(result["goalie_identity_snapshot_id"]) == 64
    assert result["independent_source_count"] == 2
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_gamecenter_participation_is_not_starter_confirmation():
    result = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source(
                "NHL_PUBLIC_WEB_API",
                "box-1",
                source_kind="GAMECENTER_BOXSCORE_PARTICIPATION",
            ),
            source("NHL_OFFICIAL_CLUB_FEED", "snap-2"),
        ],
        as_of_utc=NOW,
    )
    assert result["status"] == "HELD"
    assert result["goalie_status"] == "UNVERIFIED"
    assert "NHL_GAMECENTER_PARTICIPATION_NOT_CONFIRMATION" in result["blockers"]
    assert result["probability_publishable"] is False


def test_one_source_is_insufficient_even_when_official():
    result = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[source("NHL_PUBLIC_WEB_API", "snap-1")],
        as_of_utc=NOW,
    )
    assert result["status"] == "HELD"
    assert result["code"] == "NHL_GOALIE_INDEPENDENT_CONFIRMATION_INSUFFICIENT"


def test_same_provider_twice_is_not_independent():
    result = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source("NHL_PUBLIC_WEB_API", "snap-1"),
            source("NHL_PUBLIC_WEB_API", "snap-2", source_payload_sha256="c" * 64),
        ],
        as_of_utc=NOW,
    )
    assert result["status"] == "HELD"
    assert "NHL_GOALIE_SOURCES_NOT_INDEPENDENT" in result["blockers"]


def test_conflicting_starters_hold_instead_of_choosing_one():
    result = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source("NHL_PUBLIC_WEB_API", "snap-1"),
            source("NHL_OFFICIAL_CLUB_FEED", "snap-2", home_goalie_id="8479999"),
        ],
        as_of_utc=NOW,
    )
    assert result["status"] == "HELD"
    assert "NHL_STARTING_GOALIE_CONFLICT" in result["blockers"]
    assert result["goalie_identity_snapshot_id"] is None


def test_stale_and_post_kickoff_confirmations_hold():
    stale = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source("NHL_PUBLIC_WEB_API", "snap-1", observed_at=(NOW - timedelta(hours=2)).isoformat()),
            source("NHL_OFFICIAL_CLUB_FEED", "snap-2"),
        ],
        as_of_utc=NOW,
    )
    assert "NHL_GOALIE_CONFIRMATION_STALE" in stale["blockers"]

    # Once the canonical start has passed the identity gate holds before any
    # source can confirm a starter.
    future_started = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source("NHL_PUBLIC_WEB_API", "snap-1"),
            source("NHL_OFFICIAL_CLUB_FEED", "snap-2"),
        ],
        as_of_utc=START + timedelta(minutes=5),
    )
    assert "NHL_EVENT_NOT_PREGAME" in future_started["blockers"]
    assert future_started["status"] == "HELD"
    assert future_started["goalie_status"] == "UNVERIFIED"


def test_unhashed_snapshot_and_event_mismatch_hold():
    result = confirm_nhl_starter_goalie(
        identity=identity(),
        sources=[
            source("NHL_PUBLIC_WEB_API", "snap-1", source_payload_sha256="not-a-hash"),
            source("NHL_OFFICIAL_CLUB_FEED", "snap-2", event_id="2026020999"),
        ],
        as_of_utc=NOW,
    )
    assert "NHL_GOALIE_CONFIRMATION_PAYLOAD_HASH_INVALID" in result["blockers"]
    assert "NHL_GOALIE_CONFIRMATION_EVENT_MISMATCH" in result["blockers"]


def test_naive_as_of_time_is_a_programmer_error():
    with pytest.raises(NHLGoalieConfirmationError):
        confirm_nhl_starter_goalie(
            identity=identity(),
            sources=[source("NHL_PUBLIC_WEB_API", "snap-1"), source("NHL_OFFICIAL_CLUB_FEED", "snap-2")],
            as_of_utc=datetime(2026, 11, 10, 17, 0),
        )
