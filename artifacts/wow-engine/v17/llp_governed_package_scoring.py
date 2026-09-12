"""Governed LLP probability-package validation, scoring, and ranking.

Patch: LLP-GOVERNED-PACKAGE-SCORING-CONTRACT-2026-09-09

This module is deliberately strict. Sportsbook implied probability, generic
`probability` aliases, recent hit rates, and post-model timestamps are never
accepted as substitutes for the immutable governed model package.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite, log
from typing import Any, Mapping

PATCH_ID = "LLP-GOVERNED-PACKAGE-SCORING-CONTRACT-2026-09-09"
CAN_EXECUTE = False
EPSILON = 1e-12

MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
STALE_MODEL_OUTPUT = "STALE_MODEL_OUTPUT"
MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"
MODEL_SCORER_FAILED = "MODEL_SCORER_FAILED"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
PASS = "PASS"

_REQUIRED_STRING_FIELDS = (
    "immutable_model_timestamp",
    "calibration_method",
    "calibration_version",
    "source_snapshot_id",
    "source_snapshot_timestamp",
    "outcome_space",
)


@dataclass(frozen=True)
class GovernedPackageAudit:
    status: str
    identifier: str | None
    calibrated_probability: float | None
    calibrated_lower_bound: float | None
    calibrated_upper_bound: float | None
    immutable_model_timestamp: str | None
    blockers: tuple[str, ...]
    rank_eligible: bool
    scoring_allowed: bool
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class GovernedPackageError(ValueError):
    """Typed fail-closed exception for an invalid or stale scoring package."""

    def __init__(self, audit: GovernedPackageAudit):
        self.audit = audit
        self.code = audit.status
        self.blockers = audit.blockers
        self.rank_eligible = audit.rank_eligible
        self.scoring_allowed = audit.scoring_allowed
        super().__init__(f"{audit.status}:{','.join(audit.blockers)}")


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) else None


def _aware_timestamp(value: Any) -> datetime | None:
    token = _text(value)
    if token is None:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _invalid(identifier: str | None, blockers: list[str]) -> GovernedPackageAudit:
    return GovernedPackageAudit(
        status=MODEL_OUTPUT_INVALID,
        identifier=identifier,
        calibrated_probability=None,
        calibrated_lower_bound=None,
        calibrated_upper_bound=None,
        immutable_model_timestamp=None,
        blockers=tuple(blockers),
        rank_eligible=False,
        scoring_allowed=False,
    )


def validate_governed_scoring_package(row: Mapping[str, Any]) -> GovernedPackageAudit:
    """Validate the exact governed package required for scoring and ranking.

    Canonical scoring fields are intentionally not alias-expanded. In particular,
    `probability`, `market_probability`, `calibrated_point`, `lower_bound`,
    `upper_bound`, `model_timestamp`, and `created_at` cannot satisfy this gate.
    """
    prediction_id = _text(row.get("prediction_id"))
    candidate_id = _text(row.get("candidate_id"))
    identifier = prediction_id or candidate_id
    blockers: list[str] = []

    if identifier is None:
        blockers.append("PREDICTION_ID_OR_CANDIDATE_ID_REQUIRED")

    p = _number(row.get("calibrated_probability"))
    lower = _number(row.get("calibrated_lower_bound"))
    upper = _number(row.get("calibrated_upper_bound"))
    if p is None:
        blockers.append("CALIBRATED_PROBABILITY_REQUIRED")
    if lower is None:
        blockers.append("CALIBRATED_LOWER_BOUND_REQUIRED")
    if upper is None:
        blockers.append("CALIBRATED_UPPER_BOUND_REQUIRED")

    for field in _REQUIRED_STRING_FIELDS:
        if _text(row.get(field)) is None:
            blockers.append(f"{field.upper()}_REQUIRED")

    if _text(row.get("model_version")) is None and _text(row.get("model_artifact_version")) is None:
        blockers.append("MODEL_VERSION_OR_MODEL_ARTIFACT_VERSION_REQUIRED")

    immutable_model_timestamp = _text(row.get("immutable_model_timestamp"))
    model_at = _aware_timestamp(immutable_model_timestamp)
    if immutable_model_timestamp is not None and model_at is None:
        blockers.append("IMMUTABLE_MODEL_TIMESTAMP_INVALID")

    source_snapshot_timestamp = _text(row.get("source_snapshot_timestamp"))
    if source_snapshot_timestamp is not None and _aware_timestamp(source_snapshot_timestamp) is None:
        blockers.append("SOURCE_SNAPSHOT_TIMESTAMP_INVALID")

    if p is not None and lower is not None and upper is not None:
        if not (0.0 <= lower <= p <= upper <= 1.0):
            blockers.append("CALIBRATED_PROBABILITY_BOUNDS_INVALID")

    if blockers:
        return _invalid(identifier, blockers)

    latest_material_update = row.get("latest_material_update_at")
    if latest_material_update in (None, ""):
        latest_material_update = row.get("latest_material_update_timestamp")
    if latest_material_update not in (None, ""):
        latest_at = _aware_timestamp(latest_material_update)
        if latest_at is None:
            return _invalid(identifier, ["LATEST_MATERIAL_UPDATE_TIMESTAMP_INVALID"])
        assert model_at is not None
        if model_at < latest_at:
            return GovernedPackageAudit(
                status=STALE_MODEL_OUTPUT,
                identifier=identifier,
                calibrated_probability=p,
                calibrated_lower_bound=lower,
                calibrated_upper_bound=upper,
                immutable_model_timestamp=immutable_model_timestamp,
                blockers=("IMMUTABLE_MODEL_TIMESTAMP_PRECEDES_LATEST_MATERIAL_UPDATE",),
                rank_eligible=False,
                scoring_allowed=False,
            )

    return GovernedPackageAudit(
        status=PASS,
        identifier=identifier,
        calibrated_probability=p,
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        immutable_model_timestamp=immutable_model_timestamp,
        blockers=(),
        rank_eligible=True,
        scoring_allowed=True,
    )


def require_governed_scoring_package(row: Mapping[str, Any]) -> GovernedPackageAudit:
    audit = validate_governed_scoring_package(row)
    if audit.status != PASS:
        raise GovernedPackageError(audit)
    return audit


def brier_from_package(row: Mapping[str, Any], outcome: float) -> float:
    audit = require_governed_scoring_package(row)
    if outcome not in (0.0, 1.0):
        raise ValueError("INVALID_BRIER_OUTCOME")
    assert audit.calibrated_probability is not None
    return (audit.calibrated_probability - outcome) ** 2


def log_loss_from_package(row: Mapping[str, Any], outcome: float) -> float:
    audit = require_governed_scoring_package(row)
    if outcome not in (0.0, 1.0):
        raise ValueError("INVALID_LOG_LOSS_OUTCOME")
    assert audit.calibrated_probability is not None
    safe = min(max(audit.calibrated_probability, EPSILON), 1.0 - EPSILON)
    return -(outcome * log(safe) + (1.0 - outcome) * log(1.0 - safe))


def probability_leaderboard_score(row: Mapping[str, Any]) -> float:
    audit = require_governed_scoring_package(row)
    assert audit.calibrated_lower_bound is not None
    return audit.calibrated_lower_bound


def edge_leaderboard_score(
    row: Mapping[str, Any],
    *,
    no_vig_probability: float,
    friction_buffer: float,
) -> float:
    audit = require_governed_scoring_package(row)
    no_vig = _number(no_vig_probability)
    friction = _number(friction_buffer)
    if no_vig is None or not 0.0 <= no_vig <= 1.0:
        raise ValueError("NO_VIG_PROBABILITY_INVALID")
    if friction is None or friction < 0.0:
        raise ValueError("FRICTION_BUFFER_INVALID")
    assert audit.calibrated_lower_bound is not None
    return audit.calibrated_lower_bound - no_vig - friction


def assert_immutable_model_timestamp(original: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    """Prevent odds/refresh/audit/settlement stages from rewriting model time."""
    before = _text(original.get("immutable_model_timestamp"))
    after = _text(candidate.get("immutable_model_timestamp"))
    if before is None or after is None or before != after:
        raise GovernedPackageError(
            _invalid(
                _text(candidate.get("prediction_id")) or _text(candidate.get("candidate_id")),
                ["IMMUTABLE_MODEL_TIMESTAMP_OVERWRITE_PROHIBITED"],
            )
        )


def classify_model_failure(
    *,
    capability_available: bool,
    inputs_ready: bool,
    model_invoked: bool,
    scorer_state: str | None = None,
) -> str:
    """Preserve V17 failure taxonomy; never collapse scorer failure to unavailable."""
    state = str(scorer_state or "").strip().upper()
    if not capability_available and not model_invoked:
        return MODEL_UNAVAILABLE
    if not inputs_ready:
        return MODEL_INPUTS_INSUFFICIENT
    if model_invoked and state in {"FAILED", "ERROR", "TIMEOUT", "TIMED_OUT", "HOLD", "HELD", "NO_VALID_PACKAGE"}:
        return MODEL_SCORER_FAILED
    if model_invoked and state in {"MALFORMED", "OUTPUT_INVALID", "INVALID_PACKAGE"}:
        return MODEL_OUTPUT_INVALID
    return PASS


__all__ = [
    "CAN_EXECUTE",
    "PATCH_ID",
    "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_OUTPUT_INVALID",
    "MODEL_SCORER_FAILED",
    "MODEL_UNAVAILABLE",
    "STALE_MODEL_OUTPUT",
    "GovernedPackageAudit",
    "GovernedPackageError",
    "assert_immutable_model_timestamp",
    "brier_from_package",
    "classify_model_failure",
    "edge_leaderboard_score",
    "log_loss_from_package",
    "probability_leaderboard_score",
    "require_governed_scoring_package",
    "validate_governed_scoring_package",
]
