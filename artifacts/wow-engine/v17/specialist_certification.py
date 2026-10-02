from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


CAN_EXECUTE = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"


class QualificationStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    SPECIALIST_NOT_REGISTERED = "SPECIALIST_NOT_REGISTERED"
    SPECIALIST_NOT_CERTIFIED = "SPECIALIST_NOT_CERTIFIED"
    CERTIFICATION_EXPIRED = "CERTIFICATION_EXPIRED"
    CALIBRATION_ARTIFACT_MISSING = "CALIBRATION_ARTIFACT_MISSING"
    CALIBRATION_ARTIFACT_INVALID = "CALIBRATION_ARTIFACT_INVALID"
    REQUIRED_FEATURE_MISSING = "REQUIRED_FEATURE_MISSING"
    FEATURE_STALE = "FEATURE_STALE"
    MODEL_ARTIFACT_MISSING = "MODEL_ARTIFACT_MISSING"
    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    MARKET_NOT_SUPPORTED = "MARKET_NOT_SUPPORTED"
    LOWER_BOUND_ARTIFACT_MISSING = "LOWER_BOUND_ARTIFACT_MISSING"
    SPECIALIST_OWNERSHIP_CONFLICT = "SPECIALIST_OWNERSHIP_CONFLICT"
    CERTIFICATION_ARTIFACT_INVALID = "CERTIFICATION_ARTIFACT_INVALID"
    SCORER_FAILURE = "SCORER_FAILURE"


@dataclass(frozen=True)
class QualificationVerificationRequest:
    sport: str
    market_type: str
    target_specialist_id: str
    requested_market: str
    current_time: datetime


@dataclass(frozen=True)
class SpecialistRuntimePayload:
    identity: Mapping[str, Any]
    certification: Mapping[str, Any]
    calibration_artifact: Mapping[str, Any]
    model_artifact_sha: str
    feature_payload: Mapping[str, Any]
    lower_bound_artifact: Mapping[str, Any]
    matching_owner_ids: Sequence[str] = ()


