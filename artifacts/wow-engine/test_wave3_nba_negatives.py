"""Wave 3 FB-NBA-ML hard negatives: each probe must fail via the real validator."""

from __future__ import annotations

from v17.wave3_evidence_triad import compute_binding_proof
from v17.wave3_nba_evidence import (
    nba_hard_negative,
    verify_nba_event_orientations,
    verify_nba_lineup_freshness,
    verify_nba_lineup_evidence,
    verify_nba_snapshot_order,
)

SHA = "b" * 64


def _triad() -> dict:
    triad = {
        "sport": "NBA",
        "market": "MONEYLINE",
        "model_family": "NBA_FAMILY",
        "model_version": "v1",
        "fitted_artifact_sha256": SHA,
        "feature_schema_version": "NBA_SCHEMA_V1",
        "calibrator_artifact_sha256": SHA,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "pregame_corpus_digest": SHA,
        "settlement_basis": "OFFICIAL_FINAL",
    }
    triad["binding_proof"] = compute_binding_proof(triad)
    return triad


def test_negatives_fail_closed_through_real_validators():
    expectations = {
        "swapped_home_away": "NBA_HOME_AWAY_ORIENTATION_CONFLICT",
        "late_scratch": "NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION",
        "missing_tip_time": "NBA_EVENT_TIME_INVALID",
        "stale_source": "NBA_SOURCE_SNAPSHOT_AFTER_FEATURE",
        "missing_artifact": "EVIDENCE_TRIAD_FITTED_ARTIFACT_SHA256_MISSING",
        "missing_calibrator": "EVIDENCE_TRIAD_CALIBRATOR_ARTIFACT_SHA256_MISSING",
        "bounds_violation": "EVIDENCE_TRIAD_LOWER_ABOVE_CALIBRATED",
    }
    for kind, expected in expectations.items():
        probe = nba_hard_negative(kind, triad=_triad())
        assert probe["held_closed"] is True, kind
        assert expected in probe["blockers"], (kind, probe["blockers"])


def test_cross_sport_swap_negative_uses_real_triad():
    probe = nba_hard_negative("cross_sport_swap", triad={**_triad(), "sport": "NHL"})
    assert probe["held_closed"] is True
    assert "EVIDENCE_TRIAD_BINDING_MISMATCH" in probe["blockers"]
    assert "EVIDENCE_TRIAD_CROSS_SPORT_SWAP" in probe["blockers"]


def test_snapshot_order_validator_accepts_clean_lineage():
    result = verify_nba_snapshot_order(
        "2026-01-01T10:00:00Z",
        "2026-01-01T12:00:00Z",
        "2026-01-02T00:00:00Z",
    )
    assert result == {"ok": True, "blockers": []}


def test_orientation_validator_accepts_consistent_orientations():
    result = verify_nba_event_orientations([
        {"official_event_id": "e1", "home_team_id": "h1", "away_team_id": "a1"},
        {"official_event_id": "e2", "home_team_id": "h2", "away_team_id": "a2"},
    ])
    assert result == {"ok": True, "blockers": []}


def test_orientation_validator_rejects_same_team_mirror():
    result = verify_nba_event_orientations([
        {"official_event_id": "e1", "home_team_id": "h1", "away_team_id": "h1"},
    ])
    assert result["ok"] is False
    assert "NBA_EVENT_IDENTITY_INCOMPLETE" in result["blockers"]


def test_unchanged_lineup_after_hydration_passes_freshness():
    result = verify_nba_lineup_freshness(
        "2026-01-15T16:00:00Z",
        {"rotation_changed": False},
    )
    assert result == {"ok": True, "blockers": []}


def test_changed_lineup_before_hydration_passes_freshness():
    result = verify_nba_lineup_freshness(
        "2026-01-15T16:00:00Z",
        {
            "rotation_changed": True,
            "latest_material_update_ts": "2026-01-15T15:00:00Z",
        },
    )
    assert result == {"ok": True, "blockers": []}


def test_unknown_negative_kind_stays_open_with_typed_blocker():
    probe = nba_hard_negative("not_a_probe", triad=_triad())
    assert probe == {
        "kind": "not_a_probe",
        "held_closed": False,
        "blockers": ["UNKNOWN_NEGATIVE_KIND"],
    }


def test_lineup_evidence_at_tipoff_is_outside_prelock():
    # Exactly at tipoff is no longer pregame, for either independent source.
    record = {
        "official_event_ids": ["g1"],
        "source_id": "OFFICIAL_LINEUP_SOURCE",
        "evidence_as_of": "2026-01-02T00:00:00Z",
        "rotation_changed": False,
        "corroboration": {
            "source_id": "SECOND_INDEPENDENT_SOURCE",
            "evidence_as_of": "2026-01-01T16:30:00Z",
            "rotation_changed": False,
        },
    }
    kwargs = {
        "expected_ids": ["g1"],
        "source_snapshot_ts": "2026-01-01T16:00:00Z",
        "feature_ts": "2026-01-01T18:00:00Z",
        "tipoff_ts": "2026-01-02T00:00:00Z",
    }
    at_lock = verify_nba_lineup_evidence(record, **kwargs)
    assert at_lock["ok"] is False
    assert "NBA_LINEUP_AS_OF_AFTER_LOCK" in at_lock["blockers"]
    valid = {
        **record,
        "evidence_as_of": "2026-01-01T16:30:00Z",
    }
    assert verify_nba_lineup_evidence(valid, **kwargs)["ok"] is True
    corroboration_at_lock = {
        **valid,
        "corroboration": {
            **valid["corroboration"],
            "evidence_as_of": "2026-01-02T00:00:00Z",
        },
    }
    rejected = verify_nba_lineup_evidence(corroboration_at_lock, **kwargs)
    assert rejected["ok"] is False
    assert "NBA_LINEUP_AS_OF_AFTER_LOCK" in rejected["blockers"]


def test_missing_lineup_status_fails_closed_before_review():
    feature = "2026-01-01T18:00:00Z"
    missing_status = verify_nba_lineup_freshness(feature, {})
    assert missing_status["ok"] is False
    assert "NBA_LINEUP_EVIDENCE_MISSING" in missing_status["blockers"]

    full = {
        "official_event_ids": ["g1"],
        "source_id": "OFFICIAL_LINEUP_SOURCE",
        "evidence_as_of": "2026-01-01T16:30:00Z",
        "corroboration": {
            "source_id": "SECOND_INDEPENDENT_SOURCE",
            "evidence_as_of": "2026-01-01T16:30:00Z",
            "rotation_changed": False,
        },
    }
    kwargs = {
        "expected_ids": ["g1"],
        "source_snapshot_ts": "2026-01-01T16:00:00Z",
        "feature_ts": feature,
        "tipoff_ts": "2026-01-02T00:00:00Z",
    }
    missing_primary = verify_nba_lineup_evidence(full, **kwargs)
    assert missing_primary["ok"] is False
    assert "NBA_LINEUP_EVIDENCE_MISSING" in missing_primary["blockers"]

    complete = {**full, "rotation_changed": False}
    assert verify_nba_lineup_evidence(complete, **kwargs)["ok"] is True
    missing_corroboration = {
        **complete, "corroboration": {
            "source_id": "SECOND_INDEPENDENT_SOURCE",
            "evidence_as_of": "2026-01-01T16:30:00Z",
        },
    }
    decision = verify_nba_lineup_evidence(missing_corroboration, **kwargs)
    assert decision["ok"] is False
    assert "NBA_LINEUP_CORROBORATION_MISSING" in decision["blockers"]
