"""Governed exact-route production-registration audit for V17 props.

This module binds the universal forward-certification result to the authoritative
artifact registry, runtime model/calibration adapters, exact hydration route, and
an immutable real Action-canary receipt. It is audit-only: it never promotes an
artifact, registers a model, writes a canary, changes publication state, or grants
execution authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from v17.prop_capability_manifest import DECLARED_PROP_LANES, normalize_prop_sport
from v17.prop_certification_runtime import PropCertificationAuditRequest, run_prop_certification_audit
from v17.prop_route_lifecycle import (
    CALIBRATION_CERTIFIED_PASS,
    CERTIFICATION_APPROVED,
    FEATURE_SCHEMA_VERSION,
    PropRouteLifecycleEvidence,
    assess_prop_route,
    runtime_registration_snapshot,
)

CAN_EXECUTE = False
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
CANARY_TABLE = "wow_prop_action_canary_receipts"
CANARY_OPERATION_IDS = frozenset({"scoreWowPickRequest", "scoreWowV17PickRequest"})


class PropProductionRegistrationAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ReviewedCertificationRelease:
    """Exact reviewed release record, committed through code review.

    This is intentionally empty in production until a route has an actual
    CALIBRATION_CERTIFIED_PASS packet and independent review. A future release PR
    adds one exact immutable identity/evidence hash here; there is no sport-wide
    wildcard or runtime request parameter that can manufacture approval.
    """
    sport: str
    stat_type: str
    feature_schema_version: str
    model_family: str
    model_artifact_version: str
    artifact_checksum: str
    calibrator_version: str
    calibration_evidence_hash: str
    certification_id: str
    certification_review_status: str
    deterministic_replay_ready: bool
    source_provenance_ready: bool
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# No current exact route has passed the new universal forward-certification gate.
# Legacy/prospective certification IDs remain valid for their existing serving
# contracts but are not silently upgraded into this stricter release registry.
REVIEWED_CERTIFICATION_RELEASES: dict[tuple[str, str, str, str], ReviewedCertificationRelease] = {}


def _release_key(sport: str, stat_type: str, model_artifact_version: str, checksum: str) -> tuple[str, str, str, str]:
    return (
        normalize_prop_sport(sport),
        str(stat_type or "").strip().upper(),
        str(model_artifact_version or "").strip(),
        str(checksum or "").strip(),
    )


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _finite_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _release_matches_registry(artifact: Mapping[str, Any], release: ReviewedCertificationRelease | None) -> bool:
    if release is None or release.can_execute is not False:
        return False
    return (
        normalize_prop_sport(str(artifact.get("sport") or "")) == normalize_prop_sport(release.sport)
        and str(artifact.get("stat_type") or "").upper() == release.stat_type.upper()
        and str(artifact.get("feature_schema_version") or "") == release.feature_schema_version
        and str(artifact.get("model_family") or "") == release.model_family
        and str(artifact.get("model_artifact_version") or "") == release.model_artifact_version
        and str(artifact.get("artifact_checksum") or "") == release.artifact_checksum
        and str(artifact.get("calibrator_version") or "") == release.calibrator_version
        and str(artifact.get("certification_id") or "") == release.certification_id
    )


def validate_action_canary_receipt(receipt: Mapping[str, Any], artifact: Mapping[str, Any], release: ReviewedCertificationRelease) -> tuple[bool, tuple[str, ...]]:
    """Validate an immutable receipt from a real canonical Action invocation."""
    blockers: list[str] = []
    if receipt.get("can_execute") is not False:
        blockers.append("CANARY_CAN_EXECUTE_MUST_BE_FALSE")
    if str(receipt.get("action_operation_id") or "") not in CANARY_OPERATION_IDS:
        blockers.append("CANARY_OPERATION_NOT_CANONICAL")
    exact = (
        normalize_prop_sport(str(receipt.get("sport") or "")),
        str(receipt.get("stat_type") or "").upper(),
        str(receipt.get("feature_schema_version") or ""),
        str(receipt.get("model_family") or ""),
        str(receipt.get("model_artifact_version") or ""),
        str(receipt.get("artifact_checksum") or ""),
        str(receipt.get("calibrator_version") or ""),
        str(receipt.get("certification_id") or ""),
    )
    expected = (
        normalize_prop_sport(str(artifact.get("sport") or "")),
        str(artifact.get("stat_type") or "").upper(),
        str(artifact.get("feature_schema_version") or ""),
        str(artifact.get("model_family") or ""),
        str(artifact.get("model_artifact_version") or ""),
        str(artifact.get("artifact_checksum") or ""),
        str(artifact.get("calibrator_version") or ""),
        release.certification_id,
    )
    if exact != expected:
        blockers.append("CANARY_EXACT_ARTIFACT_IDENTITY_MISMATCH")
    raw = _finite_probability(receipt.get("raw_model_probability"))
    calibrated = _finite_probability(receipt.get("calibrated_probability"))
    lower = _finite_probability(receipt.get("calibrated_lower_bound"))
    if raw is None or calibrated is None or lower is None or lower > calibrated:
        blockers.append("CANARY_GOVERNED_PROBABILITY_PACKAGE_INVALID")
    if not str(receipt.get("prediction_id") or "").strip():
        blockers.append("CANARY_IMMUTABLE_PREDICTION_ID_REQUIRED")
    if not str(receipt.get("immutable_receipt_hash") or "").strip():
        blockers.append("CANARY_IMMUTABLE_RECEIPT_HASH_REQUIRED")
    if str(receipt.get("reconciliation_status") or "").upper() != "PASS":
        blockers.append("CANARY_EXACT_ONCE_RECONCILIATION_REQUIRED")
    if str(receipt.get("calibration_evidence_hash") or "") != release.calibration_evidence_hash:
        blockers.append("CANARY_CERTIFICATION_EVIDENCE_HASH_MISMATCH")
    return not blockers, tuple(dict.fromkeys(blockers))


def _canary_receipts(db: Any, artifact: Mapping[str, Any], release: ReviewedCertificationRelease | None) -> list[dict[str, Any]]:
    if release is None:
        return []
    try:
        result = (
            db.table(CANARY_TABLE)
            .select("*")
            .eq("sport", str(artifact.get("sport")))
            .eq("stat_type", str(artifact.get("stat_type")))
            .eq("model_artifact_version", str(artifact.get("model_artifact_version")))
            .eq("artifact_checksum", str(artifact.get("artifact_checksum")))
            .eq("certification_id", release.certification_id)
            .order("created_at", desc=True)
            .limit(20).execute()
        )
        return _rows(result)
    except Exception:
        return []


def _artifact_rows(db: Any) -> list[dict[str, Any]]:
    fields = (
        "artifact_id,provider_identity,model_family,model_artifact_version,calibrator_version,"
        "sport,stat_type,feature_schema_version,specialist_version,certification_id,lifecycle_state,"
        "training_dataset_hash,training_code_sha,artifact_checksum,promoted,active,"
        "probability_publishable,can_execute,candidate_research_active"
    )
    return _rows(db.table("wow_prop_fitted_model_artifacts").select(fields).eq("provider_identity", PROVIDER).execute())


def _best_artifact_by_route(artifacts: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in artifacts:
        key = (normalize_prop_sport(str(row.get("sport"))), str(row.get("stat_type") or "").upper())
        grouped.setdefault(key, []).append(row)
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in grouped.items():
        selected[key] = sorted(
            rows,
            key=lambda row: (
                bool(row.get("active")), bool(row.get("promoted")),
                str(row.get("model_artifact_version") or ""),
            ),
        )[-1]
    return selected


def audit_registration_for_artifact(
    artifact: Mapping[str, Any],
    certification_audit: Mapping[str, Any] | None,
    *,
    release: ReviewedCertificationRelease | None,
    canary_receipts: list[Mapping[str, Any]],
) -> dict[str, Any]:
    sport = normalize_prop_sport(str(artifact.get("sport") or ""))
    stat = str(artifact.get("stat_type") or "").upper()
    capability = DECLARED_PROP_LANES.get((sport, stat))
    runtime = runtime_registration_snapshot(
        sport=sport,
        stat_type=stat,
        model_family=str(artifact.get("model_family") or ""),
        calibrator_version=str(artifact.get("calibrator_version") or ""),
    )
    calibration_pass = bool(certification_audit and certification_audit.get("status") == CALIBRATION_CERTIFIED_PASS)
    registry_release_match = _release_matches_registry(artifact, release)
    release_valid = bool(
        registry_release_match
        and release
        and release.certification_review_status == CERTIFICATION_APPROVED
        and release.deterministic_replay_ready
        and release.source_provenance_ready
        and certification_audit
        and str(certification_audit.get("evidence_hash") or "") == release.calibration_evidence_hash
    )
    canary_verified = False
    canary_blockers: tuple[str, ...] = ("CANONICAL_ACTION_CANARY_REQUIRED",)
    if release_valid and release:
        for receipt in canary_receipts:
            valid, blockers = validate_action_canary_receipt(receipt, artifact, release)
            if valid:
                canary_verified, canary_blockers = True, ()
                break
            canary_blockers = blockers

    # Promotion/active state counts for the new universal lifecycle only after
    # the registry carries the exact reviewed certification ID. A legacy
    # prospective certification remains valid for its legacy serving contract but
    # cannot be reused as the new forward-certification promotion.
    universal_promoted = bool(artifact.get("promoted")) and release_valid
    universal_active = bool(artifact.get("active")) and release_valid

    evidence = PropRouteLifecycleEvidence(
        sport=sport,
        stat_type=stat,
        feature_schema_version=str(artifact.get("feature_schema_version") or FEATURE_SCHEMA_VERSION),
        declared_route=(sport, stat) in DECLARED_PROP_LANES,
        controlling_specialist_ready=bool(capability and capability.controlling_specialist),
        model_build_exists=True,
        fitted_model_present=True,
        candidate_research_active=bool(artifact.get("candidate_research_active")),
        model_family=str(artifact.get("model_family") or ""),
        model_artifact_version=str(artifact.get("model_artifact_version") or ""),
        artifact_checksum=str(artifact.get("artifact_checksum") or ""),
        calibrator_version=str(artifact.get("calibrator_version") or ""),
        calibration_evidence_status=CALIBRATION_CERTIFIED_PASS if calibration_pass else str((certification_audit or {}).get("status") or "NOT_ASSESSED"),
        deterministic_replay_ready=bool(release_valid and release and release.deterministic_replay_ready),
        source_provenance_ready=bool(
            (release_valid and release and release.source_provenance_ready)
            or (artifact.get("training_dataset_hash") and artifact.get("training_code_sha"))
        ),
        certification_review_status=release.certification_review_status if release_valid and release else "NOT_ASSESSED",
        certification_id=release.certification_id if release_valid and release else None,
        lifecycle_state=str(artifact.get("lifecycle_state") or "") if release_valid else "CANDIDATE",
        promoted=universal_promoted,
        active=universal_active,
        model_adapter_registered=bool(runtime["model_adapter_registered"]),
        calibrator_adapter_registered=bool(runtime["calibrator_adapter_registered"]),
        hydration_registered=bool(runtime["hydration_route_registered"]),
        action_canary_verified=canary_verified,
    )
    assessed = assess_prop_route(evidence)
    return {
        **assessed.as_dict(),
        "model_family": artifact.get("model_family"),
        "model_artifact_version": artifact.get("model_artifact_version"),
        "artifact_checksum": artifact.get("artifact_checksum"),
        "calibrator_version": artifact.get("calibrator_version"),
        "registry_lifecycle_state": artifact.get("lifecycle_state"),
        "registry_promoted": bool(artifact.get("promoted")),
        "registry_active": bool(artifact.get("active")),
        "legacy_registry_certification_id": artifact.get("certification_id"),
        "universal_certification_release_present": release is not None,
        "reviewed_release_matches_registry": registry_release_match,
        "universal_calibration_certified_pass": calibration_pass,
        "model_adapter_registered": runtime["model_adapter_registered"],
        "calibrator_adapter_registered": runtime["calibrator_adapter_registered"],
        "hydration_route_registered": runtime["hydration_route_registered"],
        "action_canary_verified": canary_verified,
        "action_canary_blockers": list(canary_blockers),
        "calibration_evidence_hash": (certification_audit or {}).get("evidence_hash"),
        "can_execute": False,
    }


def run_prop_production_registration_audit(req: PropProductionRegistrationAuditRequest, *, db: Any) -> dict[str, Any]:
    certification = run_prop_certification_audit(
        PropCertificationAuditRequest(routes=req.routes, include_inactive=True), db=db
    )
    cert_by_identity = {
        _release_key(
            str(row.get("sport")), str(row.get("stat_type")),
            str(row.get("model_artifact_version")), str(row.get("artifact_checksum")),
        ): row
        for row in certification["artifact_rows"]
    }
    wanted = {str(value).strip().upper() for value in req.routes if str(value).strip()}
    artifacts = _best_artifact_by_route(_artifact_rows(db))
    output: list[dict[str, Any]] = []
    for key in sorted(DECLARED_PROP_LANES):
        token = f"{key[0]}:{key[1]}"
        if wanted and token not in wanted:
            continue
        artifact = artifacts.get(key)
        if artifact is None:
            evidence = PropRouteLifecycleEvidence(
                sport=key[0], stat_type=key[1], declared_route=True,
                controlling_specialist_ready=bool(DECLARED_PROP_LANES[key].controlling_specialist),
            )
            output.append(assess_prop_route(evidence).as_dict())
            continue
        identity = _release_key(key[0], key[1], str(artifact.get("model_artifact_version")), str(artifact.get("artifact_checksum")))
        release = REVIEWED_CERTIFICATION_RELEASES.get(identity)
        receipts = _canary_receipts(db, artifact, release)
        output.append(audit_registration_for_artifact(
            artifact, cert_by_identity.get(identity), release=release, canary_receipts=receipts
        ))
    output.sort(key=lambda row: (row["sport"], row["stat_type"]))
    return {
        "status": "PROP_PRODUCTION_REGISTRATION_AUDIT_COMPLETE",
        "route_rows": output,
        "production_registered_n": sum(1 for row in output if row.get("status") == "PRODUCTION_REGISTERED"),
        "reviewed_release_n": len(REVIEWED_CERTIFICATION_RELEASES),
        "certification_audit": {
            "artifact_n": len(certification["artifact_rows"]),
            "certified_pass_n": certification["certified_pass_n"],
            "reviewed_policy_count": certification["reviewed_policy_count"],
        },
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE", "CANARY_TABLE", "REVIEWED_CERTIFICATION_RELEASES",
    "PropProductionRegistrationAuditRequest", "ReviewedCertificationRelease",
    "audit_registration_for_artifact", "run_prop_production_registration_audit",
    "validate_action_canary_receipt",
]
