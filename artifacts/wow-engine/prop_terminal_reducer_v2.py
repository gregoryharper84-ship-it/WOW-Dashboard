"""Canonical prop terminal reducer for WOW v16 Clean Core.

Infrastructure/capability failures are not pick rejections. This reducer keeps
those states explicit through verdict_class/blocker metadata while emitting only
native WOW terminal labels.
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

EVIDENCE_BLOCKERS = {
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
    "PROP_EVENT_IDENTITY_CONFLICT",
    "MLB_RECENT_STARTS_INSUFFICIENT",
    "MLB_STARTER_STATUS_UNRESOLVED",
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

# Row-level invalidations/rejections that intentionally occur before a fitted
# model evaluation. They are not capability failures and must not collapse to
# MODEL_UNAVAILABLE merely because model_evaluated=False.
PREMODEL_ROW_REJECTION_LABELS = {
    "SLATE_PURGE",
    "REJECT_DATA_QUALITY",
}


def _normalized(blockers: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(b).strip().upper() for b in blockers if str(b).strip()))


def reduce_prop_terminal(
    *,
    proposed_label: str,
    blockers: Iterable[str] = (),
    model_evaluated: bool,
) -> PropTerminalDecision:
    """Reduce one prop row to an honest native WOW terminal state.

    Precedence is fail-closed and objective-separated:
      1. an explicit event-state invalidation terminates the pregame row as
         NO_PLAY; it is not a pick rejection and cannot be disguised as an
         acquisition hold simply because evidence was also incomplete
      2. specialist/model capability missing -> MODEL_UNAVAILABLE
      3. mandatory evidence/hydration missing before model evaluation ->
         MODEL_UNAVAILABLE with ACQUISITION_BLOCKED verdict metadata
      4. explicit row-local premodel invalidations (for example stale-starter
         SLATE_PURGE or exhausted REJECT_DATA_QUALITY) remain rejected rows
         rather than being relabeled MODEL_UNAVAILABLE
      5. a genuine model rejection survives downstream market/money blockers
      6. market/money identity failures preserve a completed model-supported
         terminal label and mark only the market lane blocked.
    """
    bs = _normalized(blockers)
    bset = set(bs)
    label = str(proposed_label or "").strip().upper() or "MODEL_UNAVAILABLE"

    if bset & EVENT_BLOCKERS:
        return PropTerminalDecision(
            terminal_label="NO_PLAY",
            verdict_class="EVENT_INVALIDATED",
            model_evaluated=model_evaluated,
            pick_rejected=False,
            infrastructure_blocked=False,
            blockers=bs,
        )

    if bset & MODEL_CAPABILITY_BLOCKERS:
        return PropTerminalDecision(
            terminal_label="MODEL_UNAVAILABLE",
            verdict_class="CAPABILITY_BLOCKED",
            model_evaluated=False,
            pick_rejected=False,
            infrastructure_blocked=True,
            blockers=bs,
        )

    if bset & EVIDENCE_BLOCKERS and not model_evaluated:
        return PropTerminalDecision(
            terminal_label="MODEL_UNAVAILABLE",
            verdict_class="ACQUISITION_BLOCKED",
            model_evaluated=False,
            pick_rejected=False,
            infrastructure_blocked=True,
            blockers=bs,
        )

    if label in PREMODEL_ROW_REJECTION_LABELS:
        return PropTerminalDecision(
            terminal_label=label,
            verdict_class=(
                "ROW_INVALIDATED" if label == "SLATE_PURGE" else "DATA_QUALITY_REJECTED"
            ),
            model_evaluated=False,
            pick_rejected=True,
            infrastructure_blocked=False,
            blockers=bs,
        )

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
            infrastructure_blocked=bool(bset & MARKET_BLOCKERS),
            blockers=bs,
        )

    if bset & MARKET_BLOCKERS:
        return PropTerminalDecision(
            terminal_label=label if model_evaluated else "MODEL_UNAVAILABLE",
            verdict_class="MARKET_BLOCKED",
            model_evaluated=model_evaluated,
            pick_rejected=False,
            infrastructure_blocked=True,
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
