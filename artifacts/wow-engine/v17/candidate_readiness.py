"""Durable V17 candidate-readiness state machine.

Class-B orchestration only. This module never produces or substitutes sporting
probabilities, never changes calibration/threshold behavior, and never grants
execution authority. V17_TERMINAL_REDUCER remains the sole global terminal
authority for governed scoring runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Optional

CAN_EXECUTE = False


class CandidateState(str, Enum):
    ACQUIRED = "ACQUIRED"
    RECONCILED = "RECONCILED"
    HYDRATED = "HYDRATED"
    SPECIALIST_ASSIGNED = "SPECIALIST_ASSIGNED"
    EVALUATED = "EVALUATED"
    QUALIFIED_FOR_REDUCER = "QUALIFIED_FOR_REDUCER"

    REJECTED_MALFORMED_OFFER = "REJECTED_MALFORMED_OFFER"
    REJECTED_UNRESOLVED_IDENTITY = "REJECTED_UNRESOLVED_IDENTITY"
    REJECTED_FEATURE_UNAVAILABLE = "REJECTED_FEATURE_UNAVAILABLE"
    REJECTED_NO_SPECIALIST = "REJECTED_NO_SPECIALIST"
    REJECTED_DOMAIN_EXCEEDED = "REJECTED_DOMAIN_EXCEEDED"
    REJECTED_GOVERNANCE_GATE = "REJECTED_GOVERNANCE_GATE"


TERMINAL_REJECTION_STATES = frozenset(
    {
        CandidateState.REJECTED_MALFORMED_OFFER,
        CandidateState.REJECTED_UNRESOLVED_IDENTITY,
        CandidateState.REJECTED_FEATURE_UNAVAILABLE,
        CandidateState.REJECTED_NO_SPECIALIST,
        CandidateState.REJECTED_DOMAIN_EXCEEDED,
        CandidateState.REJECTED_GOVERNANCE_GATE,
    }
)

TERMINAL_STATES = TERMINAL_REJECTION_STATES | {
    CandidateState.QUALIFIED_FOR_REDUCER,
}

_FORWARD_NEXT: dict[CandidateState, CandidateState] = {
    CandidateState.ACQUIRED: CandidateState.RECONCILED,
    CandidateState.RECONCILED: CandidateState.HYDRATED,
    CandidateState.HYDRATED: CandidateState.SPECIALIST_ASSIGNED,
    CandidateState.SPECIALIST_ASSIGNED: CandidateState.EVALUATED,
    CandidateState.EVALUATED: CandidateState.QUALIFIED_FOR_REDUCER,
}

_REJECTION_FROM: dict[CandidateState, frozenset[CandidateState]] = {
    CandidateState.REJECTED_MALFORMED_OFFER: frozenset({CandidateState.ACQUIRED}),
    CandidateState.REJECTED_UNRESOLVED_IDENTITY: frozenset({CandidateState.ACQUIRED}),
    CandidateState.REJECTED_FEATURE_UNAVAILABLE: frozenset({CandidateState.RECONCILED}),
    CandidateState.REJECTED_NO_SPECIALIST: frozenset({CandidateState.HYDRATED}),
    CandidateState.REJECTED_DOMAIN_EXCEEDED: frozenset({CandidateState.SPECIALIST_ASSIGNED}),
    CandidateState.REJECTED_GOVERNANCE_GATE: frozenset(
        {CandidateState.SPECIALIST_ASSIGNED, CandidateState.EVALUATED}
    ),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def raw_payload_sha256(raw_payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_stable_json(raw_payload).encode("utf-8")).hexdigest()


def deterministic_candidate_id(
    *,
    source_feed: str,
    sport: str,
    raw_payload: Mapping[str, Any],
) -> str:
    """Return an immutable id for the exact discovered offer payload.

    Including the source and normalized sport prevents cross-provider collisions.
    The payload hash ensures repeated ingestion of an identical offer is
    idempotent while materially changed offers become distinct candidates.
    """
    source = str(source_feed or "").strip().lower()
    normalized_sport = str(sport or "").strip().upper()
    digest = raw_payload_sha256(raw_payload)
    return f"{source}:{normalized_sport}:{digest}"


class InvalidCandidateTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateLedgerEntry:
    candidate_id: str
    source_feed: str
    sport: str
    raw_payload_hash: str
    state: CandidateState = CandidateState.ACQUIRED
    canonical_event_id: Optional[str] = None
    canonical_player_id: Optional[str] = None
    canonical_market_id: Optional[str] = None
    feature_snapshot_id: Optional[str] = None
    specialist_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    updated_at: str = field(default_factory=_now_iso)
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.can_execute:
            raise ValueError("V17 candidate readiness entries must preserve can_execute=false")
        if self.state in TERMINAL_REJECTION_STATES and not self.rejection_reason:
            raise ValueError("terminal rejection states require rejection_reason")
        if self.state not in TERMINAL_REJECTION_STATES and self.rejection_reason:
            raise ValueError("non-rejection states cannot carry rejection_reason")


@dataclass(frozen=True)
class CandidateTransitionReceipt:
    candidate_id: str
    from_state: CandidateState
    to_state: CandidateState
    reason: Optional[str]
    transition_sha256: str
    created_at: str = field(default_factory=_now_iso)
    can_execute: bool = False


def acquired_candidate(
    *,
    source_feed: str,
    sport: str,
    raw_payload: Mapping[str, Any],
) -> CandidateLedgerEntry:
    payload_hash = raw_payload_sha256(raw_payload)
    return CandidateLedgerEntry(
        candidate_id=deterministic_candidate_id(
            source_feed=source_feed,
            sport=sport,
            raw_payload=raw_payload,
        ),
        source_feed=str(source_feed or "").strip().lower(),
        sport=str(sport or "").strip().upper(),
        raw_payload_hash=payload_hash,
    )


def _validate_transition(current: CandidateState, target: CandidateState) -> None:
    if current in TERMINAL_STATES:
        raise InvalidCandidateTransition(
            f"terminal candidate state {current.value} cannot transition to {target.value}"
        )

    expected = _FORWARD_NEXT.get(current)
    if target == expected:
        return

    allowed_rejection_sources = _REJECTION_FROM.get(target)
    if allowed_rejection_sources and current in allowed_rejection_sources:
        return

    raise InvalidCandidateTransition(
        f"illegal candidate transition {current.value}->{target.value}"
    )


def transition_candidate(
    entry: CandidateLedgerEntry,
    target: CandidateState,
    *,
    reason: Optional[str] = None,
    canonical_event_id: Optional[str] = None,
    canonical_player_id: Optional[str] = None,
    canonical_market_id: Optional[str] = None,
    feature_snapshot_id: Optional[str] = None,
    specialist_id: Optional[str] = None,
) -> tuple[CandidateLedgerEntry, CandidateTransitionReceipt]:
    """Advance one candidate exactly one governed state or to a typed rejection."""
    _validate_transition(entry.state, target)

    if target in TERMINAL_REJECTION_STATES and not str(reason or "").strip():
        raise InvalidCandidateTransition(
            f"{target.value} requires a typed rejection reason"
        )
    if target not in TERMINAL_REJECTION_STATES and reason is not None:
        raise InvalidCandidateTransition(
            "reason is reserved for typed rejection transitions"
        )

    updated = replace(
        entry,
        state=target,
        canonical_event_id=canonical_event_id
        if canonical_event_id is not None
        else entry.canonical_event_id,
        canonical_player_id=canonical_player_id
        if canonical_player_id is not None
        else entry.canonical_player_id,
        canonical_market_id=canonical_market_id
        if canonical_market_id is not None
        else entry.canonical_market_id,
        feature_snapshot_id=feature_snapshot_id
        if feature_snapshot_id is not None
        else entry.feature_snapshot_id,
        specialist_id=specialist_id if specialist_id is not None else entry.specialist_id,
        rejection_reason=str(reason).strip() if reason is not None else None,
        updated_at=_now_iso(),
        can_execute=False,
    )

    receipt_payload = {
        "candidate_id": entry.candidate_id,
        "from_state": entry.state.value,
        "to_state": target.value,
        "reason": updated.rejection_reason,
        "raw_payload_hash": entry.raw_payload_hash,
        "canonical_event_id": updated.canonical_event_id,
        "canonical_player_id": updated.canonical_player_id,
        "canonical_market_id": updated.canonical_market_id,
        "feature_snapshot_id": updated.feature_snapshot_id,
        "specialist_id": updated.specialist_id,
        "can_execute": False,
    }
    receipt = CandidateTransitionReceipt(
        candidate_id=entry.candidate_id,
        from_state=entry.state,
        to_state=target,
        reason=updated.rejection_reason,
        transition_sha256=hashlib.sha256(
            _stable_json(receipt_payload).encode("utf-8")
        ).hexdigest(),
        can_execute=False,
    )
    return updated, receipt


def readiness_status(entry: CandidateLedgerEntry) -> dict[str, Any]:
    """Return API-safe readiness metadata without model or market probability."""
    return {
        "candidate_id": entry.candidate_id,
        "source_feed": entry.source_feed,
        "sport": entry.sport,
        "state": entry.state.value,
        "canonical_event_id": entry.canonical_event_id,
        "canonical_player_id": entry.canonical_player_id,
        "canonical_market_id": entry.canonical_market_id,
        "feature_snapshot_id": entry.feature_snapshot_id,
        "specialist_id": entry.specialist_id,
        "rejection_reason": entry.rejection_reason,
        "qualified_for_reducer": entry.state == CandidateState.QUALIFIED_FOR_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CandidateLedgerEntry",
    "CandidateState",
    "CandidateTransitionReceipt",
    "InvalidCandidateTransition",
    "TERMINAL_REJECTION_STATES",
    "TERMINAL_STATES",
    "acquired_candidate",
    "deterministic_candidate_id",
    "raw_payload_sha256",
    "readiness_status",
    "transition_candidate",
]
