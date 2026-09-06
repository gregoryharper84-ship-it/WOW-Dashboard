"""V17 analytical slip/card portfolio optimizer.

This module owns structural exposure only. It MUST NOT alter any row's sporting
model probability, calibrated probability, or calibrated lower bound.

P0 invariants:
- exact theses are canonicalized across every proposed card in the governed session;
- adjacent thresholds/directional families remain exposure-related but are not
  mislabeled as the same exact prediction;
- same-player component/composite theses (for example POINTS MORE + PRA MORE)
  are treated as overlapping exposure when their statistical components intersect;
- a repeated/overlapping common hinge is replaced only by a strictly stronger,
  independent governed candidate; otherwise the card shrinks;
- requested card size never justifies filler;
- unresolved same-event dependence blocks multi-leg portfolio qualification rather
  than inventing an independence assumption;
- structural decisions never mutate sporting-probability fields.
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


def _event(leg: dict[str, Any]) -> str:
    return _norm(leg.get("event_id") or leg.get("event_key") or leg.get("game_id") or leg.get("game"))


def _participant(leg: dict[str, Any]) -> str:
    return _norm(leg.get("participant") or leg.get("player") or leg.get("team"))


def _market(leg: dict[str, Any]) -> str:
    return _norm(leg.get("market_family") or leg.get("stat") or leg.get("prop_type") or leg.get("market"))


def _direction(leg: dict[str, Any]) -> str:
    return _norm(leg.get("direction") or leg.get("side") or leg.get("selection"))


def _line(leg: dict[str, Any]) -> str:
    value = leg.get("exact_line")
    if value is None:
        value = leg.get("line")
    if value is None:
        value = leg.get("threshold")
    return _norm(value)


def _period(leg: dict[str, Any]) -> str:
    return _norm(leg.get("period") or leg.get("market_period") or "full_game")


def _settlement(leg: dict[str, Any]) -> str:
    return _norm(
        leg.get("settlement_identity")
        or leg.get("settlement_operator")
        or leg.get("settlement_source")
        or leg.get("platform")
    )


def canonical_thesis_identity(leg: dict[str, Any]) -> str:
    """Exact immutable thesis identity used for session duplicate enforcement."""
    return "|".join(
        (_event(leg), _participant(leg), _market(leg), _period(leg), _line(leg), _direction(leg), _settlement(leg))
    )


def thesis_identity(leg: dict[str, Any]) -> str:
    """Directional exposure-family identity retained for backwards compatibility.

    Exact duplicates are determined by :func:`canonical_thesis_identity`.  This
    broader family intentionally groups adjacent thresholds when an explicit
    ``line_family`` is absent so repeated exposure to the same player/stat/side is
    still visible without pretending the exact predictions are identical.
    """
    market = _market(leg)
    line_family = _norm(leg.get("line_family") or market)
    return "|".join((_event(leg), _participant(leg), market, _direction(leg), line_family))


_STAT_COMPONENTS: dict[str, frozenset[str]] = {
    "points": frozenset({"points"}),
    "pts": frozenset({"points"}),
    "rebounds": frozenset({"rebounds"}),
    "rebs": frozenset({"rebounds"}),
    "assists": frozenset({"assists"}),
    "asts": frozenset({"assists"}),
    "pra": frozenset({"points", "rebounds", "assists"}),
    "points_rebounds_assists": frozenset({"points", "rebounds", "assists"}),
    "pts_reb_ast": frozenset({"points", "rebounds", "assists"}),
    "points+rebounds+assists": frozenset({"points", "rebounds", "assists"}),
    "pr": frozenset({"points", "rebounds"}),
    "points_rebounds": frozenset({"points", "rebounds"}),
    "pa": frozenset({"points", "assists"}),
    "points_assists": frozenset({"points", "assists"}),
    "ra": frozenset({"rebounds", "assists"}),
    "rebounds_assists": frozenset({"rebounds", "assists"}),
}


def _market_components(leg: dict[str, Any]) -> frozenset[str]:
    token = _market(leg).replace(" ", "_").replace("-", "_")
    return _STAT_COMPONENTS.get(token, frozenset())


def component_composite_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return true for same-event/player/direction statistical overlap.

    This deliberately does not infer arbitrary cross-stat correlation. It only
    flags known component/composite relationships whose mathematical components
    intersect, such as POINTS and PRA.
    """
    if not _event(a) or _event(a) != _event(b):
        return False
    if not _participant(a) or _participant(a) != _participant(b):
        return False
    if not _direction(a) or _direction(a) != _direction(b):
        return False
    if _market(a) == _market(b):
        return False
    left, right = _market_components(a), _market_components(b)
    return bool(left and right and left.intersection(right))


