"""Canonical V17 prop terminal reducer.

MODEL_UNAVAILABLE is reserved for an absent controlling fitted capability/artifact.
Input/evidence deficiencies, scorer failures, and malformed model packages retain
separate typed terminals. This reducer never promotes a research row or mutates
sporting probability.

A downstream market/payout blocker may coexist with a completed sporting model.
Once ``model_evaluated`` is true, that blocker holds market/value/card objectives
without becoming the cause of the sporting-probability terminal. This preserves
objective separation while retaining the blocker for downstream reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# What actually produced the terminal. This is reported separately from the
# blocker list because a row can carry an infrastructure blocker while its
# terminal was decided by the model, and counting that row as
# infrastructure-blocked both inflates infrastructure failures and hides a real
# model rejection.
CAUSE_MODEL_JUDGMENT = "MODEL_JUDGMENT"
CAUSE_INFRASTRUCTURE = "INFRASTRUCTURE"
CAUSE_EVENT = "EVENT"
CAUSE_MODEL_SUPPORTED = "MODEL_SUPPORTED"
CAUSE_UNEVALUATED = "UNEVALUATED"


@dataclass(frozen=True)
class PropTerminalDecision:
    terminal_label: str
    verdict_class: str
    model_evaluated: bool
    pick_rejected: bool
    #: True only when infrastructure *caused* this terminal. A model-decided
    #: terminal that merely coexists with an infrastructure blocker is False,
    #: and that blocker is listed in ``concurrent_infrastructure_blockers``.
    infrastructure_blocked: bool
    blockers: tuple[str, ...]
    terminal_cause: str = CAUSE_UNEVALUATED
    concurrent_infrastructure_blockers: tuple[str, ...] = ()


MODEL_CAPABILITY_BLOCKERS = {
    "MODEL_UNAVAILABLE", "CONTROLLING_SPECIALIST_UNAVAILABLE", "EXACT_CERTIFIED_ROUTE_UNAVAILABLE",
    "MODEL_ARTIFACT_NOT_REGISTERED", "MODEL_ARTIFACT_NOT_PROMOTED", "UNSUPPORTED_COMPOSITE_MODEL",
    "MODEL_CALIBRATION_UNAVAILABLE", "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND", "PROP_MODEL_REGISTRY_UNAVAILABLE",
    "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE", "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE",
}

INPUT_BLOCKERS = {
    "EVIDENCE_INCOMPLETE", "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND", "ROLE_STATUS_UNAVAILABLE",
    "ROLE_OPPORTUNITY_PACKET_INCOMPLETE", "L10_EVIDENCE_INCOMPLETE", "L10_GAME_LOG_INCOMPLETE",
    "L10_BOX_SCORE_LOG_INCOMPLETE", "FAILURE_PATH_CONTRACT_INCOMPLETE", "HYDRATION_INCOMPLETE",
    "RUN_INVALID_ACQUISITION_INCOMPLETE", "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
    "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE", "PROP_AUTO_HYDRATION_INTERNAL_ERROR",
    "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE", "PROP_PLAYER_IDENTITY_UNRESOLVED", "PROP_IDENTITY_UNRESOLVED",
    "PROP_EVENT_IDENTITY_CONFLICT", "MLB_RECENT_STARTS_INSUFFICIENT", "MLB_PITCH_COMPOSITION_INSUFFICIENT",
    "MLB_STARTER_STATUS_UNRESOLVED", "STALE_EVIDENCE",
}

SCORER_FAILURE_BLOCKERS = {"MODEL_SCORER_FAILED", "ROW_SCORING_FAILED", "ROW_SCORING_UNAVAILABLE", "PROP_SCORER_EXCEPTION"}
OUTPUT_INVALID_BLOCKERS = {"MODEL_OUTPUT_INVALID", "CALIBRATED_PROBABILITY_OR_BOUND_MISSING", "PROBABILITY_INVALID", "REJECTION_WITHOUT_MODEL_EVALUATION"}
MARKET_BLOCKERS = {"EXACT_MARKET_IDENTITY_UNAVAILABLE", "MARKET_DATA_UNAVAILABLE", "PAYOUT_UNRESOLVED", "SETTLEMENT_RULE_UNRESOLVED", "PRICE_STALE"}
EVENT_BLOCKERS = {"EVENT_NOT_PREGAME", "EVENT_ALREADY_STARTED", "EVENT_STARTED", "EVENT_FINAL", "EVENT_CANCELLED", "EVENT_POSTPONED"}
TRUE_MODEL_REJECTION_LABELS = {"NO_LOW_PROBABILITY", "REJECT_PROBABILITY", "REJECT_FAILURE_PATH", "REJECT_OOD", "REJECT_CALIBRATED_LOWER_BOUND"}
PREMODEL_MODEL_CONTRACT_REJECTION_BLOCKERS = {"MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT", "LINE_OUTSIDE_CERTIFIED_SUPPORT"}
PREMODEL_ROW_REJECTION_LABELS = {"SLATE_PURGE", "REJECT_DATA_QUALITY"}


def _normalized(blockers: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(b).strip().upper() for b in blockers if str(b).strip()))


def reduce_prop_terminal(*, proposed_label: str, blockers: Iterable[str] = (), model_evaluated: bool) -> PropTerminalDecision:
    bs = _normalized(blockers)
    bset = set(bs)
    label = str(proposed_label or "").strip().upper() or "MODEL_UNAVAILABLE"

    concurrent_market = tuple(b for b in bs if b in MARKET_BLOCKERS)

    if bset & EVENT_BLOCKERS:
        return PropTerminalDecision("NO_PLAY", "EVENT_INVALIDATED", model_evaluated, False, False, bs, CAUSE_EVENT, concurrent_market)
    if bset & MODEL_CAPABILITY_BLOCKERS:
        return PropTerminalDecision("MODEL_UNAVAILABLE", "CAPABILITY_BLOCKED", False, False, True, bs, CAUSE_INFRASTRUCTURE, concurrent_market)
    if (bset & SCORER_FAILURE_BLOCKERS) or label == "MODEL_SCORER_FAILED":
        return PropTerminalDecision("MODEL_SCORER_FAILED", "SCORER_FAILED", False, False, True, bs, CAUSE_INFRASTRUCTURE, concurrent_market)
    if (bset & OUTPUT_INVALID_BLOCKERS) or label == "MODEL_OUTPUT_INVALID":
        return PropTerminalDecision("MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_INVALID", model_evaluated, False, True, bs, CAUSE_INFRASTRUCTURE, concurrent_market)
    if bset & INPUT_BLOCKERS and not model_evaluated:
        return PropTerminalDecision("MODEL_INPUTS_INSUFFICIENT", "ACQUISITION_BLOCKED", False, False, True, bs, CAUSE_INFRASTRUCTURE, concurrent_market)
    if label in PREMODEL_ROW_REJECTION_LABELS:
        return PropTerminalDecision(label, "ROW_INVALIDATED" if label == "SLATE_PURGE" else "DATA_QUALITY_REJECTED", False, True, False, bs, CAUSE_MODEL_JUDGMENT, concurrent_market)
    if label == "REJECT_OOD" and not model_evaluated and bset & PREMODEL_MODEL_CONTRACT_REJECTION_BLOCKERS:
        return PropTerminalDecision("REJECT_OOD", "MODEL_CONTRACT_REJECTED", False, True, False, bs, CAUSE_MODEL_JUDGMENT, concurrent_market)
    if label in TRUE_MODEL_REJECTION_LABELS:
        if not model_evaluated:
            return PropTerminalDecision("MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_INVALID", False, False, True, bs + ("REJECTION_WITHOUT_MODEL_EVALUATION",), CAUSE_INFRASTRUCTURE, concurrent_market)
        # The model evaluated and rejected. A market blocker present alongside
        # that decision did not cause the terminal, so it is reported as
        # concurrent rather than as the cause.
        return PropTerminalDecision(label, "MODEL_REJECTED", True, True, False, bs, CAUSE_MODEL_JUDGMENT, concurrent_market)
    if bset & MARKET_BLOCKERS:
        if model_evaluated:
            # Market/payout readiness is downstream of a completed sporting
            # probability. Preserve the model's terminal and record the market
            # hold without classifying the sporting terminal as infrastructure-
            # caused. Consumers can hold edge/EV/card objectives via
            # verdict_class + blockers while still publishing/ranking the valid
            # sporting probability.
            return PropTerminalDecision(label, "MARKET_BLOCKED", True, False, False, bs, CAUSE_MODEL_SUPPORTED, concurrent_market)
        return PropTerminalDecision("MODEL_INPUTS_INSUFFICIENT", "MARKET_BLOCKED", False, False, True, bs, CAUSE_INFRASTRUCTURE, concurrent_market)
    return PropTerminalDecision(label, "MODEL_SUPPORTED" if model_evaluated else "UNEVALUATED", model_evaluated, False, False, bs, CAUSE_MODEL_SUPPORTED if model_evaluated else CAUSE_UNEVALUATED, concurrent_market)
