"""Research-only NFL Pick'em pool-win-equity shadow challenger.

This module never changes the production pick and never changes sporting
probabilities. It consumes completed pick'em rows plus pool ownership estimates
and identifies low-separation rows where a minority-side shadow alternative is
worth evaluating in historical/forward replay.

The output is a contest-strategy diagnostic, not a certified first-place equity
probability and not a production recommendation.
"""
from __future__ import annotations

from math import isclose, isfinite
from typing import Any, Iterable, Mapping

from v17.nfl_pickem_pool_optimizer import (
    PICKEM_READY,
    PICKEM_REVIEW_HIGH_CONFIDENCE_HOLD,
    PICKEM_REVIEW_MODEL_SIDE_FRAGILITY,
    PICKEM_REVIEW_TOSS_UP,
)

SERVING_MODE = "SHADOW_ONLY"
SHADOW_OBJECTIVE = "POOL_WIN_EQUITY_RESEARCH"
CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_PICK_MUTATION_ALLOWED = False
PRODUCTION_PROBABILITY_MUTATION_ALLOWED = False

SHADOW_ELIGIBLE = "SHADOW_DIFFERENTIATION_CANDIDATE"
SHADOW_PRESERVE = "SHADOW_PRESERVE_BASELINE"
SHADOW_BLOCKED = "SHADOW_BLOCKED"

_EPS = 1e-9
_MAX_EXPECTED_CORRECT_SACRIFICE = 0.10
_MIN_OWNERSHIP_GAP = 0.15


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) else None


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _ownership_pair(
    *,
    selected: str,
    opponent: str,
    pool_pick_share: Mapping[str, float],
) -> tuple[float | None, float | None, list[str]]:
    selected_share = _number(pool_pick_share.get(selected))
    opponent_share = _number(pool_pick_share.get(opponent))
    blockers: list[str] = []
    if selected_share is None or opponent_share is None:
        blockers.append("PICKEM_SHADOW_POOL_OWNERSHIP_REQUIRED")
        return selected_share, opponent_share, blockers
    if not (0.0 <= selected_share <= 1.0 and 0.0 <= opponent_share <= 1.0):
        blockers.append("PICKEM_SHADOW_POOL_OWNERSHIP_OUT_OF_RANGE")
    if not isclose(selected_share + opponent_share, 1.0, abs_tol=1e-6):
        blockers.append("PICKEM_SHADOW_TWO_SIDED_OWNERSHIP_NOT_NORMALIZED")
    return selected_share, opponent_share, blockers


