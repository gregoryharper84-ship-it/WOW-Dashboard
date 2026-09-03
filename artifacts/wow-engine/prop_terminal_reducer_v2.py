"""Canonical V17 prop terminal reducer.

MODEL_UNAVAILABLE is reserved for an absent controlling fitted capability/artifact.
Input/evidence deficiencies, scorer failures, and malformed model packages retain
separate typed terminals.  This reducer never promotes a research row or mutates
sporting probability.
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
    "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
    "PROP_MODEL_REGISTRY_UNAVAILABLE",
    "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
    "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE",
}

INPUT_BLOCKERS = {
    "EVIDENCE_INCOMPLETE",
    "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
    "ROLE_STATUS_UNAVAILABLE",
    "ROLE_OPPORTUNITY_PACKET_INCOMPLETE",
    "L10_EVIDENCE_INCOMPLETE",
    "L10_GAME_LOG_INCOMPLETE",
    "L10_BOX_SCORE_LOG_INCOMPLETE",
    "FAILURE_PATH_CONTRACT_INCOMPLETE",
    "HYDRATION_INCOMPLETE",
    "RUN_INVALID_ACQUISITION_INCOMPLETE",
    "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
    "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
    "PROP_AUTO_HYDRATION_INTERNAL_ERROR",
    "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE",
    "PROP_PLAYER_IDENTITY_UNRESOLVED",
    "PROP_IDENTITY_UNRESOLVED",
    "PROP_EVENT_IDENTITY_CONFLICT",
    "MLB_RECENT_STARTS_INSUFFICIENT",
    "MLB_STARTER_STATUS_UNRESOLVED",
    "STALE_EVIDENCE",
}

SCORER_FAILURE_BLOCKERS = {
    "MODEL_SCORER_FAILED",
    "ROW_SCORING_FAILED",
    "ROW_SCORING_UNAVAILABLE",
    "PROP_SCORER_EXCEPTION",
}

OUTPUT_INVALID_BLOCKERS = {
    "MODEL_OUTPUT_INVALID",
    "CALIBRATED_PROBABILITY_OR_BOUND_MISSING",
    "PROBABILITY_INVALID",
    "REJECTION_WITHOUT_MODEL_EVALUATION",
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
    "EVENT_ALREADY_STARTED",
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

PREMODEL_MODEL_CONTRACT_REJECTION_BLOCKERS = {
    "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT",
    "LINE_OUTSIDE_CERTIFIED_SUPPORT",
}

PREMODEL_ROW_REJECTION_LABELS = {"SLATE_PURGE", "REJECT_DATA_QUALITY"}


def _normalized(blockers: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(b).strip().upper() for b in blockers if str(b).strip()))


def reduce_prop_terminal(*, proposed_label: str, blockers: Iterable[str] = (), model_evaluated: bool) -> PropTerminalDecision:
    """Reduce one prop row to the V17 terminal that matches the failing layer."""
    bs = _normalized(blockers)
    bset = set(bs)
    label = str(proposed_label or "").strip().upper() or "MODEL_UNAVAILABLE"

    if bset & EVENT_BLOCKERS:
        return PropTerminalDecision("NO_PLAY", "EVENT_INVALIDATED", model_evaluated, False, False, bs)

    if bset & MODEL_CAPABILITY_BLOCKERS:
        return PropTerminalDecision("MODEL_UNAVAILABLE", "CAPABILITY_BLOCKED", False, False, True, bs)

    if (bset & SCORER_FAILURE_BLOCKERS) or label == "MODEL_SCORER_FAILED":
        return PropTerminalDecision("MODEL_SCORER_FAILED", "SCORER_FAILED", False, False, True, bs)

    if (bset & OUTPUT_INVALID_BLOCKERS) or label == "MODEL_OUTPUT_INVALID":
        return PropTerminalDecision("MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_INVALID", model_evaluated, False, True, bs)

    if bset & INPUT_BLOCKERS and not model_evaluated:
        return PropTerminalDecision("MODEL_INPUTS_INSUFFICIENT", "INPUTS_INSUFFICIENT", False, False, True, bs)

    if label in PREMODEL_ROW_REJECTION_LABELS:
        return PropTerminalDecision(
            label,
            "ROW_INVALIDATED" if label == "SLATE_PURGE" else "DATA_QUALITY_REJECTED",
            False,
            True,
            False,
            bs,
        )

    if label == "REJECT_OOD" and not model_evaluated and bset & PREMODEL_MODEL_CONTRACT_REJECTION_BLOCKERS:
        return PropTerminalDecision("REJECT_OOD", "MODEL_CONTRACT_REJECTED", False, True, False, bs)

    if label in TRUE_MODEL_REJECTION_LABELS:
        if not model_evaluated:
            return PropTerminalDecision(
                "MODEL_OUTPUT_INVALID",
                "MODEL_OUTPUT_INVALID",
                False,
                False,
                True,
                bs + ("REJECTION_WITHOUT_MODEL_EVALUATION",),
            )
        return PropTerminalDecision(label, "MODEL_REJECTED", True, True, bool(bset & MARKET_BLOCKERS), bs)

    if bset & MARKET_BLOCKERS:
        return PropTerminalDecision(
            label if model_evaluated else "MODEL_INPUTS_INSUFFICIENT",
            "MARKET_BLOCKED",
            model_evaluated,
            False,
            True,
            bs,
        )

    return PropTerminalDecision(
        label,
        "MODEL_SUPPORTED" if model_evaluated else "UNEVALUATED",
        model_evaluated,
        False,
        False,
        bs,
    )
