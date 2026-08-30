"""Canonical prop terminal reducer for WOW v16 Clean Core.

Infrastructure/capability failures are not pick rejections. This reducer keeps
those states explicit so downstream reporting cannot misrepresent an unevaluated
row as a model-negative verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PropTerminalDecision:
    terminal_label: str
    verdict_class: str
    model_evaluated: bool
    pick_rejected: bool
    infrastructure_blocked: bool
    blockers: tuple[str, ...]


MODEL_CAPABILITY_BLOCKERS = {
    "MODEL_UNAVAILABLE",
    "CONTROLLING_SPECIALIST_UNAVAILABLE",
    "EXACT_CERTIFIED_ROUTE_UNAVAILABLE",
    "MODEL_ARTIFACT_NOT_REGISTERED",
    "MODEL_ARTIFACT_NOT_PROMOTED",
    "UNSUPPORTED_COMPOSITE_MODEL",
    "MODEL_CALIBRATION_UNAVAILABLE",
}

EVIDENCE_BLOCKERS = {
    "EVIDENCE_INCOMPLETE",
    "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
    "ROLE_STATUS_UNAVAILABLE",
    "ROLE_OPPORTUNITY_PACKET_INCOMPLETE",
    "L10_EVIDENCE_INCOMPLETE",
    "FAILURE_PATH_CONTRACT_INCOMPLETE",
    "HYDRATION_INCOMPLETE",
    "STALE_EVIDENCE",
}

MARKET_BLOCKERS = {
    "EXACT_MARKET_IDENTITY_UNAVAILABLE",
    "MARKET_DATA_UNAVAILABLE",
    "PAYOUT_UNRESOLVED",
    "SETTLEMENT_RULE_UNRESOLVED",
    "PRICE_STALE",
}

EVENT_BLOCKERS = {
    "EVENT_NOT_PREGAME",
    "EVENT_STARTED",
    "EVENT_FINAL",
    "EVENT_CANCELLED",
    "EVENT_POSTPONED",
}

TRUE_MODEL_REJECTION_LABELS = {
    "NO_LOW_PROBABILITY",
    "REJECT_PROBABILITY",
    "REJECT_FAILURE_PATH",
    "REJECT_OOD",
    "REJECT_CALIBRATED_LOWER_BOUND",
}


def _normalized(blockers: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(b).strip().upper() for b in blockers if str(b).strip()))


def reduce_prop_terminal(
    *,
    proposed_label: str,
    blockers: Iterable[str] = (),
    model_evaluated: bool,
) -> PropTerminalDecision:
    """Reduce one prop row to an honest terminal state.

    Precedence is fail-closed and semantic:
      1. model capability missing -> MODEL_UNAVAILABLE
      2. evidence/hydration missing -> EVIDENCE_INCOMPLETE
      3. exact market/money identity missing -> MARKET_DATA_UNAVAILABLE
      4. event no longer valid for pregame -> NO_PLAY_FINAL_REFRESH
      5. only after model evaluation may probability/failure-path labels count as
         an actual pick rejection.
    """
    bs = _normalized(blockers)
    bset = set(bs)

    if bset & MODEL_CAPABILITY_BLOCKERS:
        return PropTerminalDecision(
            terminal_label="MODEL_UNAVAILABLE",
            verdict_class="CAPABILITY_BLOCKED",
            model_evaluated=False,
            pick_rejected=False,
            infrastructure_blocked=True,
            blockers=bs,
        )

    if bset & EVIDENCE_BLOCKERS:
        return PropTerminalDecision(
            terminal_label="EVIDENCE_INCOMPLETE",
            verdict_class="ACQUISITION_BLOCKED",
            model_evaluated=False,
            pick_rejected=False,
            infrastructure_blocked=True,
            blockers=bs,
        )

    if bset & MARKET_BLOCKERS:
        return PropTerminalDecision(
            terminal_label="MARKET_DATA_UNAVAILABLE",
            verdict_class="MARKET_BLOCKED",
            model_evaluated=model_evaluated,
            pick_rejected=False,
            infrastructure_blocked=True,
            blockers=bs,
        )

    if bset & EVENT_BLOCKERS:
        return PropTerminalDecision(
            terminal_label="NO_PLAY_FINAL_REFRESH",
            verdict_class="EVENT_INVALIDATED",
            model_evaluated=model_evaluated,
            pick_rejected=False,
            infrastructure_blocked=False,
            blockers=bs,
        )

    label = str(proposed_label or "").strip().upper() or "MODEL_UNAVAILABLE"
    if label in TRUE_MODEL_REJECTION_LABELS:
        if not model_evaluated:
            return PropTerminalDecision(
                terminal_label="MODEL_UNAVAILABLE",
                verdict_class="CAPABILITY_BLOCKED",
                model_evaluated=False,
                pick_rejected=False,
                infrastructure_blocked=True,
                blockers=bs + ("REJECTION_WITHOUT_MODEL_EVALUATION",),
            )
        return PropTerminalDecision(
            terminal_label=label,
            verdict_class="MODEL_REJECTED",
            model_evaluated=True,
            pick_rejected=True,
            infrastructure_blocked=False,
            blockers=bs,
        )

    return PropTerminalDecision(
        terminal_label=label,
        verdict_class="MODEL_SUPPORTED" if model_evaluated else "UNEVALUATED",
        model_evaluated=model_evaluated,
        pick_rejected=False,
        infrastructure_blocked=False,
        blockers=bs,
    )
