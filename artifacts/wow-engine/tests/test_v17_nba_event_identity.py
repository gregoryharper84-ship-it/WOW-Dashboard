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


# --- Consecutive-day same-pairing disambiguation (2026-27 schedule) ---------
# Rows mirror the published SportsDataverse 2026-27 asset: POR (22) hosts
# HOU (10) on 2027-03-30 and again on 2027-03-31. A ±1-day team/date window
# matches both, so the schedule's own exact start time must break the tie.

_POR_HOU = [
    {
        "game_id": "401910853", "game_date": "2027-03-30",
        "date": "2027-03-31T03:00Z",
        "game_date_time": "2027-03-30T23:00:00.000000-0400",
        "time_valid": "true", "home_id": "22", "away_id": "10",
    },
    {
        "game_id": "401910863", "game_date": "2027-03-31",
        "date": "2027-04-01T02:00Z",
        "game_date_time": "2027-03-31T22:00:00.000000-0400",
        "time_valid": "true", "home_id": "22", "away_id": "10",
    },
]


def _rows(rows):
    return lambda _sport, _year: [dict(row) for row in rows]


@pytest.mark.parametrize(
    ("start", "expected", "resolution"),
    [
        # UTC 03-31 window spans both games -> start time breaks the tie.
        ("2027-03-31T03:00:00Z", "espn-401910853", "SPORTSDATAVERSE_SCHEDULE_TEAM_DATE_START_TIME_DISAMBIGUATED"),
        ("2027-03-31T03:20:00Z", "espn-401910853", "SPORTSDATAVERSE_SCHEDULE_TEAM_DATE_START_TIME_DISAMBIGUATED"),
        # UTC 04-01 window holds only the second game -> unchanged unique path.
        ("2027-04-01T02:00:00Z", "espn-401910863", "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH"),
    ],
)
def test_consecutive_day_pairing_resolves_by_exact_start(start, expected, resolution):
    out = identity.resolve_nba_current_event_identity(
        event_start_time=start,
        home_team_alias="espn-22",
        away_team_alias="espn-10",
        fetcher=_rows(_POR_HOU),
    )
    assert out["event_id"] == expected
    assert out["identity_resolution"] == resolution
    assert out["event_start_time"] is not None
    assert out["prediction_authority"] is False
    assert out["market_features_used"] is False
    assert out["can_execute"] is False


def test_start_outside_tolerance_stays_ambiguous_not_guessed():
    with pytest.raises(identity.NBAEventIdentityError) as exc:
        identity.resolve_nba_current_event_identity(
            event_start_time="2027-03-31T14:00:00Z",  # 11h from both games
            home_team_alias="espn-22",
            away_team_alias="espn-10",
            fetcher=_rows(_POR_HOU),
        )
    assert exc.value.code == "NBA_CANONICAL_EVENT_AMBIGUOUS"


def test_unconfirmed_schedule_time_cannot_break_tie():
    rows = [dict(row, time_valid="false") for row in _POR_HOU]
    with pytest.raises(identity.NBAEventIdentityError) as exc:
        identity.resolve_nba_current_event_identity(
            event_start_time="2027-03-31T03:00:00Z",
            home_team_alias="espn-22",
            away_team_alias="espn-10",
            fetcher=_rows(rows),
        )
    assert exc.value.code == "NBA_CANONICAL_EVENT_AMBIGUOUS"


def test_two_ids_at_same_start_stay_ambiguous():
    rows = [_POR_HOU[0], dict(_POR_HOU[0], game_id="401999999")]
    with pytest.raises(identity.NBAEventIdentityError) as exc:
        identity.resolve_nba_current_event_identity(
            event_start_time="2027-03-31T03:00:00Z",
            home_team_alias="espn-22",
            away_team_alias="espn-10",
            fetcher=_rows(rows),
        )
    assert exc.value.code == "NBA_CANONICAL_EVENT_AMBIGUOUS"


def test_offset_only_start_column_is_accepted_for_tiebreak():
    rows = [
        {k: v for k, v in row.items() if k != "date"} for row in _POR_HOU
    ]
    out = identity.resolve_nba_current_event_identity(
        event_start_time="2027-04-01T02:00:00Z",
        home_team_alias="espn-22",
        away_team_alias="espn-10",
        fetcher=_rows(rows),
    )
    assert out["event_id"] == "espn-401910863"


def test_unique_date_match_path_is_unchanged():
    out = identity.resolve_nba_current_event_identity(
        event_start_time="2026-10-08T00:30:00Z",
        home_team_alias="espn-13",
        away_team_alias="espn-24",
        fetcher=_fetch,
    )
    assert out["identity_resolution"] == (
        "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH"
    )
    assert out["event_start_time"] is None  # fixture row carries no start time
