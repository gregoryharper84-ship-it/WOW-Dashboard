from __future__ import annotations

import pytest

from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_prop_historical_adapter import (
    NCAAFPropHistoricalAdapterError,
    PHASE1_STAT_TYPES,
    build_schedule_index,
    normalize_player_stats_corpus,
)


def _snapshot(endpoint, rows, *, payload_hash, retrieved_at="2026-09-20T04:00:00+00:00"):
    return SourceSnapshot(
        provider="CFBD",
        endpoint=endpoint,
        season=2026,
        week=3,
        requested_at="2026-09-20T03:59:00+00:00",
        retrieved_at=retrieved_at,
        request_params={"year": 2026, "week": 3},
        response_rows=rows,
        response_row_count=len(rows),
        payload_sha256=payload_hash,
        acquisition_status="AVAILABLE",
        blocker_codes=[],
    )


GAME = {
    "id": 9001,
    "season": 2026,
    "week": 3,
    "seasonType": "regular",
    "startDate": "2026-09-19T23:00:00Z",
    "completed": True,
    "homeTeam": "Alpha",
    "awayTeam": "Beta",
    "homePoints": 35,
    "awayPoints": 24,
}

PLAYER_GAME = {
    "id": 9001,
    "teams": [
        {
            "team": "Alpha",
            "categories": [
                {
                    "name": "passing",
                    "types": [
                        {"name": "YDS", "athletes": [{"id": "qb-a", "name": "QB A", "stat": "301"}]},
                        {"name": "C/ATT", "athletes": [{"id": "qb-a", "name": "QB A", "stat": "24/33"}]},
                    ],
                },
                {
                    "name": "rushing",
                    "types": [
                        {"name": "CAR", "athletes": [{"id": "rb-a", "name": "RB A", "stat": "18"}]},
                        {"name": "YDS", "athletes": [{"id": "rb-a", "name": "RB A", "stat": "112"}]},
                    ],
                },
                {
                    "name": "receiving",
                    "types": [
                        {"name": "REC", "athletes": [{"id": "wr-a", "name": "WR A", "stat": "8"}]},
                        {"name": "YDS", "athletes": [{"id": "wr-a", "name": "WR A", "stat": "119"}]},
                    ],
                },
            ],
        },
        {
            "team": "Beta",
            "categories": [
                {
                    "name": "passing",
                    "types": [
                        {"name": "YDS", "athletes": [{"id": "qb-b", "name": "QB B", "stat": "244"}]},
                        {"name": "C/ATT", "athletes": [{"id": "qb-b", "name": "QB B", "stat": "19/31"}]},
                    ],
                },
                {
                    "name": "rushing",
                    "types": [
                        {"name": "CAR", "athletes": [{"id": "rb-b", "name": "RB B", "stat": "15"}]},
                        {"name": "YDS", "athletes": [{"id": "rb-b", "name": "RB B", "stat": "74"}]},
                    ],
                },
                {
                    "name": "receiving",
                    "types": [
                        {"name": "REC", "athletes": [{"id": "wr-b", "name": "WR B", "stat": "6"}]},
                        {"name": "YDS", "athletes": [{"id": "wr-b", "name": "WR B", "stat": "91"}]},
                    ],
                },
            ],
        },
    ],
}


def test_all_phase1_routes_normalize_with_event_team_and_opponent_identity():
    game_snapshot = _snapshot("/games", [GAME], payload_hash="a" * 64)
    player_snapshot = _snapshot("/games/players", [PLAYER_GAME], payload_hash="b" * 64)

    rows = normalize_player_stats_corpus([player_snapshot], game_snapshots=[game_snapshot])

    assert {row.stat_type for row in rows} == set(PHASE1_STAT_TYPES)
    assert len(rows) == 14
    qb_a = {row.stat_type: row for row in rows if row.identity.participant_id == "qb-a"}
    assert qb_a["PASSING_YARDS"].actual_value == 301.0
    assert qb_a["COMPLETIONS"].actual_value == 24.0
    assert qb_a["PASS_ATTEMPTS"].actual_value == 33.0
    assert qb_a["PASSING_YARDS"].identity.event_id == "9001"
    assert qb_a["PASSING_YARDS"].identity.team_id == "Alpha"
    assert qb_a["PASSING_YARDS"].identity.opponent_id == "Beta"
    assert qb_a["PASSING_YARDS"].identity.provider_ids == {
        "CFBD_PLAYER_ID": "qb-a",
        "CFBD_GAME_ID": "9001",
    }
    assert all(row.source_provider == "CFBD" for row in rows)
    assert all(row.source_payload_hash == "b" * 64 for row in rows)
    assert all(row.can_execute is False for row in rows)


