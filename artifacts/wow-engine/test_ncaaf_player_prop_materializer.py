from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_player_prop_materializer import materialize_player_stats


def _snapshot():
    return SourceSnapshot(
        provider="CFBD",
        endpoint="/games/players",
        season=2026,
        week=3,
        requested_at="2026-09-18T20:00:00+00:00",
        retrieved_at="2026-09-18T20:00:01+00:00",
        request_params={"year": 2026, "week": 3, "classification": "fbs"},
        response_rows=[
            {
                "id": 123,
                "teams": [
                    {
                        "team": "Miami",
                        "conference": "ACC",
                        "homeAway": "away",
                        "points": 31,
                        "categories": [
                            {
                                "name": "passing",
                                "types": [
                                    {"name": "C/ATT", "athletes": [{"id": "7", "name": "Darian Mensah", "stat": "24/33"}]},
                                    {"name": "YDS", "athletes": [{"id": "7", "name": "Darian Mensah", "stat": "289"}]},
                                    {"name": "TD", "athletes": [{"id": "7", "name": "Darian Mensah", "stat": "3"}]},
                                    {"name": "INT", "athletes": [{"id": "7", "name": "Darian Mensah", "stat": "1"}]},
                                ],
                            },
                            {
                                "name": "rushing",
                                "types": [
                                    {"name": "CAR", "athletes": [{"id": "22", "name": "Runner A", "stat": "17"}]},
                                    {"name": "YDS", "athletes": [{"id": "22", "name": "Runner A", "stat": "91"}]},
                                    {"name": "TD", "athletes": [{"id": "22", "name": "Runner A", "stat": "1"}]},
                                ],
                            },
                            {
                                "name": "receiving",
                                "types": [
                                    {"name": "REC", "athletes": [{"id": "4", "name": "Receiver A", "stat": "8"}]},
                                    {"name": "YDS", "athletes": [{"id": "4", "name": "Receiver A", "stat": "113"}]},
                                    {"name": "TD", "athletes": [{"id": "4", "name": "Receiver A", "stat": "2"}]},
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
        response_row_count=1,
        payload_sha256="a" * 64,
        acquisition_status="AVAILABLE",
        blocker_codes=[],
    )


def test_materializes_phase_one_primitives_and_split_completions_attempts():
    rows = materialize_player_stats(_snapshot())
    values = {(row.athlete_name, row.stat_type): row.stat_value for row in rows}

    assert values[("Darian Mensah", "PASS_COMPLETIONS")] == 24
    assert values[("Darian Mensah", "PASS_ATTEMPTS")] == 33
    assert values[("Darian Mensah", "PASS_YARDS")] == 289
    assert values[("Darian Mensah", "PASS_TDS")] == 3
    assert values[("Darian Mensah", "INTERCEPTIONS_THROWN")] == 1
    assert values[("Runner A", "RUSH_ATTEMPTS")] == 17
    assert values[("Runner A", "RUSH_YARDS")] == 91
    assert values[("Receiver A", "RECEPTIONS")] == 8
    assert values[("Receiver A", "RECEIVING_YARDS")] == 113
    assert all(row.can_execute is False for row in rows)


def test_unknown_stat_type_is_preserved_in_raw_source_but_not_promoted():
    snapshot = _snapshot()
    snapshot.response_rows[0]["teams"][0]["categories"][0]["types"].append(
        {"name": "QBR", "athletes": [{"id": "7", "name": "Darian Mensah", "stat": "85.4"}]}
    )
    rows = materialize_player_stats(snapshot)
    assert "QBR" not in {row.stat_type for row in rows}


def test_non_player_snapshot_is_not_materialized():
    snapshot = _snapshot()
    snapshot = SourceSnapshot(**{**snapshot.__dict__, "endpoint": "/games"})
    assert materialize_player_stats(snapshot) == []
