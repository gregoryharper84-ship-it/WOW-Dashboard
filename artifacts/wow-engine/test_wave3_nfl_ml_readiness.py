"""Wave 3 FB-NFL-ML + triad tests: 95-row HOLD, metric replay, bounds."""

from __future__ import annotations

import math

import pytest

from v17.wave3_evidence_triad import (
    compute_binding_proof,
    verify_probability_bounds,
    verify_triad,
)
from v17.wave3_nfl_ml_readiness import (
    GradedRow,
    build_readiness_ledger,
    certification_package,
    compute_metrics,
)

SHA = "a" * 64


def _triad_base() -> dict:
    return {
        "sport": "NFL",
        "market": "MONEYLINE",
        "model_family": "NFL_EVENT_LOGREG_PLATT_V1",
        "model_version": "v1",
        "fitted_artifact_sha256": SHA,
        "feature_schema_version": "NFL_EVENT_PREGAME_PRIOR_V1",
        "calibrator_artifact_sha256": SHA,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "pregame_corpus_digest": SHA,
        "settlement_basis": "OFFICIAL_FINAL_HOME_WIN",
    }


def _bound_triad() -> dict:
    base = _triad_base()
    base["binding_proof"] = compute_binding_proof(base)
    return base


def _95_rows() -> list:
    # 58 wins of 95 at p=0.6554688685, lower=0.5995507854 mirrors the
    # Nov-11 validation observation shape (n=95, overconfident, gap>0).
    rows = []
    for i in range(95):
        rows.append(
            GradedRow(
                forecast=0.6554688685,
                outcome=1 if i < 58 else 0,
                lower_bound=0.5995507854,
            )
        )
    return rows


def test_95_rows_stay_do_not_certify_with_missing_to_100():
    ledger = build_readiness_ledger(_95_rows())
    assert ledger.valid_n == 95
    assert ledger.missing_to_100 == 5
    assert ledger.decision == "DO_NOT_CERTIFY_YET"
    assert ledger.blockers == ("FORWARD_GRADED_N_95_LT_100",)
    # Sub-minimum metrics are reported as diagnostic observation only.
    assert ledger.brier == pytest.approx(
        sum((0.6554688685 - (1 if i < 58 else 0)) ** 2 for i in range(95)) / 95
    )
    assert ledger.log_loss is not None and ledger.log_loss > 0
    assert ledger.calibration_bias == pytest.approx(0.6554688685 - 58 / 95)
    assert ledger.can_execute is False
    package = certification_package(ledger)
    assert package["certification_decision"] == "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"
    assert package["certified"] is False
    assert package["probability_publishable"] is False


def test_metric_replay_matches_hand_computation():
    metrics = compute_metrics(_95_rows())
    assert metrics["n"] == 95
    assert metrics["mean_forecast"] == pytest.approx(0.6554688685)
    assert metrics["observed_rate"] == pytest.approx(58 / 95)
    assert metrics["brier"] == pytest.approx(
        sum((0.6554688685 - (1 if i < 58 else 0)) ** 2 for i in range(95)) / 95
    )
    expected_ll = sum(
        -math.log(0.6554688685) if i < 58 else -math.log(1 - 0.6554688685)
        for i in range(95)
    ) / 95
    assert metrics["log_loss"] == pytest.approx(expected_ll)
    assert metrics["calibration_bias"] == pytest.approx(0.6554688685 - 58 / 95)
    assert metrics["lower_bound_gap"] == pytest.approx(58 / 95 - 0.5995507854)


def test_100_balanced_cohort_passes_but_never_self_certifies():
    rows = [GradedRow(forecast=0.70, outcome=1 if i < 70 else 0, lower_bound=0.60) for i in range(100)]
    ledger = build_readiness_ledger(rows)
    assert ledger.missing_to_100 == 0
    package = certification_package(ledger)
    assert package["certified"] is False
    assert package["probability_publishable"] is False


def test_triad_rejects_missing_stale_and_cross_sport():
    base = _bound_triad()
    assert verify_triad({**base, "expected_sport": "NFL"}).ok is True
    missing = dict(base)
    missing["calibrator_artifact_sha256"] = ""
    assert "EVIDENCE_TRIAD_CALIBRATOR_ARTIFACT_SHA256_MISSING" in verify_triad(missing).blockers
    malformed = dict(base)
    malformed["fitted_artifact_sha256"] = "xyz"
    assert "EVIDENCE_TRIAD_FITTED_ARTIFACT_SHA256_MALFORMED" in verify_triad(malformed).blockers
    assert "EVIDENCE_TRIAD_STALE_COMPONENT" in verify_triad({**base, "stale": True}).blockers
    assert "EVIDENCE_TRIAD_POSTSTART_CORPUS" in verify_triad({**base, "poststart_corpus": True}).blockers
    assert "EVIDENCE_TRIAD_CROSS_SPORT_SWAP" in verify_triad(
        {**base, "expected_sport": "NBA"}
    ).blockers


def test_probability_bounds_are_fail_closed():
    assert verify_probability_bounds(0.65, 0.59, 0.72).ok is True
    assert "EVIDENCE_TRIAD_LOWER_ABOVE_CALIBRATED" in verify_probability_bounds(
        0.65, 0.70, 0.80
    ).blockers
    assert "EVIDENCE_TRIAD_CALIBRATED_ABOVE_UPPER" in verify_probability_bounds(
        0.85, 0.59, 0.80
    ).blockers
    assert verify_probability_bounds(1.5, 0.59, 0.72).ok is False
