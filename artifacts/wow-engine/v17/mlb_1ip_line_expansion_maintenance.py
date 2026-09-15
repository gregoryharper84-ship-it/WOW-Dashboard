"""Governed MLB 1IP exact-line certification maintenance.

This control-plane operation can only expand the active, already-certified
player-conditioned MLB 1IP artifact from the prior six-line grid to the
independently validated eight-line grid containing 14.5 and 16.5. It does not
refit, recalibrate, interpolate, alter probabilities, publish a betting pick, or
enable execution.

The operation is deliberately fail-closed and idempotent. A validated evidence
attestation must be checked into the repository first. The current active
artifact is cloned with identical fitted payload/checksum and a new
certification/version identity; only exact-line certification metadata changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from mlb_1ip_player_conditioned import MODEL_FAMILY as PLAYER_MODEL_FAMILY

CAN_EXECUTE = False
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
SPORT = "MLB"
STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
AGGREGATE_ARTIFACT_CHECKSUM = "3fd0fbd528f7b07556dfb93c41674a4f486e5d4fab70b33bfdac33fc7b4ef7a6"
BF_ARTIFACT_CHECKSUM = "6dfda83565d5af658408261c49645ee83345afa75a08590d8a2af2dc9a46ae73"
OLD_VALIDATED_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
EXPANDED_VALIDATED_LINES = (11.5, 13.5, 14.5, 15.5, 16.5, 17.5, 19.5, 21.5)
CERTIFICATION_FILE = (
    Path(__file__).resolve().parent
    / "certification"
    / "mlb_1ip_line_expansion_20260916.json"
)

_REGISTRY_COLUMNS = (
    "provider_identity",
    "model_family",
    "model_artifact_version",
    "calibrator_version",
    "sport",
    "stat_type",
    "feature_schema_version",
    "feature_transform_version",
    "specialist_version",
    "certification_id",
    "lifecycle_state",
    "training_dataset_hash",
    "training_code_sha",
    "artifact_checksum",
    "artifact_format",
    "artifact_payload",
    "supported_line_min",
    "supported_line_max",
    "training_rows",
    "validation_metrics",
    "promoted",
    "active",
    "probability_publishable",
    "can_execute",
)


def _blocked(code: str, *, detail: Any = None, rollback_status: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "BLOCKED",
        "code": code,
        "promotion_attempted": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    if detail is not None:
        out["detail"] = detail
    if rollback_status is not None:
        out["rollback_status"] = rollback_status
    return out


def _rows(result: Any) -> list[dict[str, Any]]:
    data = getattr(result, "data", None)
    return list(data or [])


def _validated_lines(metrics: Any) -> tuple[float, ...]:
    if not isinstance(metrics, dict):
        return ()
    try:
        return tuple(float(v) for v in (metrics.get("validated_lines") or []))
    except (TypeError, ValueError):
        return ()


def _load_attestation() -> dict[str, Any]:
    if not CERTIFICATION_FILE.exists():
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_ATTESTATION_MISSING")
    return json.loads(CERTIFICATION_FILE.read_text(encoding="utf-8"))


def validate_attestation(report: dict[str, Any]) -> None:
    if report.get("purpose") != "MLB_1IP_EXACT_LINE_EXPANSION_14_5_16_5_INDEPENDENT_EARLY_2026":
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_ATTESTATION_PURPOSE_INVALID")
    if report.get("validation_passed") is not True or list(report.get("validation_failures") or []):
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_VALIDATION_NOT_PASSED")
    if report.get("promotion_status") != "REVIEW_REQUIRED":
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_REVIEW_STATE_INVALID")
    if report.get("probability_publishable") is not False or report.get("rank_eligible") is not False:
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_PUBLICATION_GOVERNANCE_INVALID")
    if report.get("can_execute") is not False:
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_EXECUTION_GOVERNANCE_INVALID")
    if tuple(float(v) for v in report.get("expansion_lines") or []) != (14.5, 16.5):
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_LINES_INVALID")
    lineage = report.get("validation_lineage") or {}
    if lineage.get("aggregate_artifact_checksum") != AGGREGATE_ARTIFACT_CHECKSUM:
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_AGGREGATE_LINEAGE_INVALID")
    if lineage.get("bf_artifact_checksum") != BF_ARTIFACT_CHECKSUM:
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_BF_LINEAGE_INVALID")
    if not str(lineage.get("validation_lineage_hash") or ""):
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_LINEAGE_HASH_MISSING")
    if str(lineage.get("sample_policy") or "") != "EARLIEST_400_WARMUP_PLUS_700_TEST_FINAL_GAMES_2026":
        raise RuntimeError("MLB_1IP_LINE_EXPANSION_SAMPLE_POLICY_INVALID")

    gates = report.get("gates") or {}
    line_results = report.get("line_results") or {}
    for line in (14.5, 16.5):
        result = line_results.get(str(line)) or {}
        player = result.get("player_conditioned") or {}
        n = int(player.get("n") or 0)
        brier_delta = result.get("brier_delta")
        ece = player.get("ece")
        auc_gain = result.get("auc_gain")
        probability_std = player.get("probability_std")
        if n < int(gates.get("min_mature_rows_per_line") or 0):
            raise RuntimeError(f"MLB_1IP_LINE_{line}_SAMPLE_GATE_FAILED")
        if brier_delta is None or float(brier_delta) > float(gates.get("max_brier_regression")):
            raise RuntimeError(f"MLB_1IP_LINE_{line}_BRIER_GATE_FAILED")
        if ece is None or float(ece) > float(gates.get("max_ece")):
            raise RuntimeError(f"MLB_1IP_LINE_{line}_ECE_GATE_FAILED")
        if auc_gain is None or float(auc_gain) < float(gates.get("min_auc_gain")):
            raise RuntimeError(f"MLB_1IP_LINE_{line}_AUC_GATE_FAILED")
        if probability_std is None or float(probability_std) < float(gates.get("min_probability_std")):
            raise RuntimeError(f"MLB_1IP_LINE_{line}_DISCRIMINATION_GATE_FAILED")
        if int(player.get("unique_probability_count") or 0) < 2:
            raise RuntimeError(f"MLB_1IP_LINE_{line}_PROBABILITY_COLLAPSE")
        if int(result.get("lower_bound_unique_count") or 0) < 2:
            raise RuntimeError(f"MLB_1IP_LINE_{line}_LOWER_BOUND_COLLAPSE")


def _active_route(db: Any) -> list[dict[str, Any]]:
    return _rows(
        db.table("wow_prop_fitted_model_artifacts")
        .select("*")
        .eq("provider_identity", PROVIDER_IDENTITY)
        .eq("sport", SPORT)
        .eq("stat_type", STAT_TYPE)
        .eq("feature_schema_version", FEATURE_SCHEMA_VERSION)
        .eq("active", True)
        .eq("promoted", True)
        .limit(3)
        .execute()
    )


def _target_row(current: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    lineage = report["validation_lineage"]
    lineage_hash = str(lineage["validation_lineage_hash"])
    row = {key: current.get(key) for key in _REGISTRY_COLUMNS}
    current_version = str(current.get("model_artifact_version") or PLAYER_MODEL_FAMILY)
    row["model_artifact_version"] = f"{current_version}_EXACT14_5_16_5_{lineage_hash[:12]}"[:160]
    row["certification_id"] = f"PROP-CERT-MLB-1IP-LINES8-{lineage_hash[:16]}"
    row["lifecycle_state"] = "PROSPECTIVE_CERTIFIED"
    metrics = dict(current.get("validation_metrics") or {})
    metrics["validated_lines"] = list(EXPANDED_VALIDATED_LINES)
    metrics["exact_line_policy"] = "CERTIFIED_EXACT_LINES_ONLY_NO_INTERPOLATION"
    metrics["line_expansion_validation"] = report
    metrics["line_expansion_review"] = {
        "implementer_context": "CHATGPT_WOW_V17_ENGINEERING",
        "reviewer_context": "USER_GOVERNANCE_APPROVAL_FIX_AND_DEPLOY_2026_09_16",
        "verdict": "APPROVE_FOR_EXACT_LINE_CERTIFICATION_IF_VALIDATION_PASSES",
        "model_payload_changed": False,
        "calibration_transform_changed": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    row["validation_metrics"] = metrics
    row["supported_line_min"] = min(EXPANDED_VALIDATED_LINES)
    row["supported_line_max"] = max(EXPANDED_VALIDATED_LINES)
    row["promoted"] = True
    row["active"] = False
    row["probability_publishable"] = False
    row["can_execute"] = False
    return row


def run_mlb_1ip_line_expansion_maintenance(db: Any) -> dict[str, Any]:
    try:
        report = _load_attestation()
        validate_attestation(report)
    except Exception as exc:  # fail closed on absent/invalid evidence
        return _blocked(str(exc) or "MLB_1IP_LINE_EXPANSION_ATTESTATION_INVALID")

    active_rows = _active_route(db)
    if len(active_rows) != 1:
        return _blocked("MLB_1IP_ACTIVE_ARTIFACT_AMBIGUOUS", detail={"active_rows": len(active_rows)})
    current = active_rows[0]
    if current.get("model_family") != PLAYER_MODEL_FAMILY:
        return _blocked("MLB_1IP_PLAYER_CONDITIONED_ARTIFACT_NOT_ACTIVE")
    if current.get("probability_publishable") is not False or current.get("can_execute") is not False:
        return _blocked("MLB_1IP_ACTIVE_ARTIFACT_GOVERNANCE_INVALID")
    payload = current.get("artifact_payload") or {}
    if payload.get("model_family") != PLAYER_MODEL_FAMILY:
        return _blocked("MLB_1IP_ACTIVE_PAYLOAD_MODEL_FAMILY_INVALID")
    if payload.get("aggregate_artifact_checksum") != AGGREGATE_ARTIFACT_CHECKSUM:
        return _blocked("MLB_1IP_ACTIVE_AGGREGATE_ARTIFACT_MISMATCH")

    current_lines = _validated_lines(current.get("validation_metrics"))
    report_hash = str(report["validation_lineage"]["validation_lineage_hash"])
    current_expansion = (current.get("validation_metrics") or {}).get("line_expansion_validation") or {}
    current_hash = str((current_expansion.get("validation_lineage") or {}).get("validation_lineage_hash") or "")
    if current_lines == EXPANDED_VALIDATED_LINES and current_hash == report_hash:
        return {
            "status": "ALREADY_ACTIVE",
            "code": "MLB_1IP_LINE_EXPANSION_ALREADY_CERTIFIED",
            "model_artifact_version": current.get("model_artifact_version"),
            "validated_lines": list(EXPANDED_VALIDATED_LINES),
            "promotion_attempted": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    # Do not silently expand from a surprising prior exact-line contract.
    if current_lines and current_lines != OLD_VALIDATED_LINES:
        return _blocked(
            "MLB_1IP_CURRENT_LINE_CERTIFICATION_UNEXPECTED",
            detail={"current_validated_lines": list(current_lines)},
        )

    target = _target_row(current, report)
    version = target["model_artifact_version"]
    existing = _rows(
        db.table("wow_prop_fitted_model_artifacts")
        .select("*")
        .eq("provider_identity", PROVIDER_IDENTITY)
        .eq("model_artifact_version", version)
        .limit(2)
        .execute()
    )
    if len(existing) > 1:
        return _blocked("MLB_1IP_LINE_EXPANSION_TARGET_AMBIGUOUS")
    if existing:
        staged = existing[0]
        if staged.get("artifact_checksum") != current.get("artifact_checksum"):
            return _blocked("MLB_1IP_LINE_EXPANSION_VERSION_COLLISION")
        if _validated_lines(staged.get("validation_metrics")) != EXPANDED_VALIDATED_LINES:
            return _blocked("MLB_1IP_LINE_EXPANSION_STAGED_METADATA_INVALID")
    else:
        try:
            inserted = db.table("wow_prop_fitted_model_artifacts").insert(target).execute()
            staged_rows = _rows(inserted)
            if len(staged_rows) != 1:
                return _blocked("MLB_1IP_LINE_EXPANSION_STAGE_INSERT_FAILED")
            staged = staged_rows[0]
        except Exception as exc:
            return _blocked("MLB_1IP_LINE_EXPANSION_STAGE_INSERT_FAILED", detail=type(exc).__name__)

    staged_id = staged.get("artifact_id")
    current_id = current.get("artifact_id")
    if not staged_id or not current_id:
        return _blocked("MLB_1IP_LINE_EXPANSION_ARTIFACT_ID_MISSING")

    promotion_attempted = False
    try:
        promotion_attempted = True
        db.table("wow_prop_fitted_model_artifacts").update({"active": False}).eq(
            "artifact_id", current_id
        ).eq("active", True).execute()
        db.table("wow_prop_fitted_model_artifacts").update({"active": True}).eq(
            "artifact_id", staged_id
        ).eq("active", False).execute()
        verified = _active_route(db)
        if len(verified) != 1 or verified[0].get("artifact_id") != staged_id:
            raise RuntimeError("MLB_1IP_LINE_EXPANSION_POST_ACTIVATION_VERIFY_FAILED")
    except Exception as exc:
        rollback_status = "NOT_REQUIRED"
        try:
            db.table("wow_prop_fitted_model_artifacts").update({"active": False}).eq(
                "artifact_id", staged_id
            ).execute()
            db.table("wow_prop_fitted_model_artifacts").update({"active": True}).eq(
                "artifact_id", current_id
            ).execute()
            restored = _active_route(db)
            rollback_status = (
                "RESTORED"
                if len(restored) == 1 and restored[0].get("artifact_id") == current_id
                else "RESTORE_VERIFY_FAILED"
            )
        except Exception:
            rollback_status = "RESTORE_FAILED"
        out = _blocked(
            "MLB_1IP_LINE_EXPANSION_ACTIVATION_FAILED",
            detail=type(exc).__name__,
            rollback_status=rollback_status,
        )
        out["promotion_attempted"] = promotion_attempted
        return out

    return {
        "status": "PROMOTED",
        "code": "MLB_1IP_EXACT_LINE_EXPANSION_CERTIFIED",
        "prior_model_artifact_version": current.get("model_artifact_version"),
        "model_artifact_version": staged.get("model_artifact_version") or version,
        "model_family": PLAYER_MODEL_FAMILY,
        "artifact_checksum": current.get("artifact_checksum"),
        "model_payload_changed": False,
        "calibration_transform_changed": False,
        "validated_lines": list(EXPANDED_VALIDATED_LINES),
        "validation_lineage_hash": report_hash,
        "promotion_attempted": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def install_mlb_1ip_line_expansion_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/mlb-1ip-line-expansion-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17Mlb1ipLineExpansionMaintenance",
    )
    def run_maintenance() -> dict[str, Any]:
        return run_mlb_1ip_line_expansion_maintenance(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "EXPANDED_VALIDATED_LINES",
    "OLD_VALIDATED_LINES",
    "install_mlb_1ip_line_expansion_maintenance_route",
    "run_mlb_1ip_line_expansion_maintenance",
    "validate_attestation",
]
