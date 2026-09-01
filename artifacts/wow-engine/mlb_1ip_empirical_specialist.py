"""Artifact-backed governed MLB 1IP specialist scorer.

This scorer preserves the existing lineup/starter evidence-state contract while
replacing the legacy Gaussian event-tree probability calculation with the
independently shadow-validated empirical conditional-total-pitches PMF whenever
that exact certified artifact family is supplied by the registry.

It performs no artifact lookup or database mutation itself. The caller must
supply the already-resolved artifact record from the governed registry.
"""
from __future__ import annotations

from typing import Any

from mlb_1ip_empirical_pmf import (
    CALIBRATOR_VERSION,
    MODEL_FAMILY,
    score_empirical_pmf,
)
from mlb_1ip_specialist import (
    CONTROLLING_SPECIALIST,
    classify_lineup_evidence,
)

CAN_EXECUTE = False


def score_mlb_1ip_empirical(
    *,
    artifact_record: dict[str, Any],
    starter_status: str,
    official_lineup_status: str,
    projected_top_four: Any,
    line_value: float,
    side: str,
    failure_path_prior: dict[str, Any] | None = None,
    market_evidence_present: bool = True,
) -> dict[str, Any]:
    """Score one MLB 1IP row from a registry-resolved empirical artifact."""
    if artifact_record.get("model_family") != MODEL_FAMILY:
        raise ValueError("MLB_1IP_CERTIFIED_ARTIFACT_FAMILY_INVALID")
    if artifact_record.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
        raise ValueError("MLB_1IP_CERTIFIED_ARTIFACT_NOT_READY")

    line_min = float(artifact_record.get("supported_line_min"))
    line_max = float(artifact_record.get("supported_line_max"))
    if not line_min <= float(line_value) <= line_max:
        return {
            "controlling_specialist": CONTROLLING_SPECIALIST,
            "model_family": MODEL_FAMILY,
            "model_evaluated": False,
            "terminal_label": "REJECT_OOD",
            "code": "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT",
            "blockers": ["LINE_OUTSIDE_CERTIFIED_SUPPORT"],
            "supported_line_min": line_min,
            "supported_line_max": line_max,
            "final_refresh_required": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    state, completeness, reasons = classify_lineup_evidence(
        starter_status=starter_status,
        official_lineup_status=official_lineup_status,
        projected_top_four=projected_top_four,
    )
    if state == "INSUFFICIENT_TO_RECONSTRUCT":
        return {
            "controlling_specialist": CONTROLLING_SPECIALIST,
            "model_family": MODEL_FAMILY,
            "lineup_evidence_state": state,
            "model_evaluated": False,
            "terminal_label": "REJECT_DATA_QUALITY",
            "code": "MANDATORY_EVENT_TREE_INPUTS_UNOBTAINABLE_AFTER_APPROVED_ATTEMPTS",
            "blockers": reasons,
            "final_refresh_required": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    scored = score_empirical_pmf(
        artifact_record.get("artifact_payload") or {},
        line_value=float(line_value),
        side=side,
    )
    blockers = list(reasons)
    if failure_path_prior is not None and failure_path_prior.get("status") == "MATERIAL_UNRESOLVED":
        blockers.append("PITCHER_FAILURE_PATH_PRIOR_UNRESOLVED")
    if not market_evidence_present:
        blockers.append("MARKET_DATA_UNAVAILABLE")

    return {
        **scored,
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "model_artifact_version": artifact_record.get("model_artifact_version"),
        "model_artifact_checksum": artifact_record.get("artifact_checksum"),
        "certification_id": artifact_record.get("certification_id"),
        "lineup_evidence_state": state,
        "lineup_evidence_completeness": completeness,
        "model_evaluated": True,
        "raw_probability": scored["selected_probability"],
        "calibrated_probability": scored["selected_probability"],
        "calibrated_probability_lower_bound": scored["lower_bound"],
        "calibrated_probability_upper_bound": scored["upper_bound"],
        "calibration_method": CALIBRATOR_VERSION,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "final_refresh_required": state != "OFFICIAL_CONFIRMED",
        "blockers": blockers,
        "probability_publishable": False,
        "can_execute": False,
    }
