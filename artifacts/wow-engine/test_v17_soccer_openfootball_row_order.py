"""Regression: openfootball soccer rows must satisfy the multiclass chronology contract.

Production soccer-model-maintenance blocked every run with
MULTICLASS_ROWS_NOT_CHRONOLOGICAL because same-kickoff matches were emitted in
team-name order while the lifecycle requires (event_start_time, event_id).
"""
from __future__ import annotations

from datetime import date, timedelta

from v17 import soccer_openfootball_candidate as soccer
from v17.multiclass_candidate_lifecycle import train_multiclass_candidate

TEAMS = [f"Team {chr(65 + i)}" for i in range(20)]


def _season_matches() -> list[dict]:
    """Double round robin; every matchday's 10 games kick off simultaneously."""
    n = len(TEAMS)
    rounds = []
    teams = list(range(n))
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n - 1 - i]) for i in range(n // 2)])
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    rounds += [[(b, a) for a, b in rnd] for rnd in rounds]
    start = date(2023, 8, 12)
    matches = []
    for k, rnd in enumerate(rounds * 2):
        day = (start + timedelta(days=7 * k + (90 if k >= len(rounds) else 0))).isoformat()
        for j, (h, a) in enumerate(rnd):
            hg, ag = (k + h) % 4, (k + 2 * a + j) % 3
            matches.append({
                "date": day, "time": "15:00", "team1": TEAMS[h], "team2": TEAMS[a],
                "score": {"ft": [hg, ag]}, "_source_url": "fixture", "_source_sha256": "0" * 64,
            })
    return matches


def _features_by_event(rows):
    return {row.event_id: dict(row.features) for row in rows}


def test_rows_are_in_lifecycle_chronological_order_with_simultaneous_kickoffs():
    rows, metadata = soccer.build_training_rows(_season_matches(), competition="EPL")
    assert len(rows) == len(metadata) >= 500
    assert rows == sorted(rows, key=lambda r: (r.event_start_time, r.event_id))
    for row, meta in zip(rows, metadata):
        assert soccer._hash_json(meta["source_manifest"]) == row.source_manifest_sha256
    candidate = train_multiclass_candidate(
        rows, model_family="SOCCER_TEST", feature_names=soccer.FEATURE_NAMES,
        expected_classes=("HOME", "DRAW", "AWAY"), min_rows=500,
    )
    assert candidate.metrics.train_n > 0


def test_reordering_ties_does_not_change_any_row_features():
    matches = _season_matches()
    rows_a, _ = soccer.build_training_rows(matches, competition="EPL")
    rows_b, _ = soccer.build_training_rows(list(reversed(matches)), competition="EPL")
    assert _features_by_event(rows_a) == _features_by_event(rows_b)
    assert [r.event_id for r in rows_a] == [r.event_id for r in rows_b]
