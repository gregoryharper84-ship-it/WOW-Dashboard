"""Fail-closed independent arithmetic replay; never a model promotion or release gate."""
from __future__ import annotations

from datetime import datetime, timezone
import math

from v17.nfl_exact_version_evidence_replay import audit_exact_version

MODEL = "NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb"
ARTIFACT = "7fee1209-0bed-4bd0-aa0f-fc7a881eff80"
DIGEST = "a" * 64


def _case():
    rows = []
    for i in range(100):
        y = 1 if i < 60 else 0
        p = 0.6
        rows.append({
            "official_event_id": f"NFL-SYNTHETIC-{i}",
            "event_prediction_id": f"pred-{i}",
            "model_version": MODEL,
            "model_artifact_id": ARTIFACT,
            "artifact_checksum": DIGEST,
            "source_game_id": f"NFL-SYNTHETIC-{i}",
            "schema_version": "NFL_FORWARD_SHADOW_GRADE_V1",
            "calibrated_probability": p,
            "calibrated_lower_bound": 0.5,
            "calibrated_upper_bound": 0.7,
            "outcome": y,
            "brier": (p-y)**2,
            "log_loss": -(y*math.log(p)+(1-y)*math.log(1-p)),
            "event_start_time_utc": "2026-11-15T20:00:00Z",
            "prediction_created_at": "2026-11-15T19:00:00Z",
            "can_execute": False,
        })
    health = {
        "graded_n": 100,
        "minimum_forward_required": 100,
        "status": "PASS",
        "certification_recommendation": "FORWARD_EVIDENCE_PASS",
        "blockers": [],
        "brier": 0.24,
        "log_loss": -(0.6*math.log(0.6)+0.4*math.log(0.4)),
        "ece": 0.0,
        "calibration_bias": 0.0,
        "lower_bound_reliability_gap": 0.1,
    }
    return rows, health


def _audit(rows, health):
    return audit_exact_version(
        rows,
        expected_model_version=MODEL,
        expected_model_artifact_id=ARTIFACT,
        expected_artifact_checksum=DIGEST,
        health=health,
    )


def test_consistent_exact_version_is_evidence_only_not_authorization():
    rows, health = _case()
    result = _audit(rows, health)
    assert result["status"] == "EVIDENCE_CONSISTENT"
    assert result["verified_grade_count"] == 100
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert result["independent_certification_required"] is True


def test_rejects_mixed_model_version_even_when_global_health_passes():
    rows, health = _case()
    rows[0]["model_version"] = "DIFFERENT_MODEL"
    assert _audit(rows, health)["code"] == "NFL_EXACT_VERSION_COHORT_MISMATCH"


def test_rejects_different_artifact_same_version():
    rows, health = _case()
    rows[0]["artifact_checksum"] = "b" * 64
    assert _audit(rows, health)["code"] == "NFL_EXACT_VERSION_COHORT_MISMATCH"


def test_rejects_global_pass_with_nonmatching_calibration_metrics():
    rows, health = _case()
    health["brier"] = 0.1
    assert _audit(rows, health)["code"] == "NFL_FORWARD_METRIC_REPLAY_MISMATCH"


def test_rejects_false_graded_count_while_global_health_passes():
    rows, health = _case()
    health["graded_n"] = 107
    assert _audit(rows, health)["code"] == "NFL_FORWARD_HEALTH_COHORT_MISMATCH"


def test_rejects_duplicate_or_post_kickoff_prediction():
    rows, health = _case()
    rows[1]["official_event_id"] = rows[0]["official_event_id"]
    assert _audit(rows, health)["code"] == "NFL_FORWARD_GRADE_IDENTITY_INVALID"
    rows, health = _case()
    rows[0]["prediction_created_at"] = rows[0]["event_start_time_utc"]
    assert _audit(rows, health)["code"] == "NFL_FORWARD_CHRONOLOGY_INVALID"


def test_rejects_tampered_grade_arithmetic_or_null_finite_probability():
    rows, health = _case()
    rows[0]["brier"] = 0.0
    assert _audit(rows, health)["code"] == "NFL_FORWARD_GRADE_ARITHMETIC_MISMATCH"
    rows, health = _case()
    rows[0]["calibrated_probability"] = float("nan")
    assert _audit(rows, health)["code"] == "NFL_FORWARD_GRADE_INVALID"


def test_rejects_less_than_100_or_unapproved_health():
    rows, health = _case()
    assert _audit(rows[:99], health)["code"] == "NFL_FORWARD_EVIDENCE_INSUFFICIENT"
    rows, health = _case()
    health["certification_recommendation"] = "DO_NOT_CERTIFY_YET"
    assert _audit(rows, health)["code"] == "NFL_FORWARD_HEALTH_NOT_PASS"
