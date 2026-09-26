from pathlib import Path

import pytest

from ncaaf_cfbd_client import CFBDResponse
from ncaaf_cfbd_hydrator import (
    hydrate_cfbd_player_stats_season,
    hydrate_cfbd_season,
    profile_cfbd_player_stats,
)


PLAYER_GAME = {
    "id": 401,
    "teams": [
        {
            "team": "Alpha",
            "conference": "SEC",
            "homeAway": "home",
            "points": 31,
            "categories": [
                {
                    "name": "passing",
                    "types": [
                        {
                            "name": "YDS",
                            "athletes": [
                                {"id": "qb-1", "name": "Quarter Back", "stat": "287"},
                            ],
                        },
                        {
                            "name": "C/ATT",
                            "athletes": [
                                {"id": "qb-1", "name": "Quarter Back", "stat": "22/31"},
                            ],
                        },
                    ],
                },
                {
                    "name": "rushing",
                    "types": [
                        {
                            "name": "CAR",
                            "athletes": [
                                {"id": "rb-1", "name": "Running Back", "stat": "18"},
                            ],
                        },
                    ],
                },
            ],
        }
    ],
}


class FakeCFBD:
    def games(self, *, year, week=None, classification=None):
        return CFBDResponse(endpoint="/games", params={"year": year, "week": week, "classification": classification}, rows=[{"id": f"{year}-{week}"}])

    def player_game_stats(self, *, year, week=None, team=None, conference=None, classification=None, season_type=None, category=None):
        return CFBDResponse(
            endpoint="/games/players",
            params={"year": year, "week": week, "classification": classification, "seasonType": season_type},
            rows=[PLAYER_GAME],
        )

    def ratings(self, family, *, year, week=None):
        endpoint = {"elo": "/ratings/elo", "sp": "/ratings/sp"}[family]
        return CFBDResponse(endpoint=endpoint, params={"year": year, **({"week": week} if week is not None else {})}, rows=[{"team": "A", "rating": 1.0}])


def test_weekly_games_and_elo_are_staged_with_hashes():
    rows = hydrate_cfbd_season(FakeCFBD(), season=2025, weeks=[1, 2], rating_families=("elo",))
    assert len(rows) == 4
    assert all(row.provider == "CFBD" for row in rows)
    assert all(len(row.payload_sha256) == 64 for row in rows)
    assert all(row.can_execute is False for row in rows)


def test_player_box_scores_are_staged_with_immutable_hashes():
    rows = hydrate_cfbd_player_stats_season(FakeCFBD(), season=2025, weeks=[1, 2])
    assert len(rows) == 2
    assert all(row.endpoint == "/games/players" for row in rows)
    assert all(row.provider == "CFBD" for row in rows)
    assert all(len(row.payload_sha256) == 64 for row in rows)
    assert all(row.can_execute is False for row in rows)


def test_player_stat_profile_validates_nested_schema_without_probability_authority():
    rows = hydrate_cfbd_player_stats_season(FakeCFBD(), season=2025, weeks=[1])
    profile = profile_cfbd_player_stats(rows)

    assert profile["status"] == "READY_FOR_NORMALIZATION"
    assert profile["game_n"] == 1
    assert profile["participant_id_n"] == 2
    assert profile["athlete_stat_n"] == 3
    assert profile["category_types"] == {
        "passing": ["C/ATT", "YDS"],
        "rushing": ["CAR"],
    }
    assert profile["probability_publishable"] is False
    assert profile["can_execute"] is False


def test_player_stat_profile_fails_closed_on_schema_drift():
    class BrokenCFBD(FakeCFBD):
        def player_game_stats(self, **kwargs):
            return CFBDResponse(endpoint="/games/players", params=kwargs, rows=[{"id": 401, "teams": {}}])

    rows = hydrate_cfbd_player_stats_season(BrokenCFBD(), season=2025, weeks=[1])
    profile = profile_cfbd_player_stats(rows)
    assert profile["status"] == "BLOCKED"
    assert profile["blocker"] == "NCAAF_PROP_PLAYER_STATS_SCHEMA_INVALID"
    assert profile["malformed_n"] > 0


def test_full_season_rating_is_explicitly_blocked_from_pregame_feature_use():
    rows = hydrate_cfbd_season(FakeCFBD(), season=2025, weeks=[1], rating_families=("sp",))
    season_rating = next(row for row in rows if row.endpoint == "/ratings/sp")
    assert season_rating.week is None
    assert "RETROSPECTIVE_RATING_NOT_PREGAME_FEATURE" in season_rating.blocker_codes


def test_invalid_week_contract_fails_closed():
    with pytest.raises(ValueError):
        hydrate_cfbd_season(FakeCFBD(), season=2025, weeks=[])
    with pytest.raises(ValueError):
        hydrate_cfbd_player_stats_season(FakeCFBD(), season=2025, weeks=[])


def test_staging_sql_is_internal_and_non_executable():
    sql = Path(__file__).with_name("ncaaf_cfbd_staging.sql").read_text()
    lowered = sql.lower()
    assert "enable row level security" in lowered
    assert "revoke all on table public.wow_ncaaf_source_snapshots from anon, authenticated" in lowered
    assert "can_execute boolean not null default false" in lowered
    assert "wow_ncaaf_source_never_execute" in lowered
