"""Autonomous V17 postmortem audit for high-confidence catastrophic prop misses.

The postmortem engine must not wait for a user to point out that a selection was
presented as HIGH/model-qualified and then failed through an extreme opposite-tail
outcome. This module performs a deterministic audit over immutable pregame
prediction/evidence records and the settled outcome.

It does not rewrite the pregame forecast, use postgame-only facts as pregame
evidence, or alter production model weights. It emits diagnostic/learning
findings only. can_execute is always false.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping

from mlb_pitcher_k_risk_guard import (
    ROLE_INCOMPATIBLE,
    TAG_LOW_LINE_MORE_TAIL_CONTRADICTION,
    low_line_more_tail_risk,
)


@dataclass(frozen=True)
class CatastrophicMissAudit:
    triggered: bool
    audit_type: str
    primary_miss_class: str
    contributing_factors: tuple[str, ...]
    predictability_class: str
    learning_level: str
    patch_candidate: bool
    preserve_constraints: tuple[str, ...]
    diagnostics: Mapping[str, Any]
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _high_confidence(prediction: Mapping[str, Any]) -> bool:
    tier = str(prediction.get("confidence_tier") or "").upper()
    terminal = str(prediction.get("terminal_label") or prediction.get("probability_ceiling") or "").upper()
    if tier == "HIGH" or terminal == "MODEL_QUALIFIED_HOLD":
        return True
    p = _number(prediction.get("calibrated_probability"))
    lb = _number(
        prediction.get("calibrated_probability_lower_bound")
        or prediction.get("calibrated_lower_bound")
    )
    return bool(p is not None and lb is not None and p >= 0.65 and lb >= 0.60)


def _actual_stat(outcome: Mapping[str, Any]) -> float | None:
    for key in ("official_stat_or_settlement_value", "actual_stat", "settled_value", "result_value"):
        value = _number(outcome.get(key))
        if value is not None:
            return value
    return None


def audit_high_confidence_catastrophic_miss(
    *,
    prediction: Mapping[str, Any],
    evidence: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> CatastrophicMissAudit:
    """Audit one exact immutable selection after settlement.

    Current certified special handling is intentionally narrow: MLB pitcher
    strikeout MORE selections that were high-confidence/model-qualified and
    settled at zero strikeouts. Other rows return triggered=False and remain
    available to the ordinary postmortem taxonomy.
    """
    sport = str(prediction.get("sport") or "").upper()
    stat = str(prediction.get("stat_type") or prediction.get("market") or "").upper()
    direction = str(prediction.get("direction") or prediction.get("side") or "").upper()
    line = _number(prediction.get("line") or prediction.get("exact_line_or_threshold"))
    actual = _actual_stat(outcome)

    trigger = bool(
        sport == "MLB"
        and stat == "PITCHER_STRIKEOUTS"
        and direction == "MORE"
        and line is not None
        and actual == 0.0
        and _high_confidence(prediction)
    )
    if not trigger:
        return CatastrophicMissAudit(
            triggered=False,
            audit_type="NOT_APPLICABLE",
            primary_miss_class="UNRESOLVED",
            contributing_factors=(),
            predictability_class="UNRESOLVED",
            learning_level="L0_OR_STANDARD_RETRO",
            patch_candidate=False,
            preserve_constraints=(),
            diagnostics={},
        )

    factors: list[str] = ["HIGH_CONFIDENCE_ZERO_K_FLOOR_COLLAPSE"]
    diagnostics: dict[str, Any] = {
        "sport": sport,
        "stat_type": stat,
        "direction": direction,
        "line": line,
        "actual": actual,
        "calibrated_probability": prediction.get("calibrated_probability"),
        "calibrated_lower_bound": prediction.get("calibrated_probability_lower_bound") or prediction.get("calibrated_lower_bound"),
    }

    role_status = evidence.get("role_status") if isinstance(evidence.get("role_status"), Mapping) else {}
    role_profile = role_status.get("recent_role_profile") if isinstance(role_status.get("recent_role_profile"), Mapping) else None
    role_mismatch = bool(role_profile and str(role_profile.get("status") or "").upper() == ROLE_INCOMPATIBLE)
    role_audit_missing = role_profile is None
    diagnostics["recent_role_profile"] = dict(role_profile) if role_profile else None

    if role_mismatch:
        factors.extend(("ROLE_OR_WORKLOAD_ERROR", "CURRENT_USAGE_INCOMPATIBLE_WITH_STARTER_MODEL"))
    elif role_audit_missing:
        factors.append("CURRENT_ROLE_WORKLOAD_AUDIT_MISSING")

    game_log = evidence.get("game_log") if isinstance(evidence.get("game_log"), list) else []
    tail = low_line_more_tail_risk(game_log, line=line, direction=direction)
    diagnostics["low_k_tail_audit"] = tail
    persisted_tags = {
        str(tag).strip().upper()
        for tag in (prediction.get("failure_cause_tags") or [])
        if str(tag).strip()
    }
    tail_contradiction = bool(tail.get("applies"))
    tail_guard_missing = tail_contradiction and TAG_LOW_LINE_MORE_TAIL_CONTRADICTION not in persisted_tags
    tail_guard_bypassed = tail_contradiction and TAG_LOW_LINE_MORE_TAIL_CONTRADICTION in persisted_tags
    if tail_guard_missing:
        factors.append("LOW_K_TAIL_CONTRADICTION_NOT_CONSUMED")
    if tail_guard_bypassed:
        factors.append("QUALIFICATION_CEILING_BYPASSED")

    market_prior = _number(prediction.get("market_prior_probability"))
    model_p = _number(prediction.get("calibrated_probability"))
    market_contradiction = bool(
        market_prior is not None
        and model_p is not None
        and model_p >= 0.65
        and market_prior < 0.50
    )
    if market_contradiction:
        # Market evidence is contradiction evidence only. It never replaces the
        # governed model probability.
        factors.append("EXACT_MARKET_CONTRADICTION_PRESENT")
    diagnostics["market_contradiction"] = market_contradiction

    if role_mismatch:
        primary = "ROLE_OR_WORKLOAD_ERROR"
        predictability = "PREDICTABLE_BUT_OMITTED"
        learning = "L3_PATCH_CANDIDATE"
        patch = True
    elif role_audit_missing:
        primary = "MODEL_INPUT_ERROR"
        predictability = "PREDICTABLE_BUT_OMITTED"
        learning = "L3_PATCH_CANDIDATE"
        patch = True
    elif tail_guard_missing or tail_guard_bypassed:
        primary = "TAIL_RISK_UNDERMODELED"
        predictability = "PREDICTABLE_BUT_UNDERWEIGHTED"
        learning = "L3_PATCH_CANDIDATE"
        patch = True
    else:
        primary = "MODEL_MISS"
        predictability = "PREDICTABLE_AND_MODELED"
        learning = "L1_LOG_ONLY"
        patch = False

    return CatastrophicMissAudit(
        triggered=True,
        audit_type="HIGH_CONFIDENCE_CATASTROPHIC_MISS_AUDIT",
        primary_miss_class=primary,
        contributing_factors=tuple(dict.fromkeys(factors)),
        predictability_class=predictability,
        learning_level=learning,
        patch_candidate=patch,
        preserve_constraints=(
            "DO_NOT_MUTATE_IMMUTABLE_PREGAME_PROBABILITY",
            "DO_NOT_REPLACE_MODEL_PROBABILITY_WITH_MARKET_PROBABILITY",
            "PRESERVE_NORMAL_STARTER_K_MODEL_BEHAVIOR",
            "PRESERVE_OPPONENT_K_SUPPRESSION_SINGLE_APPLICATION",
            "PRESERVE_CAN_EXECUTE_FALSE",
        ),
        diagnostics=diagnostics,
    )


__all__ = ["CatastrophicMissAudit", "audit_high_confidence_catastrophic_miss"]