def test_requested_route_filter_never_creates_unrequested_outcomes():
    rows = normalize_player_stats_corpus(
        [_snapshot("/games/players", [PLAYER_GAME], payload_hash="b" * 64)],
        game_snapshots=[_snapshot("/games", [GAME], payload_hash="a" * 64)],
        stat_types=["PASSING_YARDS", "PASS_ATTEMPTS"],
    )
    assert {row.stat_type for row in rows} == {"PASSING_YARDS", "PASS_ATTEMPTS"}
    assert len(rows) == 4


def test_unsupported_route_fails_closed():
    with pytest.raises(NCAAFPropHistoricalAdapterError) as exc:
        normalize_player_stats_corpus(
            [_snapshot("/games/players", [PLAYER_GAME], payload_hash="b" * 64)],
            game_snapshots=[_snapshot("/games", [GAME], payload_hash="a" * 64)],
            stat_types=["TOUCHDOWNS"],
        )
    assert exc.value.code == "NCAAF_PROP_STAT_TYPE_UNSUPPORTED"


def test_unresolved_schedule_identity_fails_closed():
    broken = {**PLAYER_GAME, "id": 9999}
    with pytest.raises(NCAAFPropHistoricalAdapterError) as exc:
        normalize_player_stats_corpus(
            [_snapshot("/games/players", [broken], payload_hash="b" * 64)],
            game_snapshots=[_snapshot("/games", [GAME], payload_hash="a" * 64)],
        )
    assert exc.value.code == "NCAAF_PROP_SCHEDULE_GAME_UNRESOLVED"


def test_team_mismatch_fails_closed():
    broken = {**PLAYER_GAME, "teams": [{**PLAYER_GAME["teams"][0], "team": "Gamma"}, PLAYER_GAME["teams"][1]]}
    with pytest.raises(NCAAFPropHistoricalAdapterError) as exc:
        normalize_player_stats_corpus(
            [_snapshot("/games/players", [broken], payload_hash="b" * 64)],
            game_snapshots=[_snapshot("/games", [GAME], payload_hash="a" * 64)],
        )
    assert exc.value.code == "NCAAF_PROP_PLAYER_GAME_TEAM_MISMATCH"


def test_premature_player_snapshot_is_rejected():
    with pytest.raises(NCAAFPropHistoricalAdapterError) as exc:
        normalize_player_stats_corpus(
            [
                _snapshot(
                    "/games/players",
                    [PLAYER_GAME],
                    payload_hash="b" * 64,
                    retrieved_at="2026-09-19T22:00:00+00:00",
                )
            ],
            game_snapshots=[_snapshot("/games", [GAME], payload_hash="a" * 64)],
        )
    assert exc.value.code == "NCAAF_PROP_OUTCOME_TIMESTAMP_PREMATURE"


def test_invalid_completion_attempt_pair_fails_closed():
    passing = PLAYER_GAME["teams"][0]["categories"][0]
    broken_passing = {
        **passing,
        "types": [passing["types"][0], {**passing["types"][1], "athletes": [{"id": "qb-a", "name": "QB A", "stat": "34/33"}]}],
    }
    broken_team = {
        **PLAYER_GAME["teams"][0],
        "categories": [broken_passing, *PLAYER_GAME["teams"][0]["categories"][1:]],
    }
    broken = {**PLAYER_GAME, "teams": [broken_team, PLAYER_GAME["teams"][1]]}
    with pytest.raises(NCAAFPropHistoricalAdapterError) as exc:
        normalize_player_stats_corpus(
            [_snapshot("/games/players", [broken], payload_hash="b" * 64)],
            game_snapshots=[_snapshot("/games", [GAME], payload_hash="a" * 64)],
        )
    assert exc.value.code == "NCAAF_PROP_PASSING_C_ATT_INVALID"


def test_schedule_conflict_fails_closed():
    game_a = _snapshot("/games", [GAME], payload_hash="a" * 64)
    conflicting = {**GAME, "homeTeam": "Gamma"}
    game_b = _snapshot(
        "/games",
        [conflicting],
        payload_hash="c" * 64,
        retrieved_at="2026-09-20T05:00:00+00:00",
    )
    with pytest.raises(NCAAFPropHistoricalAdapterError) as exc:
        build_schedule_index([game_a, game_b])
    assert exc.value.code == "NCAAF_PROP_SCHEDULE_IDENTITY_CONFLICT"
