from __future__ import annotations

from v17.prop_calibration_certification import PropCalibrationCertificationPolicy
from v17.prop_certification_runtime import (
    POLICY_REVIEW_REQUIRED,
    audit_artifact_certification,
    build_independent_observations,
)
from v17.prop_route_lifecycle import CALIBRATION_CERTIFIED_PASS, CALIBRATION_EVIDENCE_REQUIRED


def _artifact():
    return {
        "sport": "WNBA",
        "stat_type": "POINTS",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": "WNBA_PTS_FORWARD_V2",
        "artifact_checksum": "a" * 64,
        "calibrator_version": "WNBA_POINTS_PLATT_V2",
    }


def _prediction(prediction_id: str, direction: str, p: float, lb: float, model_ts: str):
    return {
        "prediction_id": prediction_id,
        "event_id": "game-1",
        "event_start_time": "2026-09-20T20:00:00+00:00",
        "model_timestamp": model_ts,
        "locked_at": model_ts,
        "source_snapshot_id": "11111111-1111-1111-1111-111111111111",
        "player": "Player A",
        "sport": "WNBA",
        "stat_type": "POINTS",
        "line": 19.5,
        "direction": direction,
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": "WNBA_PTS_FORWARD_V2",
        "model_artifact_checksum": "a" * 64,
        "feature_schema_version": "PROP_FEATURES_V1",
        "calibration_version": "WNBA_POINTS_PLATT_V2",
        "calibration_status": "PLATT_TIME_SPLIT_V1",
        "calibrated_probability": p,
        "calibrated_probability_lower_bound": lb,
    }


def test_direction_twins_and_refreshes_count_once_and_choose_latest_more():
    predictions = [
        _prediction("p1", "MORE", 0.60, 0.52, "2026-09-20T17:00:00+00:00"),
        _prediction("p2", "LESS", 0.40, 0.32, "2026-09-20T17:00:00+00:00"),
        _prediction("p3", "MORE", 0.65, 0.56, "2026-09-20T18:00:00+00:00"),
    ]
    outcomes = {
        "p1": {"hit": True, "push": False, "void": False},
        "p2": {"hit": False, "push": False, "void": False},
        "p3": {"hit": True, "push": False, "void": False},
    }
    rows, counts = build_independent_observations(predictions, outcomes)
    assert len(rows) == 1
    assert rows[0].prediction_id == "p3"
    assert rows[0].calibrated_probability == 0.65
    assert counts["settled_direction_row_n"] == 3
    assert counts["independent_settled_thesis_n"] == 1
    assert counts["duplicate_or_twin_row_n"] == 2


def test_unreviewed_route_policy_cannot_certify_even_with_large_eligible_cohort():
    rows = []
    for index in range(200):
        p = _prediction(
            f"p{index}", "MORE", 0.80, 0.70,
            f"2026-09-{(index % 9) + 10:02d}T17:00:00+00:00",
        )
        # Use the immutable observation builder across distinct events/players.
        p["event_id"] = f"game-{index}"
        p["player"] = f"player-{index}"
        outcome = {p["prediction_id"]: {"hit": index % 5 != 0, "push": False, "void": False}}
        built, _ = build_independent_observations([p], outcome)
        rows.extend(built)
    audit = audit_artifact_certification(_artifact(), rows)
    assert audit["status"] != CALIBRATION_CERTIFIED_PASS
    assert POLICY_REVIEW_REQUIRED in audit["blockers"]
    assert audit["promotion_package_ready"] is False
    assert audit["can_execute"] is False


def test_reviewed_explicit_policy_can_certify_exact_artifact_only():
    rows = []
    for index in range(200):
        p = _prediction(f"p{index}", "MORE", 0.80, 0.70, "2026-09-20T17:00:00+00:00")
        p["event_id"] = f"game-{index}"
        p["player"] = f"player-{index}"
        built, _ = build_independent_observations(
            [p], {p["prediction_id"]: {"hit": index % 5 != 0, "push": False, "void": False}}
        )
        rows.extend(built)
    policy = PropCalibrationCertificationPolicy(
        policy_id="WNBA_POINTS_FORWARD_CERT_POLICY_REVIEWED_V2",
        min_settled_predictions=200,
        max_brier_score=0.25,
        max_log_loss=0.70,
        max_ece=0.05,
        max_abs_calibration_bias=0.05,
        min_lower_bound_reliability_margin=0.0,
        eligible_calibration_statuses=("PLATT_TIME_SPLIT_V1",),
    )
    audit = audit_artifact_certification(
        _artifact(), rows, reviewed_policies={("WNBA", "POINTS"): policy}
    )
    assert audit["status"] == CALIBRATION_CERTIFIED_PASS
    assert audit["promotion_package_ready"] is True
    assert audit["settled_prediction_n"] == 200
    assert audit["can_execute"] is False


def test_phase_a_remains_evidence_required_even_if_registry_is_legacy_promoted():
    predictions = []
    outcomes = {}
    for index in range(200):
        p = _prediction(f"p{index}", "MORE", 0.60, 0.52, "2026-09-20T17:00:00+00:00")
        p["event_id"] = f"game-{index}"
        p["player"] = f"player-{index}"
        p["calibration_status"] = "PRECALIBRATION_SHRINKAGE"
        predictions.append(p)
        outcomes[p["prediction_id"]] = {"hit": index % 2 == 0, "push": False, "void": False}
    rows, _ = build_independent_observations(predictions, outcomes)
    audit = audit_artifact_certification(_artifact(), rows)
    assert audit["status"] == CALIBRATION_EVIDENCE_REQUIRED
    assert "PHASE_A_PRECALIBRATION_NOT_FORWARD_CERTIFICATION" in audit["blockers"]
