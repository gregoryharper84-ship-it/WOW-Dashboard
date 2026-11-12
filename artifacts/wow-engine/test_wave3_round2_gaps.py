"""Wave 3 round-2 review gaps: binding proofs, ID identity, lineage wiring,
lower-bound rejection, NHL non-empty partitions + chronology."""

from __future__ import annotations

import pytest

from v17.wave3_evidence_triad import compute_binding_proof, verify_triad
from v17.wave3_nba_evidence import (
    nba_evidence_preflight,
    verify_chronology_disjoint,
    verify_nba_snapshot_order,
)
from v17.wave3_nfl_ml_readiness import GradedRow
from v17.wave3_nhl_evidence import (
    nhl_evidence_preflight,
    reconcile_partition_accounting,
)

SHA = "b" * 64
OTHER = "c" * 64


def _triad(sport: str) -> dict:
    return {
        "sport": sport,
        "market": "MONEYLINE",
        "model_family": f"{sport}_FAMILY",
        "model_version": "v1",
        "fitted_artifact_sha256": SHA,
        "feature_schema_version": f"{sport}_SCHEMA_V1",
        "calibrator_artifact_sha256": SHA,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "pregame_corpus_digest": SHA,
        "settlement_basis": "OFFICIAL_FINAL",
    }


def _bound(sport: str) -> dict:
    triad = _triad(sport)
    triad["binding_proof"] = compute_binding_proof(triad)
    return triad


# --- Gap 5: artifact-to-corpus binding proof ---


def test_binding_proof_round_trip_passes():
    triad = _bound("NBA")
    verdict = verify_triad(
        {**triad, "expected_sport": "NBA", "expected_market": "MONEYLINE"},
        require_binding=True,
    )
    assert verdict.ok is True


