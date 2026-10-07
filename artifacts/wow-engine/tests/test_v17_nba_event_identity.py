from __future__ import annotations

import pytest

from v17 import nba_event_identity as identity


def _fetch(_sport, _year):
    return [
        {
            "game_id": "401950001",
            "game_date": "2026-10-07",
            "home_id": "13",
            "away_id": "24",
        }
    ]


def test_resolves_provider_team_aliases_through_schedule_row():
    out = identity.resolve_nba_current_event_identity(
        event_start_time="2026-10-08T00:30:00Z",
        home_team_alias="espn-13",
        away_team_alias="espn-24",
        fetcher=_fetch,
    )
    assert out["event_id"] == "espn-401950001"
    assert out["identity_provider"] == "SPORTSDATAVERSE_ESPN"
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False


def test_discovery_event_id_is_not_needed_to_assign_canonical_identity():
    out = identity.resolve_nba_current_event_identity(
        event_start_time="2026-10-08T00:30:00Z",
        home_team_alias="13",
        away_team_alias="24",
        fetcher=_fetch,
    )
    assert out["event_id"] == "espn-401950001"


def test_team_mismatch_fails_closed():
    with pytest.raises(identity.NBAEventIdentityError) as exc:
        identity.resolve_nba_current_event_identity(
            event_start_time="2026-10-08T00:30:00Z",
            home_team_alias="espn-1",
            away_team_alias="espn-2",
            fetcher=_fetch,
        )
    assert exc.value.code == "NBA_CANONICAL_EVENT_NOT_FOUND"


def test_multiple_schedule_ids_fail_ambiguous():
    def duplicate(_sport, _year):
        return [
            {"game_id": "1", "game_date": "2026-10-07", "home_id": "13", "away_id": "24"},
            {"game_id": "2", "game_date": "2026-10-08", "home_id": "13", "away_id": "24"},
        ]

    with pytest.raises(identity.NBAEventIdentityError) as exc:
        identity.resolve_nba_current_event_identity(
            event_start_time="2026-10-08T00:30:00Z",
            home_team_alias="espn-13",
            away_team_alias="espn-24",
            fetcher=duplicate,
        )
    assert exc.value.code == "NBA_CANONICAL_EVENT_AMBIGUOUS"



def test_nba_schedule_asset_uses_season_ending_year():
    from datetime import datetime, timezone

    assert identity.schedule_season_year(
        datetime(2026, 10, 7, tzinfo=timezone.utc)
    ) == 2027
    assert identity.schedule_season_year(
        datetime(2026, 6, 13, tzinfo=timezone.utc)
    ) == 2026


def test_october_2026_identity_fetches_2027_schedule_asset():
    seen = {}

    def fetch(sport, year):
        seen["sport"] = sport
        seen["year"] = year
        return [{
            "game_id": "401950001",
            "game_date": "2026-10-07",
            "home_id": "13",
            "away_id": "24",
        }]

    out = identity.resolve_nba_current_event_identity(
        event_start_time="2026-10-08T00:30:00Z",
        home_team_alias="espn-13",
        away_team_alias="espn-24",
        fetcher=fetch,
    )
    assert out["event_id"] == "espn-401950001"
    assert seen == {"sport": "NBA", "year": 2027}
