"""Governed ingress for validated WNBA prop *candidate* artifacts.

This boundary deliberately cannot certify, promote, activate, or publish a model.
It exists so the already-built chronological WNBA trainer can persist validated
candidate evidence in the production artifact registry without exposing a
service-role credential to GitHub Actions.

Frozen-source provenance fields are accepted as transport metadata and verified
when present. The registry table does not own those top-level convenience
fields, so they are stripped before insert; the durable validation_metrics
payload retains the complete source snapshot/provenance record.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency

CAN_EXECUTE = False
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
MODEL_FAMILY = "WNBA_PROP_POISSON_LOGGLM_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
ARTIFACT_FORMAT = "JSON_POISSON_LOGGLM_V1"
ALLOWED_STATS = frozenset({"POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_CODE = re.compile(r"^[0-9a-f]{40,64}$")
_FROZEN_TRANSPORT_FIELDS = (
    "numeric_canonicalization_decimals",
    "source_snapshot_bundle_id",
    "source_provider",
    "source_license_id",
    "source_attribution_required",
)


class WNBAPropCandidateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_identity: str
    model_family: str
    model_artifact_version: str = Field(min_length=1, max_length=160)
    calibrator_version: str = Field(min_length=1, max_length=160)
    sport: str
    stat_type: str
    feature_schema_version: str
    feature_transform_version: str = Field(min_length=1, max_length=160)
    specialist_version: str = Field(min_length=1, max_length=160)
    certification_id: str = Field(min_length=1, max_length=160)
    lifecycle_state: str
    training_dataset_hash: str
    training_code_sha: str
    artifact_checksum: str
    artifact_format: str
    artifact_payload: dict[str, Any]
    supported_line_min: float
    supported_line_max: float
    training_rows: int
    validation_metrics: dict[str, Any]
    certification_eligible: bool | None = None
    numeric_canonicalization_decimals: int | None = None
    source_snapshot_bundle_id: str | None = None
    source_provider: str | None = None
    source_license_id: str | None = None
    source_attribution_required: bool | None = None
    promoted: bool
    active: bool
    probability_publishable: bool
    can_execute: bool


class WNBAPropCandidateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifacts: list[WNBAPropCandidateArtifact] = Field(min_length=1, max_length=8)


def _canonical_checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_frozen_source_provenance(candidate: WNBAPropCandidateArtifact, blockers: list[str]) -> None:
    transport_present = any(getattr(candidate, field) is not None for field in _FROZEN_TRANSPORT_FIELDS)
    if not transport_present:
        return

    if not candidate.source_snapshot_bundle_id:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_BUNDLE_MISSING")
    if not candidate.source_provider:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_PROVIDER_MISSING")
    if not candidate.source_license_id:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_LICENSE_MISSING")
    if candidate.source_attribution_required is not True:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_ATTRIBUTION_NOT_ENFORCED")
    if candidate.numeric_canonicalization_decimals is None or candidate.numeric_canonicalization_decimals < 0:
        blockers.append("WNBA_PROP_NUMERIC_CANONICALIZATION_INVALID")

    source = candidate.validation_metrics.get("source")
    snapshot = source.get("source_snapshot") if isinstance(source, dict) else None
    if not isinstance(snapshot, dict):
        blockers.append("WNBA_PROP_FROZEN_SOURCE_METRICS_MISSING")
        return
    if str(snapshot.get("bundle_id") or "") != str(candidate.source_snapshot_bundle_id or ""):
        blockers.append("WNBA_PROP_FROZEN_SOURCE_BUNDLE_MISMATCH")
    if str(snapshot.get("provider") or "") != str(candidate.source_provider or ""):
        blockers.append("WNBA_PROP_FROZEN_SOURCE_PROVIDER_MISMATCH")
    if str(snapshot.get("license_id") or "") != str(candidate.source_license_id or ""):
        blockers.append("WNBA_PROP_FROZEN_SOURCE_LICENSE_MISMATCH")
    if snapshot.get("attribution_required") is not True:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_METRICS_ATTRIBUTION_NOT_ENFORCED")
    if snapshot.get("grants_model_capability") is not False:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_CANNOT_GRANT_MODEL_CAPABILITY")
    if snapshot.get("probability_publishable") is not False or snapshot.get("can_execute") is not False:
        blockers.append("WNBA_PROP_FROZEN_SOURCE_GOVERNANCE_INVALID")


def validate_candidate(candidate: WNBAPropCandidateArtifact) -> dict[str, Any]:
    row = candidate.model_dump()
    blockers: list[str] = []
    sport = str(candidate.sport).strip().upper()
    stat = str(candidate.stat_type).strip().upper()

    if candidate.provider_identity != PROVIDER_IDENTITY:
        blockers.append("WNBA_PROP_PROVIDER_IDENTITY_INVALID")
    if candidate.model_family != MODEL_FAMILY:
        blockers.append("WNBA_PROP_MODEL_FAMILY_INVALID")
    if sport != "WNBA":
        blockers.append("WNBA_PROP_SPORT_INVALID")
    if stat not in ALLOWED_STATS:
        blockers.append("WNBA_PROP_STAT_TYPE_UNSUPPORTED")
    if candidate.feature_schema_version != FEATURE_SCHEMA_VERSION:
        blockers.append("WNBA_PROP_FEATURE_SCHEMA_INVALID")
    if candidate.artifact_format != ARTIFACT_FORMAT:
        blockers.append("WNBA_PROP_ARTIFACT_FORMAT_INVALID")
    if candidate.lifecycle_state != "CANDIDATE":
        blockers.append("WNBA_PROP_CANDIDATE_LIFECYCLE_REQUIRED")
    if candidate.promoted or candidate.active or candidate.probability_publishable or candidate.can_execute:
        blockers.append("WNBA_PROP_CANDIDATE_GOVERNANCE_FLAGS_INVALID")
    if candidate.training_rows <= 0:
        blockers.append("WNBA_PROP_TRAINING_ROWS_INVALID")
    if candidate.supported_line_min < 0 or candidate.supported_line_max < candidate.supported_line_min:
        blockers.append("WNBA_PROP_SUPPORTED_LINE_RANGE_INVALID")
    if not _HEX64.fullmatch(candidate.training_dataset_hash):
        blockers.append("WNBA_PROP_TRAINING_DATASET_HASH_INVALID")
    if not _HEX_CODE.fullmatch(candidate.training_code_sha):
        blockers.append("WNBA_PROP_TRAINING_CODE_SHA_INVALID")
    if not _HEX64.fullmatch(candidate.artifact_checksum):
        blockers.append("WNBA_PROP_ARTIFACT_CHECKSUM_INVALID")
    elif _canonical_checksum(candidate.artifact_payload) != candidate.artifact_checksum:
        blockers.append("WNBA_PROP_ARTIFACT_CHECKSUM_MISMATCH")

    payload_family = str(candidate.artifact_payload.get("model_family") or "")
    payload_stat = str(candidate.artifact_payload.get("stat_type") or "").upper()
    if payload_family != MODEL_FAMILY or payload_stat != stat:
        blockers.append("WNBA_PROP_ARTIFACT_PAYLOAD_IDENTITY_INVALID")

    metrics = candidate.validation_metrics
    if str(metrics.get("validation_status") or "").upper() != "PASS":
        blockers.append("WNBA_PROP_VALIDATION_NOT_PASS")
    if list(metrics.get("blockers") or []):
        blockers.append("WNBA_PROP_VALIDATION_BLOCKERS_PRESENT")
    if metrics.get("can_execute") is not False or metrics.get("probability_publishable") is not False:
        blockers.append("WNBA_PROP_VALIDATION_GOVERNANCE_FLAGS_INVALID")
    if candidate.certification_eligible is not True:
        blockers.append("WNBA_PROP_OFFLINE_CERTIFICATION_ELIGIBILITY_NOT_PASS")

    _validate_frozen_source_provenance(candidate, blockers)

    if blockers:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WNBA_PROP_CANDIDATE_INVALID",
                "stat_type": stat,
                "blockers": sorted(set(blockers)),
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    # Registry schema contains neither certification_eligible nor the frozen
    # source transport convenience fields. Their durable evidence is already
    # present inside validation_metrics.source.source_snapshot.
    row.pop("certification_eligible", None)
    for field in _FROZEN_TRANSPORT_FIELDS:
        row.pop(field, None)
    row["sport"] = "WNBA"
    row["stat_type"] = stat
    row["lifecycle_state"] = "CANDIDATE"
    row["promoted"] = False
    row["active"] = False
    row["probability_publishable"] = False
    row["can_execute"] = False
    return row


def register_candidates(db: Any, batch: WNBAPropCandidateBatch) -> dict[str, Any]:
    registered: list[dict[str, Any]] = []
    seen_routes: set[str] = set()

    for candidate in batch.artifacts:
        row = validate_candidate(candidate)
        stat = row["stat_type"]
        if stat in seen_routes:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "WNBA_PROP_DUPLICATE_STAT_ROUTE",
                    "stat_type": stat,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        seen_routes.add(stat)

        existing_result = (
            db.table("wow_prop_fitted_model_artifacts")
            .select("artifact_id,model_artifact_version,artifact_checksum,lifecycle_state,active,promoted")
            .eq("provider_identity", PROVIDER_IDENTITY)
            .eq("model_artifact_version", row["model_artifact_version"])
            .limit(1)
            .execute()
        )
        existing = (existing_result.data or [None])[0]
        if existing is not None:
            if str(existing.get("artifact_checksum") or "") != row["artifact_checksum"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "WNBA_PROP_ARTIFACT_VERSION_COLLISION",
                        "model_artifact_version": row["model_artifact_version"],
                        "probability_publishable": False,
                        "can_execute": False,
                    },
                )
            registered.append({
                "stat_type": stat,
                "model_artifact_version": row["model_artifact_version"],
                "artifact_id": existing.get("artifact_id"),
                "status": "ALREADY_REGISTERED_IDENTICAL",
                "lifecycle_state": existing.get("lifecycle_state"),
            })
            continue

        inserted = db.table("wow_prop_fitted_model_artifacts").insert(row).execute()
        persisted = (inserted.data or [{}])[0]
        registered.append({
            "stat_type": stat,
            "model_artifact_version": row["model_artifact_version"],
            "artifact_id": persisted.get("artifact_id"),
            "status": "CANDIDATE_REGISTERED",
            "lifecycle_state": "CANDIDATE",
        })

    return {
        "status": "CANDIDATE_REGISTRATION_COMPLETE",
        "sport": "WNBA",
        "model_family": MODEL_FAMILY,
        "registered": registered,
        "registered_n": len(registered),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_wnba_prop_candidate_registration_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/wnba-prop-candidates"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="registerWowV17WnbaPropCandidates",
    )
    def register(batch: WNBAPropCandidateBatch) -> dict[str, Any]:
        return register_candidates(db_client_fn(), batch)


__all__ = [
    "ALLOWED_STATS",
    "CAN_EXECUTE",
    "MODEL_FAMILY",
    "WNBAPropCandidateArtifact",
    "WNBAPropCandidateBatch",
    "install_wnba_prop_candidate_registration_route",
    "register_candidates",
    "validate_candidate",
]