def _parse_expiry(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def verify_specialist_qualification(
    request: QualificationVerificationRequest,
    specialist: Optional[SpecialistRuntimePayload],
) -> Tuple[QualificationStatus, str]:
    """Fail-closed V17 lineage verification; never substitutes another specialist."""

    if specialist is None or not specialist.identity:
        return (
            QualificationStatus.SPECIALIST_NOT_REGISTERED,
            f"No specialist registered for ID '{request.target_specialist_id}'.",
        )

    identity = specialist.identity
    cert = specialist.certification

    if identity.get("specialist_id") != request.target_specialist_id:
        return (
            QualificationStatus.SPECIALIST_NOT_REGISTERED,
            f"Resolved specialist '{identity.get('specialist_id')}' does not match target '{request.target_specialist_id}'.",
        )

    owner_ids = tuple(specialist.matching_owner_ids) or (str(identity.get("specialist_id")),)
    if len(owner_ids) != 1 or owner_ids[0] != request.target_specialist_id:
        return (
            QualificationStatus.SPECIALIST_OWNERSHIP_CONFLICT,
            f"Expected exactly one owner '{request.target_specialist_id}', got {list(owner_ids)}.",
        )

    if identity.get("sport") != request.sport or identity.get("market_type") != request.market_type:
        return (
            QualificationStatus.SPECIALIST_NOT_REGISTERED,
            f"Specialist '{identity.get('specialist_id')}' does not own {request.sport}:{request.market_type}.",
        )

    if request.requested_market not in identity.get("supported_markets", []):
        return (
            QualificationStatus.MARKET_NOT_SUPPORTED,
            f"Market '{request.requested_market}' not supported by '{request.target_specialist_id}'.",
        )

    if not cert:
        return (
            QualificationStatus.SPECIALIST_NOT_CERTIFIED,
            f"Specialist '{request.target_specialist_id}' has no certification artifact.",
        )
    if not cert.get("is_active", False):
        return (
            QualificationStatus.SPECIALIST_NOT_CERTIFIED,
            f"Specialist '{request.target_specialist_id}' certification is inactive.",
        )

    expires_at = _parse_expiry(cert.get("expires_at"))
    if expires_at is None:
        return (
            QualificationStatus.CERTIFICATION_ARTIFACT_INVALID,
            f"Certification expiry is missing or malformed for '{request.target_specialist_id}'.",
        )
    if request.current_time >= expires_at:
        return (
            QualificationStatus.CERTIFICATION_EXPIRED,
            f"Certification for '{request.target_specialist_id}' expired at {cert.get('expires_at')}.",
        )

    if not specialist.model_artifact_sha:
        return (
            QualificationStatus.MODEL_ARTIFACT_MISSING,
            f"Model artifact missing for '{request.target_specialist_id}'.",
        )

    calibration = specialist.calibration_artifact
    if not calibration:
        return (
            QualificationStatus.CALIBRATION_ARTIFACT_MISSING,
            f"Calibration artifact missing for '{request.target_specialist_id}'.",
        )

    feature_payload = specialist.feature_payload
    required_cert_keys = (
        "model_artifact_sha",
        "feature_contract_version",
        "calibration_artifact_id",
        "qualification_policy",
        "lower_bound_method",
    )
    missing_cert_keys = [key for key in required_cert_keys if key not in cert]
    if missing_cert_keys:
        return (
            QualificationStatus.CERTIFICATION_ARTIFACT_INVALID,
            f"Certification artifact missing keys: {missing_cert_keys}.",
        )

    if (
        specialist.model_artifact_sha != cert["model_artifact_sha"]
        or feature_payload.get("version") != cert["feature_contract_version"]
        or calibration.get("artifact_id") != cert["calibration_artifact_id"]
    ):
        return (
            QualificationStatus.MODEL_VERSION_MISMATCH,
            f"Version triad mismatch in '{request.target_specialist_id}'.",
        )

    if feature_payload.get("is_stale", False):
        return (
            QualificationStatus.FEATURE_STALE,
            f"Feature payload is stale for '{request.target_specialist_id}'.",
        )

    required_features = set(identity.get("required_feature_inputs", []))
    available_features = set((feature_payload.get("features") or {}).keys())
    missing_features = required_features - available_features
    if missing_features:
        return (
            QualificationStatus.REQUIRED_FEATURE_MISSING,
            f"Missing required features: {sorted(missing_features)}.",
        )

    policy = cert.get("qualification_policy")
    if not isinstance(policy, Mapping):
        return (
            QualificationStatus.CERTIFICATION_ARTIFACT_INVALID,
            f"Qualification policy missing or malformed for '{request.target_specialist_id}'.",
        )

    required_policy_keys = ("policy_id", "max_ece", "max_brier", "minimum_validation_rows")
    if any(key not in policy for key in required_policy_keys):
        return (
            QualificationStatus.CERTIFICATION_ARTIFACT_INVALID,
            f"Qualification policy incomplete for '{request.target_specialist_id}'.",
        )

    if (
        calibration.get("ece_score", 1.0) > policy["max_ece"]
        or calibration.get("brier_score", 1.0) > policy["max_brier"]
        or calibration.get("validation_rows", 0) < policy["minimum_validation_rows"]
    ):
        return (
            QualificationStatus.CALIBRATION_ARTIFACT_INVALID,
            f"Calibration artifact failed specialist policy '{policy['policy_id']}'.",
        )

    if not specialist.lower_bound_artifact:
        return (
            QualificationStatus.LOWER_BOUND_ARTIFACT_MISSING,
            f"Lower-bound artifact missing for '{request.target_specialist_id}'.",
        )

    return QualificationStatus.QUALIFIED, "SPECIALIST_QUALIFIED"


def evaluate_row_publication_gate(
    specialist_payload: Optional[SpecialistRuntimePayload],
    status: QualificationStatus,
    can_execute: bool = CAN_EXECUTE,
) -> Dict[str, Any]:
    """Return an auditable Class B receipt before V17_TERMINAL_REDUCER."""

    payload = specialist_payload
    owner_ids = tuple(payload.matching_owner_ids) if payload else ()
    if payload and not owner_ids and payload.identity:
        owner_ids = (str(payload.identity.get("specialist_id")),)

    receipt = {
        "exactly_one_specialist_owner": len(owner_ids) == 1,
        "model_artifact_present": bool(payload and payload.model_artifact_sha),
        "feature_contract_satisfied": status
        not in {QualificationStatus.REQUIRED_FEATURE_MISSING, QualificationStatus.FEATURE_STALE},
        "calibration_matches_model_and_features": status
        not in {
            QualificationStatus.MODEL_VERSION_MISMATCH,
            QualificationStatus.CALIBRATION_ARTIFACT_MISSING,
            QualificationStatus.CALIBRATION_ARTIFACT_INVALID,
        },
        "certification_active": status == QualificationStatus.QUALIFIED,
        "market_supported": status != QualificationStatus.MARKET_NOT_SUPPORTED,
        "lower_bound_artifact_valid": bool(payload and payload.lower_bound_artifact),
        "can_execute": can_execute,
        "terminal_authority": GLOBAL_TERMINAL_REDUCER,
    }

    publish_checks = [
        value
        for key, value in receipt.items()
        if key not in {"can_execute", "terminal_authority"}
    ]
    is_publishable = bool(publish_checks) and all(publish_checks) and not can_execute

    return {
        "is_publishable": is_publishable,
        "terminal_status": status.value if not is_publishable else "QUALIFIED_FOR_REDUCER",
        "verification_matrix": receipt,
    }
