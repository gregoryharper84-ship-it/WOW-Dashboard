"""Wave 3 ID-anchored evidence: keyed event times, per-event prelock, corroboration.

Chronology must be keyed by official event ID (no positional pairing),
prelock lineage must hold per event (not cohort-wide scalars), and lineup
evidence must be corroborated by a second independent source.
"""

from __future__ import annotations

from v17.wave3_nba_evidence import (
    nba_evidence_preflight,
    verify_nba_id_time_binding,
    verify_nba_lineup_evidence,
    verify_nba_per_event_prelock,
)

SHA = "b" * 64
OTHER = "c" * 64


def _times(ids, start="2026-01-01T00:00:00Z"):
    return {v: start for v in ids}


# --- Keyed event-time binding ---


def test_binding_missing_id_fails():
    result = verify_nba_id_time_binding(
        ["g1"], ["g2"], ["g3"], {"g1": "2026-01-01T00:00:00Z", "g2": "2026-02-01T00:00:00Z"}
    )
    assert result["ok"] is False
    assert "NBA_EVENT_TIME_BINDING_MISSING" in result["blockers"]


def test_binding_unrelated_id_fails():
    result = verify_nba_id_time_binding(
        ["g1"],
        ["g2"],
        ["g3"],
        {
            "g1": "2026-01-01T00:00:00Z",
            "g2": "2026-02-01T00:00:00Z",
            "g3": "2026-03-01T00:00:00Z",
            "zzz": "2026-04-01T00:00:00Z",
        },
    )
    assert result["ok"] is False
    assert "NBA_EVENT_TIME_UNRELATED_EVENT" in result["blockers"]


def test_binding_invalid_time_fails():
    result = verify_nba_id_time_binding(
        ["g1"],
        ["g2"],
        ["g3"],
        {
            "g1": "2026-01-01T00:00:00Z",
            "g2": "not-a-time",
            "g3": "2026-03-01T00:00:00Z",
        },
    )
    assert result["ok"] is False
    assert "NBA_EVENT_TIME_INVALID" in result["blockers"]


def test_binding_overlap_fails():
    result = verify_nba_id_time_binding(
        ["g1"],
        ["g2"],
        ["g3"],
        {
            "g1": "2026-02-15T00:00:00Z",
            "g2": "2026-02-01T00:00:00Z",
            "g3": "2026-03-01T00:00:00Z",
        },
    )
    assert result["ok"] is False
    assert "NBA_TRAIN_CALIBRATION_OVERLAP" in result["blockers"]


def test_binding_happy_path_passes():
    result = verify_nba_id_time_binding(
        ["g1"],
        ["g2"],
        ["g3"],
        {
            "g1": "2026-01-01T00:00:00Z",
            "g2": "2026-02-01T00:00:00Z",
            "g3": "2026-03-01T00:00:00Z",
        },
    )
    assert result == {"ok": True, "blockers": []}


def test_binding_rejects_non_mapping():
    result = verify_nba_id_time_binding(["g1"], ["g2"], ["g3"], None)
    assert result == {"ok": False, "blockers": ["NBA_EVENT_TIME_BINDING_REQUIRED"]}


# --- Per-event prelock ---


def _record(event_id, start="2026-01-02T00:00:00Z"):
    return {
        "official_event_id": event_id,
        "home_team_id": "h1",
        "away_team_id": "a1",
        "event_start_time": start,
    }


def test_per_event_prelock_missing_start_fails():
    record = _record("g1")
    del record["event_start_time"]
    result = verify_nba_per_event_prelock([record], feature_ts="2026-01-01T12:00:00Z")
    assert result == {"ok": False, "blockers": ["NBA_PER_EVENT_PRELOCK_UNPROVEN"]}


def test_per_event_prelock_stale_start_fails():
    result = verify_nba_per_event_prelock(
        [_record("g1", start="2026-01-01T10:00:00Z")],
        feature_ts="2026-01-01T12:00:00Z",
    )
    assert result["ok"] is False
    assert "NBA_PER_EVENT_PRELOCK_UNPROVEN" in result["blockers"]


def test_per_event_prelock_happy_path_passes():
    result = verify_nba_per_event_prelock(
        [_record("g1"), _record("g2", start="2026-01-03T00:00:00Z")],
        feature_ts="2026-01-01T12:00:00Z",
    )
    assert result == {"ok": True, "blockers": []}


# --- Lineup corroboration ---


def _lineup(**overrides):
    lineup = {
        "rotation_changed": False,
        "latest_material_update_ts": None,
        "official_event_ids": ["g1", "g2", "g3"],
        "source_id": "nba-official-injury-report",
        "evidence_as_of": "2026-01-01T11:00:00Z",
        "corroboration": {
            "source_id": "nba-team-pr-wire",
            "evidence_as_of": "2026-01-01T11:30:00Z",
            "rotation_changed": False,
        },
    }
    lineup.update(overrides)
    return lineup


def _lineup_kwargs():
    return {
        "expected_ids": ["g1", "g2", "g3"],
        "source_snapshot_ts": "2026-01-01T10:00:00Z",
        "feature_ts": "2026-01-01T12:00:00Z",
        "tipoff_ts": "2026-01-02T00:00:00Z",
    }


def test_lineup_missing_corroboration_fails():
    lineup = _lineup()
    del lineup["corroboration"]
    result = verify_nba_lineup_evidence(lineup, **_lineup_kwargs())
    assert result["ok"] is False
    assert "NBA_LINEUP_CORROBORATION_MISSING" in result["blockers"]


def test_lineup_same_source_corroboration_fails():
    lineup = _lineup()
    lineup["corroboration"]["source_id"] = "nba-official-injury-report"
    result = verify_nba_lineup_evidence(lineup, **_lineup_kwargs())
    assert result["ok"] is False
    assert "NBA_LINEUP_CORROBORATION_NOT_INDEPENDENT" in result["blockers"]


def test_lineup_corroboration_conflict_fails():
    lineup = _lineup()
    lineup["corroboration"]["rotation_changed"] = True
    lineup["corroboration"]["latest_material_update_ts"] = "2026-01-01T11:45:00Z"
    result = verify_nba_lineup_evidence(lineup, **_lineup_kwargs())
    assert result["ok"] is False
    assert "NBA_LINEUP_CORROBORATION_CONFLICT" in result["blockers"]


def test_corroborated_lineup_passes():
    result = verify_nba_lineup_evidence(_lineup(), **_lineup_kwargs())
    assert result == {"ok": True, "blockers": []}
