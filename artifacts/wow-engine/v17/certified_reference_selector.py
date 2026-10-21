"""WOW V17 certified-reference *selection* contract.

SOURCE-ONLY, offline pure policy. This module never scores a model, calibrates
probabilities, authorizes publication, calls a provider, or executes wagers.
Production adoption requires independently verified registry/evidence adapters,
certified reference artifacts, and V17 terminal-reducer integration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Optional

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
ELIGIBLE_PRIMARY_FAILURES = frozenset({
    "MODEL_UNAVAILABLE",
    "MODEL_SCORER_FAILED",
    "MODEL_OUTPUT_INVALID",
    "MODEL_INPUTS_INSUFFICIENT",
})
PREGAME_STATES = frozenset({"PREGAME", "SCHEDULED"})
SOURCE_ONLY = "SOURCE_ONLY_NOT_ACTIVATED"


@dataclass(frozen=True)
class ExactCandidate:
    candidate_id: str
    sport: str
    market_family: str
    stat_family: Optional[str]
    period: str
    official_event_id: str
    settlement_id: str
    event_status: str
    latest_material_update_at: datetime
    as_of: datetime
    exact_line: Optional[float] = None
    direction: Optional[str] = None
    missing_model_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelResult:
    status: str
    candidate_id: str
    model_id: Optional[str] = None
    artifact_id: Optional[str] = None
    calibrator_id: Optional[str] = None
    model_timestamp: Optional[datetime] = None
    calibrated_probability: Optional[float] = None
    calibrated_lower_bound: Optional[float] = None
    calibrated_upper_bound: Optional[float] = None


@dataclass(frozen=True)
class ReferenceRegistration:
    """MUST be read from trusted registry/certifier; never caller-provided."""
    model_id: str
    artifact_id: str
    calibrator_id: str
    certification_id: str
    independent_review_id: str
    active: bool
    certified: bool
    sport: str
    market_family: str
    stat_family: Optional[str]
    period: str
    settlement_id: str
    exact_lines: tuple[float, ...]
    allowed_primary_failures: tuple[str, ...]
    certified_missing_inputs: tuple[str, ...] = ()
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None


@dataclass(frozen=True)
class Selection:
    controlling_source: str       # PRIMARY | REFERENCE | NONE
    controlling_model_id: Optional[str]
    reason: str                    # selector diagnostic, NOT a public failure code
    primary_status: str            # immutable primary result preserved for reducer
    candidate_id: str
    reducer_required: bool = True
    selector_is_publication_authority: bool = False
    can_execute: bool = False


def _aware(dt: Optional[datetime]) -> bool:
    return dt is not None and isinstance(dt, datetime) and dt.tzinfo is not None and dt.utcoffset() is not None


def _probability_package_ok(result: ModelResult, candidate: ExactCandidate) -> bool:
    if result.status != "VALID" or result.candidate_id != candidate.candidate_id:
        return False
    if not all((result.model_id, result.artifact_id, result.calibrator_id)):
        return False
    if not _aware(result.model_timestamp):
        return False
    if not (candidate.latest_material_update_at <= result.model_timestamp <= candidate.as_of):
        return False
    nums = (
        result.calibrated_probability,
        result.calibrated_lower_bound,
        result.calibrated_upper_bound,
    )
    if any(v is None or isinstance(v, bool) or not isinstance(v, (float, int)) or not isfinite(v) for v in nums):
        return False
    p, lb, ub = nums
    return 0.0 < p < 1.0 and 0.0 <= lb <= p <= ub <= 1.0


def choose_reference_route(
    candidate: ExactCandidate,
    primary: ModelResult,
    reference_registration: Optional[ReferenceRegistration],
    reference_result: Optional[ModelResult],
    *,
    upstream_blockers: tuple[str, ...] = (),
) -> Selection:
    """Select one *candidate* controlling model. Never publish or approve.

    If the primary result is already valid, keep it even if later governance
    rejects it. Fallback is only considered after an eligible *typed* failure.
    Reference cannot be used to hide invalid identity, stale event, data
    conflicts, unsupported lines, or other upstream blockers.
    """
    def hold(reason: str) -> Selection:
        return Selection("NONE", None, reason, primary.status, candidate.candidate_id)

    def choose(source: str, model_id: str, reason: str) -> Selection:
        return Selection(source, model_id, reason, primary.status, candidate.candidate_id)

    if upstream_blockers:
        return hold("UPSTREAM_BLOCKER_PRESERVED")
    if not candidate.candidate_id or not candidate.official_event_id or not candidate.settlement_id:
        return hold("EXACT_IDENTITY_UNRESOLVED")
    if not _aware(candidate.latest_material_update_at) or not _aware(candidate.as_of):
        return hold("TIMESTAMP_INVALID")
    if candidate.latest_material_update_at > candidate.as_of:
        return hold("EVIDENCE_TIMESTAMP_CONFLICT")
    if candidate.event_status not in PREGAME_STATES:
        return hold("NON_PREGAME_EVENT")
    if candidate.market_family == "PLAYER_PROP":
        if (candidate.exact_line is None or not isfinite(candidate.exact_line) or
                candidate.direction not in ("MORE", "LESS") or not candidate.stat_family):
            return hold("EXACT_PROP_IDENTITY_UNRESOLVED")
    if primary.candidate_id != candidate.candidate_id:
        return hold("PRIMARY_IDENTITY_MISMATCH")

    if _probability_package_ok(primary, candidate):
        return choose("PRIMARY", str(primary.model_id), "PRIMARY_VALID_NO_FAILOVER")
    if primary.status == "VALID":
        return hold("PRIMARY_VALID_STATUS_WITH_INVALID_PACKAGE")
    if primary.status not in ELIGIBLE_PRIMARY_FAILURES:
        return hold("PRIMARY_STATUS_NOT_FAILOVER_ELIGIBLE")

    reg = reference_registration
    if reg is None or not reg.active or not reg.certified:
        return hold("REFERENCE_NOT_CERTIFIED_ACTIVE")
    if not all((reg.model_id, reg.artifact_id, reg.calibrator_id, reg.certification_id, reg.independent_review_id)):
        return hold("REFERENCE_PROVENANCE_MISSING")
    if (
        reg.sport != candidate.sport or reg.market_family != candidate.market_family or
        reg.stat_family != candidate.stat_family or reg.period != candidate.period or
        reg.settlement_id != candidate.settlement_id
    ):
        return hold("REFERENCE_SCOPE_MISMATCH")
    if primary.status not in reg.allowed_primary_failures:
        return hold("PRIMARY_FAILURE_NOT_IN_CERTIFIED_SCOPE")
    if candidate.exact_line is not None and candidate.exact_line not in reg.exact_lines:
        return hold("REFERENCE_EXACT_LINE_UNSUPPORTED")
    if not _aware(reg.valid_from) or not _aware(reg.valid_until):
        return hold("REFERENCE_CERTIFICATION_WINDOW_MISSING")
    if not (reg.valid_from <= candidate.as_of <= reg.valid_until):
        return hold("REFERENCE_CERTIFICATION_EXPIRED_OR_NOT_YET_VALID")
    if primary.model_id and reg.model_id == primary.model_id:
        return hold("REFERENCE_NOT_INDEPENDENT_OF_PRIMARY")
    if primary.status == "MODEL_INPUTS_INSUFFICIENT":
        # Only explicitly validated degraded-input regimes may use fallback.
        if tuple(sorted(set(candidate.missing_model_inputs))) != tuple(sorted(set(reg.certified_missing_inputs))):
            return hold("MISSING_INPUT_REGIME_UNCERTIFIED")
    elif candidate.missing_model_inputs:
        return hold("UNRESOLVED_MISSING_INPUTS")

    if reference_result is None:
        return hold("REFERENCE_RESULT_MISSING")
    if not _probability_package_ok(reference_result, candidate):
        return hold("REFERENCE_RESULT_INVALID_OR_STALE")
    if (
        reference_result.model_id != reg.model_id or
        reference_result.artifact_id != reg.artifact_id or
        reference_result.calibrator_id != reg.calibrator_id
    ):
        return hold("REFERENCE_ARTIFACT_OR_CALIBRATOR_MISMATCH")
    return choose("REFERENCE", reg.model_id, "CERTIFIED_REFERENCE_SELECTED_AWAIT_REDUCER")