def evaluate_shadow_candidate(
    pick: Mapping[str, Any],
    *,
    pool_pick_share: Mapping[str, float],
    pool_size: int,
) -> dict[str, Any]:
    """Evaluate one production pick for research-only differentiation."""
    selected = _text(pick.get("pool_pick"))
    opponent = _text(pick.get("opponent"))
    event_id = _text(pick.get("official_event_id"))
    selected_p = _number(pick.get("selected_probability"))
    home_p = _number(pick.get("home_probability"))
    away_p = _number(pick.get("away_probability"))

    blockers: list[str] = []
    if pick.get("status") != PICKEM_READY:
        blockers.append("PICKEM_SHADOW_BASE_PICK_NOT_READY")
    if not selected or not opponent or selected == opponent:
        blockers.append("PICKEM_SHADOW_PARTICIPANT_IDENTITY_INVALID")
    if event_id is None:
        blockers.append("PICKEM_SHADOW_EVENT_ID_REQUIRED")
    if selected_p is None or home_p is None or away_p is None:
        blockers.append("PICKEM_SHADOW_GOVERNED_PROBABILITY_REQUIRED")
    elif not (
        0.0 <= selected_p <= 1.0
        and 0.0 <= home_p <= 1.0
        and 0.0 <= away_p <= 1.0
        and isclose(home_p + away_p, 1.0, abs_tol=_EPS)
    ):
        blockers.append("PICKEM_SHADOW_GOVERNED_PROBABILITY_INVALID")
    if isinstance(pool_size, bool) or pool_size < 2:
        blockers.append("PICKEM_SHADOW_POOL_SIZE_INVALID")

    selected_share = opponent_share = None
    if selected and opponent:
        selected_share, opponent_share, ownership_blockers = _ownership_pair(
            selected=selected,
            opponent=opponent,
            pool_pick_share=pool_pick_share,
        )
        blockers.extend(ownership_blockers)

    if blockers:
        return {
            "serving_mode": SERVING_MODE,
            "shadow_objective": SHADOW_OBJECTIVE,
            "status": SHADOW_BLOCKED,
            "official_event_id": event_id,
            "production_pool_pick": selected,
            "shadow_pool_pick": None,
            "blockers": sorted(set(blockers)),
            "automatic_promotion": False,
            "production_pick_mutation_allowed": False,
            "production_probability_mutation_allowed": False,
            "can_execute": False,
        }

    assert selected is not None
    assert opponent is not None
    assert selected_p is not None
    assert home_p is not None
    assert away_p is not None
    assert selected_share is not None
    assert opponent_share is not None

    opponent_p = 1.0 - selected_p
    expected_correct_sacrifice = selected_p - opponent_p
    ownership_gap = selected_share - opponent_share
    shadow_signal = ownership_gap - expected_correct_sacrifice
    review_class = _text(pick.get("pickem_review_class"))

    reasons: list[str] = []
    if review_class == PICKEM_REVIEW_HIGH_CONFIDENCE_HOLD:
        reasons.append("HIGH_CONFIDENCE_HOLD_PRESERVE")
    if review_class not in {
        PICKEM_REVIEW_MODEL_SIDE_FRAGILITY,
        PICKEM_REVIEW_TOSS_UP,
    }:
        reasons.append("ROW_NOT_FRAGILITY_OR_TOSS_UP_REVIEW")
    if ownership_gap < _MIN_OWNERSHIP_GAP - _EPS:
        reasons.append("INSUFFICIENT_POOL_OWNERSHIP_GAP")
    if expected_correct_sacrifice > _MAX_EXPECTED_CORRECT_SACRIFICE + _EPS:
        reasons.append("EXPECTED_CORRECT_SACRIFICE_TOO_LARGE")
    if selected_share <= opponent_share + _EPS:
        reasons.append("BASELINE_SIDE_NOT_OVEROWNED")

    eligible = not reasons
    return {
        "serving_mode": SERVING_MODE,
        "shadow_objective": SHADOW_OBJECTIVE,
        "status": SHADOW_ELIGIBLE if eligible else SHADOW_PRESERVE,
        "official_event_id": event_id,
        "production_pool_pick": selected,
        "shadow_pool_pick": opponent if eligible else selected,
        "opponent": opponent,
        "governed_selected_probability": selected_p,
        "governed_opponent_probability": opponent_p,
        "governed_probability_gap": abs(selected_p - opponent_p),
        "pool_size": pool_size,
        "production_side_pool_share": selected_share,
        "opponent_pool_share": opponent_share,
        "ownership_gap": ownership_gap,
        "expected_correct_sacrifice_if_switched": expected_correct_sacrifice,
        "shadow_differentiation_signal": shadow_signal,
        "review_class": review_class,
        "reasons": reasons,
        "thresholds": {
            "max_expected_correct_sacrifice": _MAX_EXPECTED_CORRECT_SACRIFICE,
            "min_ownership_gap": _MIN_OWNERSHIP_GAP,
            "certified_for_production": False,
        },
        "production_pool_pick_unchanged": True,
        "sporting_probabilities_unchanged": True,
        "automatic_promotion": False,
        "production_pick_mutation_allowed": False,
        "production_probability_mutation_allowed": False,
        "can_execute": False,
    }


def build_pool_win_equity_shadow(
    picks: Iterable[Mapping[str, Any]],
    *,
    pool_pick_shares: Mapping[str, Mapping[str, float]],
    pool_size: int,
) -> dict[str, Any]:
    """Build a research-only shadow board; production picks remain immutable."""
    rows = []
    for pick in picks:
        event_id = _text(pick.get("official_event_id"))
        ownership = pool_pick_shares.get(event_id or "", {})
        rows.append(
            evaluate_shadow_candidate(
                pick,
                pool_pick_share=ownership,
                pool_size=pool_size,
            )
        )

    return {
        "serving_mode": SERVING_MODE,
        "shadow_objective": SHADOW_OBJECTIVE,
        "row_count": len(rows),
        "candidate_count": sum(1 for row in rows if row["status"] == SHADOW_ELIGIBLE),
        "preserve_count": sum(1 for row in rows if row["status"] == SHADOW_PRESERVE),
        "blocked_count": sum(1 for row in rows if row["status"] == SHADOW_BLOCKED),
        "rows": rows,
        "production_strategy_changed": False,
        "production_probability_changed": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "CAN_EXECUTE",
    "PRODUCTION_PICK_MUTATION_ALLOWED",
    "PRODUCTION_PROBABILITY_MUTATION_ALLOWED",
    "SERVING_MODE",
    "SHADOW_BLOCKED",
    "SHADOW_ELIGIBLE",
    "SHADOW_OBJECTIVE",
    "SHADOW_PRESERVE",
    "build_pool_win_equity_shadow",
    "evaluate_shadow_candidate",
]
