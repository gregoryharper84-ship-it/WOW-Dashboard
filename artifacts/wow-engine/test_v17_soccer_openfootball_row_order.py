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
                "_verified_event_start_utc": day + "T15:00:00+00:00",
                "_verified_start_receipt_id": "SYNTHETIC_TEST_FIXTURE_NOT_SOURCE_AUTHORIZATION",
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



def test_timezone_free_openfootball_time_cannot_order_fitted_candidate_rows():
    import pytest

    raw = _season_matches()
    for row in raw:
        row.pop("_verified_event_start_utc")
        row.pop("_verified_start_receipt_id")
    with pytest.raises(soccer.SoccerCandidateUnavailable) as exc:
        soccer.build_training_rows(raw, competition="EPL")
    assert exc.value.code == "SOCCER_KICKOFF_UTC_EVIDENCE_MISSING"


def test_verified_kickoff_must_be_explicitly_timezone_aware_and_linked_to_receipt():
    import pytest

    one = _season_matches()[0]
    one["_verified_event_start_utc"] = "2024-08-16T20:00:00"
    with pytest.raises(soccer.SoccerCandidateUnavailable) as exc:
        soccer._event_time(one)
    assert exc.value.code == "SOCCER_KICKOFF_UTC_INVALID"
    one["_verified_event_start_utc"] = "2024-08-16T20:00:00+02:00"
    assert soccer._event_time(one).isoformat() == "2024-08-16T18:00:00+00:00"
    one.pop("_verified_start_receipt_id")
    with pytest.raises(soccer.SoccerCandidateUnavailable) as exc:
        soccer._event_time(one)
    assert exc.value.code == "SOCCER_KICKOFF_UTC_EVIDENCE_MISSING"


def test_untrusted_openfootball_json_cannot_inject_fake_utc_proof(monkeypatch):
    import json

    payload = {
        "name": "untrusted synthetic source",
        "matches": [{
            "date": "2024-08-16", "time": "20:00", "team1": "A", "team2": "B",
            "score": {"ft": [1, 0]},
            "_verified_event_start_utc": "2024-08-16T20:00:00+00:00",
            "_verified_start_receipt_id": "forged-from-source-json",
        }],
    }

    class Reply:
        status_code = 200
        content = json.dumps(payload).encode("utf-8")

        def json(self):
            return payload

    monkeypatch.setattr(soccer.requests, "get", lambda *args, **kwargs: Reply())
    matches, _ = soccer.fetch_competition("en.1", seasons=("2024-25",))
    assert len(matches) == 1
    assert "_verified_event_start_utc" not in matches[0]
    assert "_verified_start_receipt_id" not in matches[0]
    with pytest.raises(soccer.SoccerCandidateUnavailable) as exc:
        soccer.build_training_rows(matches, competition="EPL")
    assert exc.value.code == "SOCCER_KICKOFF_UTC_EVIDENCE_MISSING"