def _quality(leg: dict[str, Any]) -> float:
    """Read existing governed row quality without rewriting probability."""
    for key in ("calibrated_lower_bound", "calibrated_probability", "model_probability"):
        value = leg.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    critical = leg.get("critical_leg_score")
    if isinstance(critical, (int, float)) and not isinstance(critical, bool):
        return float(critical)
    return 0.50


def _all_legs(cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [leg for card in cards for leg in list(card.get("legs") or [])]


def find_component_overlap_pairs(legs: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    rows = list(legs)
    pairs: list[tuple[str, str]] = []
    for i, left in enumerate(rows):
        for right in rows[i + 1 :]:
            if component_composite_overlap(left, right):
                pairs.append((str(left.get("row_id")), str(right.get("row_id"))))
    return pairs


def _is_independent(candidate: dict[str, Any], existing: Iterable[dict[str, Any]]) -> bool:
    exact = canonical_thesis_identity(candidate)
    family = thesis_identity(candidate)
    for leg in existing:
        if canonical_thesis_identity(leg) == exact:
            return False
        if thesis_identity(leg) == family:
            return False
        if component_composite_overlap(candidate, leg):
            return False
    return True


@dataclass(frozen=True)
class PortfolioOptimizationResult:
    cards: list[dict[str, Any]]
    duplicate_counts: dict[str, int]
    replacements: list[dict[str, Any]]
    removals: list[dict[str, Any]]
    exact_duplicate_counts: dict[str, int]
    component_overlap_pairs: tuple[tuple[str, str], ...]
    cards_qualified: int
    cards_held: int
    probability_fields_mutated: bool = False
    can_execute: bool = CAN_EXECUTE


def _best_replacement(
    *,
    alternatives: list[dict[str, Any]],
    duplicate_leg: dict[str, Any],
    retained_portfolio: list[dict[str, Any]],
) -> dict[str, Any] | None:
    duplicate_quality = _quality(duplicate_leg)
    viable = [
        alt
        for alt in alternatives
        if _quality(alt) > duplicate_quality and _is_independent(alt, retained_portfolio)
    ]
    if not viable:
        return None
    return max(viable, key=_quality)


def _same_event_dependency(legs: list[dict[str, Any]]) -> bool:
    counts = Counter(_event(leg) for leg in legs if _event(leg))
    return any(count > 1 for count in counts.values())


def _joint_dependence_resolved(card: dict[str, Any]) -> bool:
    values = {
        _norm(card.get("joint_probability_status")),
        _norm(card.get("correlation_treatment_status")),
        _norm((card.get("portfolio_governance") or {}).get("joint_probability_status")),
    }
    return bool(values.intersection({"pass", "resolved", "available", "joint_model_pass"}))


def optimize_portfolio(
    cards: list[dict[str, Any]],
    *,
    alternatives: list[dict[str, Any]] | None = None,
    prior_session_legs: list[dict[str, Any]] | None = None,
    duplicate_penalty: float = DUPLICATE_THESIS_PENALTY,
    min_card_legs: int = MIN_CARD_LEGS,
) -> PortfolioOptimizationResult:
    """Enforce session exposure, overlap, weakest-leg replacement and shrink.

    ``prior_session_legs`` lets a caller include already-proposed theses from the
    same governed session. The optimizer itself is intentionally stateless; a
    persistence/session layer may feed that ledger without changing this contract.

    A repeated or overlapping leg is never retained merely to preserve the requested
    card size. If no superior independent replacement exists, it is removed. A card
    that falls below the platform minimum remains a useful research artifact but is
    explicitly held and cannot be promoted as a qualified slip.
    """
    out = deepcopy(cards)
    alternatives = deepcopy(alternatives or [])
    prior = deepcopy(prior_session_legs or [])
    incoming = _all_legs(out)
    all_session = prior + incoming

    family_counts: Counter = Counter(thesis_identity(leg) for leg in all_session if thesis_identity(leg))
    exact_counts: Counter = Counter(
        canonical_thesis_identity(leg) for leg in all_session if canonical_thesis_identity(leg)
    )
    overlap_pairs = find_component_overlap_pairs(all_session)

    original_probability_snapshot = {
        (str(card.get("card_id")), str(leg.get("row_id"))): (
            leg.get("model_probability"),
            leg.get("calibrated_probability"),
            leg.get("calibrated_lower_bound"),
        )
        for card in out
        for leg in list(card.get("legs") or [])
    }

    seen_exact: Counter = Counter(canonical_thesis_identity(leg) for leg in prior)
    seen_family: Counter = Counter(thesis_identity(leg) for leg in prior)
    retained_portfolio: list[dict[str, Any]] = list(prior)
    replacements: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []

    for card in out:
        card_id = str(card.get("card_id") or "")
        legs = list(card.get("legs") or [])
        kept: list[dict[str, Any]] = []
        card_shrunk = False

        for leg in legs:
            exact_id = canonical_thesis_identity(leg)
            family_id = thesis_identity(leg)
            exact_count = exact_counts.get(exact_id, 0)
            family_count = family_counts.get(family_id, 0)
            overlapping_rows = [
                str(existing.get("row_id"))
                for existing in retained_portfolio
                if component_composite_overlap(leg, existing)
            ]
            repeated_exact = exact_count > 1 and seen_exact.get(exact_id, 0) > 0
            repeated_family = family_count > 1 and seen_family.get(family_id, 0) > 0
            overlapping = bool(overlapping_rows)

            base_quality = _quality(leg)
            structural_instances = max(exact_count, family_count, 1)
            exposure_penalty = max(0, structural_instances - 1) * float(duplicate_penalty)
            structural_score = base_quality - exposure_penalty
            governance = leg.setdefault("portfolio_governance", {})
            governance.update(
                {
                    "canonical_thesis_identity": exact_id,
                    "thesis_identity": family_id,
                    "exact_duplicate_count": exact_count,
                    "duplicate_thesis_count": family_count,
                    "component_composite_overlap": overlapping,
                    "overlapping_row_ids": overlapping_rows,
                    "duplicate_thesis_penalty": round(exposure_penalty, 6),
                    "critical_leg_score": round(structural_score, 6),
                    "sporting_probability_mutated": False,
                    "can_execute": False,
                }
            )

            reason = None
            if repeated_exact:
                reason = "EXACT_THESIS_DUPLICATE_SESSION_EXPOSURE"
            elif repeated_family:
                reason = "DIRECTIONAL_THESIS_FAMILY_EXPOSURE"
            elif overlapping:
                reason = "COMPONENT_COMPOSITE_OVERLAP"

            if reason is not None:
                replacement = _best_replacement(
                    alternatives=alternatives,
                    duplicate_leg=leg,
                    retained_portfolio=retained_portfolio + kept,
                )
                if replacement is not None:
                    replacement = deepcopy(replacement)
                    replacement.setdefault("portfolio_governance", {}).update(
                        {
                            "replacement_for_thesis": exact_id,
                            "replacement_reason": reason,
                            "sporting_probability_mutated": False,
                            "can_execute": False,
                        }
                    )
                    kept.append(replacement)
                    retained_portfolio.append(replacement)
                    seen_exact[canonical_thesis_identity(replacement)] += 1
                    seen_family[thesis_identity(replacement)] += 1
                    replacements.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": leg.get("row_id"),
                            "replacement_row_id": replacement.get("row_id"),
                            "canonical_thesis_identity": exact_id,
                            "reason": reason,
                        }
                    )
                    continue

                card_shrunk = True
                removals.append(
                    {
                        "card_id": card_id,
                        "removed_row_id": leg.get("row_id"),
                        "canonical_thesis_identity": exact_id,
                        "reason": f"{reason}_SHRINK_NO_SUPERIOR_INDEPENDENT_REPLACEMENT",
                    }
                )
                continue

            kept.append(leg)
            retained_portfolio.append(leg)
            seen_exact[exact_id] += 1
            seen_family[family_id] += 1

        card["legs"] = kept
        blockers: list[str] = []
        structure = _norm(card.get("structure") or card.get("slip_type"))
        if len(kept) < int(min_card_legs):
            blockers.append("INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK")
        if structure in {"flex", "power"} and _same_event_dependency(kept) and not _joint_dependence_resolved(card):
            blockers.append("PP_CORRELATION_UNRESOLVED")

        card.setdefault("portfolio_governance", {}).update(
            {
                "portfolio_optimized": True,
                "card_shrunk": card_shrunk,
                "shrink_reason": "COMMON_HINGE_NO_SUPERIOR_INDEPENDENT_REPLACEMENT" if card_shrunk else None,
                "portfolio_qualified": not blockers,
                "portfolio_status": "QUALIFIED" if not blockers else "HELD",
                "blockers": blockers,
                "can_execute": False,
            }
        )

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

    cards_qualified = sum(
        1 for card in out if (card.get("portfolio_governance") or {}).get("portfolio_qualified") is True
    )
    return PortfolioOptimizationResult(
        cards=out,
        duplicate_counts=dict(family_counts),
        replacements=replacements,
        removals=removals,
        exact_duplicate_counts=dict(exact_counts),
        component_overlap_pairs=tuple(overlap_pairs),
        cards_qualified=cards_qualified,
        cards_held=len(out) - cards_qualified,
        probability_fields_mutated=probability_mutated,
        can_execute=False,
    )


__all__ = [
    "CAN_EXECUTE",
    "DUPLICATE_THESIS_PENALTY",
    "MIN_CARD_LEGS",
    "PortfolioOptimizationResult",
    "canonical_thesis_identity",
    "component_composite_overlap",
    "find_component_overlap_pairs",
    "optimize_portfolio",
    "thesis_identity",
]