def test_binding_proof_missing_fails_when_required():
    verdict = verify_triad(_triad("NBA"), require_binding=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_BINDING_UNPROVEN" in verdict.blockers


def test_binding_proof_mismatch_detects_tampered_digest():
    triad = _bound("NBA")
    tampered = {**triad, "pregame_corpus_digest": OTHER}
    verdict = verify_triad(tampered, require_binding=True)
    assert verdict.ok is False
    assert "EVIDENCE_TRIAD_BINDING_MISMATCH" in verdict.blockers


def test_unrequired_triad_without_proof_keeps_prior_behavior():
    assert verify_triad(_triad("NFL")).ok is True


# --- Gap 1: NBA identity on official event IDs ---


def test_duplicate_event_ids_rejected_even_with_distinct_timestamps():
    result = verify_chronology_disjoint(
        ["2026-01-15T00:00:00Z", "2026-01-16T00:00:00Z"],
        ["2026-02-01T00:00:00Z"],
        ["2026-03-01T00:00:00Z"],
        train_ids=["game-1", "game-1"],
        calibration_ids=["game-2"],
        holdout_ids=["game-3"],
    )
    assert result["ok"] is False
    assert "NBA_PARTITION_EVENT_REUSE" in result["blockers"]


def test_cross_partition_id_reuse_rejected():
    result = verify_chronology_disjoint(
        ["2026-01-01T00:00:00Z"],
        ["2026-02-01T00:00:00Z"],
        ["2026-03-01T00:00:00Z"],
        train_ids=["game-1"],
        calibration_ids=["game-1"],
        holdout_ids=["game-3"],
    )
    assert "NBA_PARTITION_EVENT_REUSE" in result["blockers"]


def test_partial_id_coverage_fails_closed():
    result = verify_chronology_disjoint(
        ["2026-01-01T00:00:00Z"],
        ["2026-02-01T00:00:00Z"],
        ["2026-03-01T00:00:00Z"],
        train_ids=["game-1"],
    )
    assert result["ok"] is False
    assert "NBA_EVENT_ID_COVERAGE_INCOMPLETE" in result["blockers"]


def test_clean_ids_and_times_pass_disjointness():
    result = verify_chronology_disjoint(
        ["2026-01-01T00:00:00Z"],
        ["2026-02-01T00:00:00Z"],
        ["2026-03-01T00:00:00Z"],
        train_ids=["game-1"],
        calibration_ids=["game-2"],
        holdout_ids=["game-3"],
    )
    assert result == {"ok": True, "blockers": []}


# --- Gap 2: preflight enforces snapshot lineage when supplied ---


def test_preflight_rejects_stale_snapshot_lineage():
    verdict = nba_evidence_preflight(
        triad=_bound("NBA"),
        train=["2026-01-01T00:00:00Z"],
        calibration=["2026-02-01T00:00:00Z"],
        holdout=["2026-03-01T00:00:00Z"],
        settlement_includes_ot=True,
        post_tipoff_features_used=False,
        train_ids=["g1"],
        calibration_ids=["g2"],
        holdout_ids=["g3"],
        source_snapshot_ts="2026-01-01T12:00:00Z",
        feature_ts="2026-01-01T10:00:00Z",
        tipoff_ts="2026-01-02T00:00:00Z",
    )
    assert verdict["decision"] == "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"
    assert "NBA_SOURCE_SNAPSHOT_AFTER_FEATURE" in verdict["blockers"]


def test_preflight_accepts_clean_lineage_with_binding_and_ids():
    verdict = nba_evidence_preflight(
        triad=_bound("NBA"),
        train=["2026-01-01T00:00:00Z"],
        calibration=["2026-02-01T00:00:00Z"],
        holdout=["2026-03-01T00:00:00Z"],
        settlement_includes_ot=True,
        post_tipoff_features_used=False,
        train_ids=["g1"],
        calibration_ids=["g2"],
        holdout_ids=["g3"],
        source_snapshot_ts="2026-01-01T10:00:00Z",
        feature_ts="2026-01-01T12:00:00Z",
        tipoff_ts="2026-01-02T00:00:00Z",
    )
    assert verdict["decision"] == "READY_FOR_INDEPENDENT_MODEL_REVIEW"
    assert verdict["probability_publishable"] is False
    assert verdict["can_execute"] is False


def test_preflight_without_binding_proof_holds():
    verdict = nba_evidence_preflight(
        triad=_triad("NBA"),
        train=["2026-01-01T00:00:00Z"],
        calibration=["2026-02-01T00:00:00Z"],
        holdout=["2026-03-01T00:00:00Z"],
        settlement_includes_ot=True,
        post_tipoff_features_used=False,
    )
    assert verdict["decision"] == "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"
    assert "EVIDENCE_TRIAD_BINDING_UNPROVEN" in verdict["blockers"]


def test_snapshot_order_validator_accepts_clean_lineage():
    assert verify_nba_snapshot_order(
        "2026-01-01T10:00:00Z", "2026-01-01T12:00:00Z", "2026-01-02T00:00:00Z"
    ) == {"ok": True, "blockers": []}


# --- Gap 3: lower bound above forecast is rejected at row construction ---


def test_graded_row_rejects_lower_bound_above_forecast():
    with pytest.raises(ValueError, match="lower_bound must not exceed forecast"):
        GradedRow(forecast=0.60, outcome=1, lower_bound=0.70)


def test_graded_row_accepts_equal_lower_and_forecast():
    row = GradedRow(forecast=0.60, outcome=0, lower_bound=0.60)
    assert row.lower_bound == 0.60


# --- Gap 4: NHL non-empty partitions and verified chronology ---


def _nhl_ids():
    train = [f"G{i:05d}" for i in range(3676)]
    cal = [f"C{i:05d}" for i in range(1226)]
    test = [f"T{i:05d}" for i in range(1226)]
    return train, cal, test


def test_nhl_empty_calibration_and_test_partitions_fail():
    train, _, _ = _nhl_ids()
    accounting = reconcile_partition_accounting(train, [], [])
    assert accounting["ok"] is False
    assert "NHL_CALIBRATION_PARTITION_EMPTY" in accounting["blockers"]
    assert "NHL_TEST_PARTITION_EMPTY" in accounting["blockers"]


def test_nhl_chronology_overlap_fails_when_times_supplied():
    train, cal, test = _nhl_ids()
    accounting = reconcile_partition_accounting(
        train,
        cal,
        test,
        train_times=["2024-10-01T00:00:00Z"] * len(train),
        calibration_times=["2024-09-01T00:00:00Z"] * len(cal),
        test_times=["2025-04-01T00:00:00Z"] * len(test),
    )
    assert accounting["ok"] is False
    assert "NHL_TRAIN_CALIBRATION_OVERLAP" in accounting["blockers"]


def test_nhl_partial_time_coverage_fails_closed():
    train, cal, test = _nhl_ids()
    accounting = reconcile_partition_accounting(
        train, cal, test, train_times=["2024-10-01T00:00:00Z"] * len(train)
    )
    assert accounting["ok"] is False
    assert "NHL_PARTITION_CHRONOLOGY_UNVERIFIED" in accounting["blockers"]


def test_nhl_preflight_holds_until_binding_and_goalie_evidence():
    train, cal, test = _nhl_ids()
    verdict = nhl_evidence_preflight(
        triad=_bound("NHL"),
        train_ids=train,
        calibration_ids=cal,
        test_ids=test,
        settlement_includes_ot_shootout=True,
        goalie_source_permitted=True,
        goalie_second_corroboration=True,
        goalie_confirmation_prelock=True,
        train_times=["2024-10-01T00:00:00Z"] * len(train),
        calibration_times=["2025-01-01T00:00:00Z"] * len(cal),
        test_times=["2025-04-01T00:00:00Z"] * len(test),
    )
    assert verdict["decision"] == "READY_FOR_INDEPENDENT_MODEL_REVIEW"
    assert verdict["probability_publishable"] is False
    assert verdict["can_execute"] is False
