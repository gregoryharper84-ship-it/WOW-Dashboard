"""Server-side derivation/registration of the WNBA PRA composite candidate artifact.

The route derives only from the existing governed WNBA joint-component candidate.
It cannot certify, promote, activate, or publish the derived artifact.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import FastAPI, HTTPException

from github_actions_oidc import scout_route_auth_dependency
from v17.wnba_composite_fitted_candidate import (
    CONTROLLING_SPECIALIST,
    MODEL_FAMILY,
    WNBACompositeCandidateError,
    derive_candidate_payload,
)

CAN_EXECUTE = False
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
SOURCE_STAT = "FANTASY_SCORE"
SOURCE_MODEL_FAMILY_PREFIX = "WNBA_FANTASY_SCORE_EMPIRICAL_RESIDUAL"
ARTIFACT_FORMAT = "JSON_JOINT_EMPIRICAL_RESIDUAL_V1"
FEATURE_TRANSFORM_VERSION = "WNBA_PRA_FROM_JOINT_COMPONENT_RESIDUAL_V1"
SPECIALIST_VERSION = f"{CONTROLLING_SPECIALIST}@1"


def _canonical_checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_row(db: Any) -> dict[str, Any]:
    result = (
        db.table("wow_prop_fitted_model_artifacts")
        .select(
            "artifact_id,provider_identity,model_family,model_artifact_version,"
            "artifact_checksum,artifact_payload,training_dataset_hash,training_code_sha,"
            "feature_schema_version,feature_transform_version,training_rows,validation_metrics,"
            "lifecycle_state,promoted,active,probability_publishable,can_execute,created_at"
        )
        .eq("sport", "WNBA")
        .eq("stat_type", SOURCE_STAT)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    row = (result.data or [None])[0]
    if not isinstance(row, dict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WNBA_COMPOSITE_SOURCE_ARTIFACT_MISSING",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    blockers: list[str] = []
    if row.get("provider_identity") != PROVIDER_IDENTITY:
        blockers.append("WNBA_COMPOSITE_SOURCE_PROVIDER_INVALID")
    if not str(row.get("model_family") or "").startswith(SOURCE_MODEL_FAMILY_PREFIX):
        blockers.append("WNBA_COMPOSITE_SOURCE_MODEL_FAMILY_INVALID")
    if not isinstance(row.get("artifact_payload"), dict):
        blockers.append("WNBA_COMPOSITE_SOURCE_PAYLOAD_INVALID")
    if not row.get("artifact_checksum"):
        blockers.append("WNBA_COMPOSITE_SOURCE_CHECKSUM_MISSING")
    if not row.get("training_dataset_hash") or not row.get("training_code_sha"):
        blockers.append("WNBA_COMPOSITE_SOURCE_PROVENANCE_MISSING")
    if row.get("probability_publishable") is not False or row.get("can_execute") is not False:
        blockers.append("WNBA_COMPOSITE_SOURCE_GOVERNANCE_INVALID")
    if blockers:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WNBA_COMPOSITE_SOURCE_ARTIFACT_INVALID",
                "blockers": blockers,
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return row


def build_candidate_row(source: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = derive_candidate_payload(
            source["artifact_payload"],
            source_model_artifact_version=str(source["model_artifact_version"]),
            source_artifact_checksum=str(source["artifact_checksum"]),
            source_training_dataset_hash=str(source["training_dataset_hash"]),
            source_training_code_sha=str(source["training_code_sha"]),
        )
    except (KeyError, WNBACompositeCandidateError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WNBA_COMPOSITE_CANDIDATE_DERIVATION_FAILED",
                "error_type": type(exc).__name__,
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    checksum = _canonical_checksum(payload)
    version = f"WNBA_PRA_JOINT_RESIDUAL_CANDIDATE_V1_{checksum[:12]}"
    metrics = {
        "validation_status": "CANDIDATE_DERIVED_FROM_STRICTLY_PRIOR_JOINT_RESIDUALS",
        "candidate_only": True,
        "source_artifact_id": source.get("artifact_id"),
        "source_model_artifact_version": source.get("model_artifact_version"),
        "source_artifact_checksum": source.get("artifact_checksum"),
        "source_training_rows": source.get("training_rows"),
        "source_validation_metrics": {
            "train_rows": (source.get("validation_metrics") or {}).get("train_rows"),
            "calibration_rows": (source.get("validation_metrics") or {}).get("calibration_rows"),
            "untouched_test_rows": (source.get("validation_metrics") or {}).get("untouched_test_rows"),
        },
        "joint_residual_sampling": True,
        "independent_component_multiplication_used": False,
        "calibration_status": "BLOCKED_NO_PRA_EXACT_LINE_CALIBRATION_ARTIFACT",
        "certification_status": "CANDIDATE_ONLY",
        "blockers": [
            "WNBA_COMPOSITE_EXACT_LINE_CALIBRATION_MISSING",
            "WNBA_COMPOSITE_FORWARD_COHORT_NOT_CERTIFIED",
            "WNBA_COMPOSITE_CANONICAL_ACTION_CANARY_MISSING",
        ],
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    return {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": version,
        "calibrator_version": "UNAVAILABLE_CANDIDATE_ONLY",
        "sport": "WNBA",
        "stat_type": "PRA",
        "feature_schema_version": str(source.get("feature_schema_version") or "PROP_FEATURES_V1"),
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "certification_id": f"CANDIDATE-NOT-CERTIFIED-{checksum[:16]}",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": str(source["training_dataset_hash"]),
        "training_code_sha": str(source["training_code_sha"]),
        "artifact_checksum": checksum,
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": payload,
        "supported_line_min": None,
        "supported_line_max": None,
        "training_rows": int(source.get("training_rows") or 0),
        "validation_metrics": metrics,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
        "candidate_research_active": True,
    }


def derive_and_register_candidate(db: Any) -> dict[str, Any]:
    source = _source_row(db)
    row = build_candidate_row(source)

    existing_result = (
        db.table("wow_prop_fitted_model_artifacts")
        .select(
            "artifact_id,model_artifact_version,artifact_checksum,lifecycle_state,"
            "active,promoted,probability_publishable,can_execute"
        )
        .eq("provider_identity", PROVIDER_IDENTITY)
        .eq("model_artifact_version", row["model_artifact_version"])
        .limit(1)
        .execute()
    )
    existing = (existing_result.data or [None])[0]
    if isinstance(existing, dict):
        if str(existing.get("artifact_checksum") or "") != row["artifact_checksum"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WNBA_COMPOSITE_ARTIFACT_VERSION_COLLISION",
                    "model_artifact_version": row["model_artifact_version"],
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        return {
            "status": "ALREADY_REGISTERED_IDENTICAL",
            "artifact_id": existing.get("artifact_id"),
            "model_artifact_version": existing.get("model_artifact_version"),
            "lifecycle_state": existing.get("lifecycle_state"),
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    inserted = db.table("wow_prop_fitted_model_artifacts").insert(row).execute()
    persisted = (inserted.data or [{}])[0]
    return {
        "status": "WNBA_COMPOSITE_CANDIDATE_REGISTERED",
        "artifact_id": persisted.get("artifact_id"),
        "model_artifact_version": row["model_artifact_version"],
        "model_family": MODEL_FAMILY,
        "stat_type": "PRA",
        "lifecycle_state": "CANDIDATE",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_wnba_composite_candidate_registration_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/wnba-composite-candidate/derive"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="deriveWowV17WnbaCompositeCandidate",
    )
    def derive() -> dict[str, Any]:
        return derive_and_register_candidate(db_client_fn())


__all__ = [
    "ARTIFACT_FORMAT",
    "CAN_EXECUTE",
    "MODEL_FAMILY",
    "build_candidate_row",
    "derive_and_register_candidate",
    "install_wnba_composite_candidate_registration_route",
]
