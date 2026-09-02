"""V17 analytical slip/card portfolio optimizer.

This module owns structural exposure only. It MUST NOT alter any row's sporting
model probability, calibrated probability, or calibrated lower bound. Duplicate
thesis exposure is a portfolio construction risk, not a second model penalty.

The optimizer treats separate slips as one portfolio. If the same underlying
thesis becomes a common hinge across cards, its structural risk rises. A stronger
independent alternative is preferred; when none exists, the card shrinks rather
than accepting filler.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

CAN_EXECUTE = False
DUPLICATE_THESIS_PENALTY = 0.04
MIN_CARD_LEGS = 2


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def thesis_identity(leg: dict[str, Any]) -> str:
    """Return normalized underlying prediction identity across cards.

    line_family is intentionally preferred over the exact board line. When the
    caller has not supplied an explicit family, the market/stat itself is the
    family so nearby promotional thresholds for the same event/player/stat/side
    are still recognized as one underlying directional thesis.
    """
    event = _norm(leg.get("event_id") or leg.get("event_key") or leg.get("game_id") or leg.get("game"))
    participant = _norm(leg.get("participant") or leg.get("player") or leg.get("team"))
    market = _norm(leg.get("market_family") or leg.get("stat") or leg.get("prop_type"))
    direction = _norm(leg.get("direction") or leg.get("side") or leg.get("selection"))
    line_family = _norm(leg.get("line_family") or market)
    return "|".join((event, participant, market, direction, line_family))


def _quality(leg: dict[str, Any]) -> float:
    """Read existing row quality without rewriting sporting probability."""
    for key in ("calibrated_lower_bound", "calibrated_probability", "model_probability"):
        value = leg.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    critical = leg.get("critical_leg_score")
    if isinstance(critical, (int, float)) and not isinstance(critical, bool):
        # Existing critical score is assumed quality-like: larger is stronger.
        return float(critical)
    return 0.50


def _all_legs(cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [leg for card in cards for leg in list(card.get("legs") or [])]


@dataclass(frozen=True)
class PortfolioOptimizationResult:
    cards: list[dict[str, Any]]
    duplicate_counts: dict[str, int]
    replacements: list[dict[str, Any]]
    removals: list[dict[str, Any]]
    probability_fields_mutated: bool = False
    can_execute: bool = CAN_EXECUTE


def _best_replacement(
    *,
    alternatives: list[dict[str, Any]],
    card_legs: list[dict[str, Any]],
    duplicate_leg: dict[str, Any],
    portfolio_counts: Counter,
) -> dict[str, Any] | None:
    existing_ids = {thesis_identity(leg) for leg in card_legs}
    duplicate_quality = _quality(duplicate_leg)
    viable: list[dict[str, Any]] = []
    for alt in alternatives:
        identity = thesis_identity(alt)
        if not identity or identity in existing_ids:
            continue
        # Do not replace one common hinge with another existing portfolio hinge.
        if portfolio_counts.get(identity, 0) > 0:
            continue
        # No filler: replacement must be strictly stronger than the duplicated
        # leg's unpenalized sporting-quality signal.
        if _quality(alt) <= duplicate_quality:
            continue
        viable.append(alt)
    if not viable:
        return None
    return max(viable, key=_quality)


def optimize_portfolio(
    cards: list[dict[str, Any]],
    *,
    alternatives: list[dict[str, Any]] | None = None,
    duplicate_penalty: float = DUPLICATE_THESIS_PENALTY,
    min_card_legs: int = MIN_CARD_LEGS,
) -> PortfolioOptimizationResult:
    """Remove common-hinge fragility across a set of proposed slips.

    The first occurrence of an otherwise valid thesis may remain. Subsequent
    occurrences are evaluated as portfolio duplication. Borderline duplicate
    legs are replaced with a stronger independent alternative when possible;
    otherwise the duplicate is removed if the card can retain ``min_card_legs``.

    Structural annotations are written under ``portfolio_governance`` only.
    Sporting probability fields are copied verbatim and never changed.
    """
    out = deepcopy(cards)
    alternatives = deepcopy(alternatives or [])
    counts: Counter = Counter(thesis_identity(leg) for leg in _all_legs(out) if thesis_identity(leg))
    original_probability_snapshot = {
        (str(card.get("card_id")), str(leg.get("row_id"))): (
            leg.get("model_probability"),
            leg.get("calibrated_probability"),
            leg.get("calibrated_lower_bound"),
        )
        for card in out
        for leg in list(card.get("legs") or [])
    }
    seen: Counter = Counter()
    replacements: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []

    for card in out:
        card_id = str(card.get("card_id") or "")
        legs = list(card.get("legs") or [])
        kept: list[dict[str, Any]] = []
        for leg in legs:
            identity = thesis_identity(leg)
            exposure_count = counts.get(identity, 0)
            base_quality = _quality(leg)
            exposure_penalty = max(0, exposure_count - 1) * float(duplicate_penalty)
            structural_score = base_quality - exposure_penalty
            leg.setdefault("portfolio_governance", {}).update(
                {
                    "thesis_identity": identity,
                    "duplicate_thesis_count": exposure_count,
                    "duplicate_thesis_penalty": round(exposure_penalty, 6),
                    "critical_leg_score": round(structural_score, 6),
                    "sporting_probability_mutated": False,
                    "can_execute": False,
                }
            )

            is_repeated_occurrence = exposure_count > 1 and seen[identity] > 0
            if not is_repeated_occurrence:
                kept.append(leg)
                seen[identity] += 1
                continue

            replacement = _best_replacement(
                alternatives=alternatives,
                card_legs=kept + [candidate for candidate in legs if candidate is not leg],
                duplicate_leg=leg,
                portfolio_counts=counts,
            )
            if replacement is not None:
                replacement = deepcopy(replacement)
                replacement.setdefault("portfolio_governance", {}).update(
                    {
                        "replacement_for_thesis": identity,
                        "replacement_reason": "DUPLICATE_THESIS_COMMON_HINGE",
                        "sporting_probability_mutated": False,
                        "can_execute": False,
                    }
                )
                kept.append(replacement)
                replacements.append(
                    {
                        "card_id": card_id,
                        "removed_row_id": leg.get("row_id"),
                        "replacement_row_id": replacement.get("row_id"),
                        "thesis_identity": identity,
                    }
                )
                seen[thesis_identity(replacement)] += 1
                continue

            if len(legs) - 1 >= int(min_card_legs):
                removals.append(
                    {
                        "card_id": card_id,
                        "removed_row_id": leg.get("row_id"),
                        "thesis_identity": identity,
                        "reason": "DUPLICATE_THESIS_SHRINK_NO_SUPERIOR_REPLACEMENT",
                    }
                )
                card.setdefault("portfolio_governance", {}).update(
                    {
                        "card_shrunk": True,
                        "shrink_reason": "DUPLICATE_THESIS_NO_SUPERIOR_REPLACEMENT",
                        "can_execute": False,
                    }
                )
                continue

            # Cannot shrink below the minimum. Retain the leg but make the common
            # hinge explicit rather than silently treating the cards independent.
            leg["portfolio_governance"]["duplicate_thesis_unresolved"] = True
            kept.append(leg)
            seen[identity] += 1

        card["legs"] = kept
        card.setdefault("portfolio_governance", {}).update(
            {
                "portfolio_optimized": True,
                "can_execute": False,
            }
        )

    # Defensive invariant: for surviving original rows, probabilities are exact
    # copies. This is deliberately separate from model/failure-path probability.
    probability_mutated = False
    for card in out:
        card_id = str(card.get("card_id") or "")
        for leg in list(card.get("legs") or []):
            key = (card_id, str(leg.get("row_id")))
            if key not in original_probability_snapshot:
                continue
            now = (
                leg.get("model_probability"),
                leg.get("calibrated_probability"),
                leg.get("calibrated_lower_bound"),
            )
            probability_mutated = probability_mutated or now != original_probability_snapshot[key]

    return PortfolioOptimizationResult(
        cards=out,
        duplicate_counts=dict(counts),
        replacements=replacements,
        removals=removals,
        probability_fields_mutated=probability_mutated,
        can_execute=False,
    )
