"""Artifact-backed governed MLB 1IP specialist scorer.

The specialist supports both the existing aggregate empirical artifact and the
new player-conditioned BF-mixture artifact. Registry lifecycle state remains the
serving authority: this module does not promote artifacts or mutate the registry.

Sporting-probability publication is intentionally independent of market/payout
readiness. Once the certified artifact returns a valid calibrated package, the
probability remains publishable for probability-only output. Missing market or
payout evidence is carried as a downstream blocker and may hold edge/EV/card
qualification, but it must not erase the sporting probability.
"""
from __future__ import annotations

from typing import Any

from mlb_1ip_empirical_pmf import (
    CALIBRATOR_VERSION as AGGREGATE_CALIBRATOR_VERSION,
    MODEL_FAMILY as AGGREGATE_MODEL_FAMILY,
    score_empirical_pmf,
)
from mlb_1ip_player_conditioned import (
    CALIBRATOR_VERSION as PLAYER_CALIBRATOR_VERSION,
    MODEL_FAMILY as PLAYER_MODEL_FAMILY,
    score_player_conditioned_1ip,
)
from mlb_1ip_specialist import (
    CONTROLLING_SPECIALIST,
    classify_lineup_evidence,
)

CAN_EXECUTE = False
EXPECTED_FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
SUPPORTED_MODEL_FAMILIES = {AGGREGATE_MODEL_FAMILY, PLAYER_MODEL_FAMILY}


def _certified_lines(artifact_record: dict[str, Any]) -> tuple[float, ...]:
    metrics = artifact_record.get("validation_metrics") or {}
    raw = metrics.get("validated_lines")
    if not isinstance(raw, list) or not raw:
        raise ValueError("MLB_1IP_CERTIFIED_LINE_SUPPORT_MISSING")
    try:
        lines = tuple(float(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("MLB_1IP_CERTIFIED_LINE_SUPPORT_INVALID") from exc
    if len(set(lines)) != len(lines):
        raise ValueError("MLB_1IP_CERTIFIED_LINE_SUPPORT_INVALID")
    return lines


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
    """Score one MLB 1IP row from the registry-resolved certified artifact."""
    model_family = str(artifact_record.get("model_family") or "")
    if model_family not in SUPPORTED_MODEL_FAMILIES:
        raise ValueError("MLB_1IP_CERTIFIED_ARTIFACT_FAMILY_INVALID")
    if artifact_record.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
        raise ValueError("MLB_1IP_CERTIFIED_ARTIFACT_NOT_READY")
    if artifact_record.get("feature_schema_version") != EXPECTED_FEATURE_SCHEMA_VERSION:
        raise ValueError("MLB_1IP_CERTIFIED_ARTIFACT_FEATURE_SCHEMA_INVALID")

    supported_lines = _certified_lines(artifact_record)
    requested_line = float(line_value)
    if requested_line not in supported_lines:
        return {
            "controlling_specialist": CONTROLLING_SPECIALIST,
            "model_family": model_family,
            "model_evaluated": False,
            "terminal_label": "REJECT_OOD",
            "code": "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT",
            "blockers": ["LINE_OUTSIDE_CERTIFIED_SUPPORT"],
            "supported_lines": list(supported_lines),
            "supported_line_min": min(supported_lines),
            "supported_line_max": max(supported_lines),
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
            "model_family": model_family,
            "lineup_evidence_state": state,
            "model_evaluated": False,
            "terminal_label": "REJECT_DATA_QUALITY",
            "code": "MANDATORY_EVENT_TREE_INPUTS_UNOBTAINABLE_AFTER_APPROVED_ATTEMPTS",
            "blockers": reasons,
            "final_refresh_required": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    if model_family == PLAYER_MODEL_FAMILY:
        conditioning = (failure_path_prior or {}).get("player_conditioning") or {}
        pitcher_id = conditioning.get("pitcher_id")
        recent_bf = conditioning.get("recent_1ip_batters_faced")
        if not pitcher_id or not isinstance(recent_bf, list) or len(recent_bf) < 5:
            return {
                "controlling_specialist": CONTROLLING_SPECIALIST,
                "model_family": model_family,
                "lineup_evidence_state": state,
                "model_evaluated": False,
                "terminal_label": "REJECT_DATA_QUALITY",
                "code": "MLB_1IP_PLAYER_CONDITIONING_INPUTS_INSUFFICIENT",
                "blockers": ["PLAYER_CONDITIONING_HISTORY_REQUIRED"],
                "final_refresh_required": False,
                "probability_publishable": False,
                "can_execute": False,
            }
        scored = score_player_conditioned_1ip(
            artifact_payload=artifact_record.get("artifact_payload") or {},
            pitcher_id=int(pitcher_id),
            recent_batters_faced=recent_bf,
            line_value=requested_line,
            side=side,
        )
        calibration_method = PLAYER_CALIBRATOR_VERSION
    else:
        scored = score_empirical_pmf(
            artifact_record.get("artifact_payload") or {},
            line_value=requested_line,
            side=side,
        )
        calibration_method = AGGREGATE_CALIBRATOR_VERSION

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
        "calibration_method": calibration_method,
        "certified_supported_lines": list(supported_lines),
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "final_refresh_required": state != "OFFICIAL_CONFIRMED",
        "blockers": blockers,
        "probability_status": "PASS",
        "probability_publishable": True,
        "can_execute": False,
    }
