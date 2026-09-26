"""Fitted-artifact certification contract for V17 team/event specialists.

A routable bridge is not a certified sporting model.  This module defines the
minimum immutable proof required before a non-legacy sport/event specialist may
publish or rank a governed probability.  It deliberately contains no sporting
model logic and never changes can_execute.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS, normalize_team_event_sport

CAN_EXECUTE = False
CERTIFICATION_PASS = "PASS"
CERTIFIED = "CERTIFIED"
GLOBAL_TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class TeamEventCertificationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FittedTeamEventCertification:
    certification_id: str
    sport: str
    league_scope: str
    controlling_specialist: str
    model_family: str
    model_version: str
    artifact_id: str
    artifact_sha256: str
    feature_schema_version: str
    input_contract_version: str
    calibration_artifact_id: str
    calibration_sha256: str
    calibration_method: str
    independent_verification_status: str
    certification_status: str
    certified_at: str
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "certification_id": self.certification_id,
            "sport": self.sport,
            "league_scope": self.league_scope,
            "controlling_specialist": self.controlling_specialist,
            "model_family": self.model_family,
            "model_version": self.model_version,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "feature_schema_version": self.feature_schema_version,
            "input_contract_version": self.input_contract_version,
            "calibration_artifact_id": self.calibration_artifact_id,
            "calibration_sha256": self.calibration_sha256,
            "calibration_method": self.calibration_method,
            "independent_verification_status": self.independent_verification_status,
            "certification_status": self.certification_status,
            "certified_at": self.certified_at,
            "can_execute": False,
        }


def _text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_FIELD_MISSING", field)
    return value


def _sha256(payload: Mapping[str, Any], field: str) -> str:
    value = _text(payload, field).lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_HASH_INVALID", field)
    return value


def _timestamp(payload: Mapping[str, Any], field: str) -> str:
    value = _text(payload, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_TIMESTAMP_INVALID", field) from exc
    if parsed.utcoffset() is None:
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_TIMESTAMP_INVALID", field)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_certification(payload: Mapping[str, Any]) -> FittedTeamEventCertification:
    """Validate one immutable fitted-specialist certification record.

    Registration, an importable scorer, a calibration object supplied by a caller,
    or a numerical model implementation cannot satisfy this contract by itself.
    """
    if not isinstance(payload, Mapping):
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_INVALID", "proof must be an object")
    if payload.get("can_execute") is not False:
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_CAN_EXECUTE_FORBIDDEN", "can_execute must be false")

    sport = normalize_team_event_sport(_text(payload, "sport"))
    if sport not in EXPECTED_TEAM_EVENT_SPORTS:
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_SPORT_UNSUPPORTED", sport)
    verification = _text(payload, "independent_verification_status").upper()
    status = _text(payload, "certification_status").upper()
    if verification != CERTIFICATION_PASS:
        raise TeamEventCertificationError("TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS", verification)
    if status != CERTIFIED:
        raise TeamEventCertificationError("TEAM_EVENT_SPECIALIST_NOT_CERTIFIED", status)

    return FittedTeamEventCertification(
        certification_id=_text(payload, "certification_id"),
        sport=sport,
        league_scope=_text(payload, "league_scope").upper(),
        controlling_specialist=_text(payload, "controlling_specialist"),
        model_family=_text(payload, "model_family"),
        model_version=_text(payload, "model_version"),
        artifact_id=_text(payload, "artifact_id"),
        artifact_sha256=_sha256(payload, "artifact_sha256"),
        feature_schema_version=_text(payload, "feature_schema_version"),
        input_contract_version=_text(payload, "input_contract_version"),
        calibration_artifact_id=_text(payload, "calibration_artifact_id"),
        calibration_sha256=_sha256(payload, "calibration_sha256"),
        calibration_method=_text(payload, "calibration_method"),
        independent_verification_status=verification,
        certification_status=status,
        certified_at=_timestamp(payload, "certified_at"),
        can_execute=False,
    )


def resolve_certification(client: Any, *, sport: str, league_scope: str | None = None) -> FittedTeamEventCertification:
    """Resolve the currently certified artifact from the service-role registry."""
    normalized = normalize_team_event_sport(sport)
    try:
        response = client.rpc(
            "wow_v17_active_team_event_certification",
            {"p_sport": normalized, "p_league_scope": league_scope},
        ).execute()
    except Exception as exc:  # noqa: BLE001 - registry outage is a typed capability blocker
        raise TeamEventCertificationError(
            "TEAM_EVENT_CERTIFICATION_REGISTRY_UNAVAILABLE",
            normalized,
        ) from exc
    payload = getattr(response, "data", None)
    if not isinstance(payload, Mapping):
        raise TeamEventCertificationError("TEAM_EVENT_CERTIFICATION_REGISTRY_INVALID_RESPONSE", normalized)
    if payload.get("ok") is not True:
        raise TeamEventCertificationError(
            str(payload.get("code") or "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"),
            normalized,
        )
    return parse_certification(payload)


def validate_prediction_package(
    package: Mapping[str, Any],
    certification: FittedTeamEventCertification,
) -> tuple[bool, tuple[str, ...]]:
    """Validate the common V17 sport-specialist probability envelope.

    This validator is intentionally sport-agnostic.  Sport-specific outcome-space
    normalization, failure paths and status gates remain owned by the exact lane.
    """
    blockers: list[str] = []
    if not isinstance(package, Mapping):
        return False, ("TEAM_EVENT_PROBABILITY_PACKAGE_INVALID",)

    if normalize_team_event_sport(str(package.get("sport") or "")) != certification.sport:
        blockers.append("TEAM_EVENT_CERTIFICATION_SPORT_MISMATCH")
    if str(package.get("controlling_specialist") or "") != certification.controlling_specialist:
        blockers.append("TEAM_EVENT_CERTIFICATION_SPECIALIST_MISMATCH")
    if str(package.get("model_version") or "") != certification.model_version:
        blockers.append("TEAM_EVENT_CERTIFICATION_MODEL_VERSION_MISMATCH")
    if str(package.get("artifact_id") or "") != certification.artifact_id:
        blockers.append("TEAM_EVENT_CERTIFICATION_ARTIFACT_MISMATCH")
    if str(package.get("calibration_method") or "") != certification.calibration_method:
        blockers.append("TEAM_EVENT_CERTIFICATION_CALIBRATION_MISMATCH")
    if str(package.get("independent_verification_status") or "").upper() != CERTIFICATION_PASS:
        blockers.append("TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS")
    if package.get("probability_package_valid") is not True:
        blockers.append("TEAM_EVENT_PROBABILITY_PACKAGE_NOT_VALID")
    if package.get("can_execute") is not False:
        blockers.append("CAN_EXECUTE_MUST_BE_FALSE")

    def probability(name: str) -> float | None:
        value = package.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        parsed = float(value)
        return parsed if isfinite(parsed) and 0.0 <= parsed <= 1.0 else None

    raw = probability("raw_probability")
    calibrated = probability("calibrated_probability")
    lower = probability("lower_bound")
    upper = probability("upper_bound")
    if raw is None:
        blockers.append("RAW_PROBABILITY_INVALID")
    if calibrated is None:
        blockers.append("CALIBRATED_PROBABILITY_INVALID")
    if lower is None:
        blockers.append("CALIBRATED_LOWER_BOUND_INVALID")
    if upper is None:
        blockers.append("CALIBRATED_UPPER_BOUND_INVALID")
    if None not in (lower, calibrated, upper) and not (lower <= calibrated <= upper):
        blockers.append("CALIBRATED_BOUNDS_ORDER_INVALID")

    model_at = None
    latest_at = None
    for field, bucket in (("model_timestamp", "model"), ("latest_material_update_timestamp", "latest")):
        try:
            parsed = datetime.fromisoformat(_text(package, field).replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError(field)
            if bucket == "model":
                model_at = parsed.astimezone(timezone.utc)
            else:
                latest_at = parsed.astimezone(timezone.utc)
        except (TeamEventCertificationError, ValueError):
            blockers.append(f"{field.upper()}_INVALID")
    if model_at is not None and latest_at is not None and model_at < latest_at:
        blockers.append("MODEL_STALE_AFTER_MATERIAL_UPDATE")

    return not blockers, tuple(dict.fromkeys(blockers))


__all__ = [
    "CAN_EXECUTE",
    "CERTIFICATION_PASS",
    "CERTIFIED",
    "FittedTeamEventCertification",
    "GLOBAL_TERMINAL_AUTHORITY",
    "TeamEventCertificationError",
    "parse_certification",
    "resolve_certification",
    "validate_prediction_package",
]
