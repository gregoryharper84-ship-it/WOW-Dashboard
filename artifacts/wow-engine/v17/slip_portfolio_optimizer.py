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
- model qualification and all-or-nothing/Power card admission are separate states;
- Power cards run a critical-leg concentration audit before structural qualification;
- explicitly typed zero-event props require a P(0)/P(1+) event distribution before
  they can remain mandatory Power hinges;
- requested card size never justifies filler;
- unresolved same-event dependence blocks multi-leg portfolio qualification rather
  than inventing an independence assumption;
- structural decisions never mutate sporting-probability fields.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite, prod
from typing import Any, Iterable

CAN_EXECUTE = False
DUPLICATE_THESIS_PENALTY = 0.04
MIN_CARD_LEGS = 2
DEFAULT_MAX_POWER_CRITICAL_FAILURE_SHARE = 0.60
ZERO_EVENT_PROBABILITY_TOLERANCE = 1e-6
POWER_STRUCTURES = {"power", "power_play", "powerplay", "all_or_nothing", "all-or-nothing"}


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


def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def canonical_thesis_identity(leg: dict[str, Any]) -> str:
    """Exact immutable thesis identity used for session duplicate enforcement."""
    return "|".join(
        (_event(leg), _participant(leg), _market(leg), _period(leg), _line(leg), _direction(leg), _settlement(leg))
    )


def thesis_identity(leg: dict[str, Any]) -> str:
    """Directional exposure-family identity used for portfolio construction.

    Exact duplicates are determined by :func:`canonical_thesis_identity`. This
    broader family intentionally groups adjacent thresholds. Callers may provide
    ``exposure_family_key`` when a specialist has a stronger canonical exposure
    family; otherwise ``line_family`` or the market family is used.

    Exposure-family repetition is a structure/card risk only. It never changes
    sporting model probability or calibration.
    """
    explicit_family = _norm(leg.get("exposure_family_key"))
    if explicit_family:
        return explicit_family
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
    """Return true for same-event/player/direction statistical overlap."""
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
        value = _prob(leg.get(key))
        if value is not None:
            return value
    critical = _prob(leg.get("critical_leg_score"))
    if critical is not None:
        return critical
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
    candidate_row = str(candidate.get("row_id") or "")
    for leg in existing:
        if candidate_row and candidate_row == str(leg.get("row_id") or ""):
            return False
        if canonical_thesis_identity(leg) == exact:
            return False
        if thesis_identity(leg) == family:
            return False
        if component_composite_overlap(candidate, leg):
            return False
    return True


def _zero_event_required(leg: dict[str, Any]) -> bool:
    return bool(leg.get("discrete_zero_event_required")) or _norm(leg.get("fragility_class")) in {
        "zero_event",
        "discrete_zero_event",
    }


def _zero_event_audit(leg: dict[str, Any]) -> tuple[bool, tuple[str, ...], dict[str, Any]]:
    """Validate specialist-supplied P(0) / P(1+) for zero-event Power hinges.

    This function does not derive event probabilities from recent hit rate, market
    price, or the card's calibrated probability. The controlling specialist must
    supply the event distribution.
    """
    if not _zero_event_required(leg):
        return True, (), {"required": False, "status": "NOT_APPLICABLE"}

    p_zero = _prob(leg.get("p_zero_events"))
    p_one_plus = _prob(leg.get("p_one_plus_events"))
    tail_drivers = leg.get("zero_event_tail_drivers")
    if isinstance(tail_drivers, str):
        tail_drivers = [tail_drivers] if tail_drivers.strip() else []
    elif not isinstance(tail_drivers, Iterable) or isinstance(tail_drivers, (bytes, dict)):
        tail_drivers = []
    else:
        tail_drivers = [str(item).strip() for item in tail_drivers if str(item).strip()]

    blockers: list[str] = []
    if p_zero is None or p_one_plus is None:
        blockers.append("POWER_ZERO_EVENT_DISTRIBUTION_UNRESOLVED")
    elif abs((p_zero + p_one_plus) - 1.0) > ZERO_EVENT_PROBABILITY_TOLERANCE:
        blockers.append("POWER_ZERO_EVENT_DISTRIBUTION_NOT_NORMALIZED")
    if not tail_drivers:
        blockers.append("POWER_ZERO_EVENT_TAIL_PATH_UNRESOLVED")

    status = "PASS" if not blockers else "BLOCKED"
    return not blockers, tuple(blockers), {
        "required": True,
        "status": status,
        "p_zero_events": p_zero,
        "p_one_plus_events": p_one_plus,
        "tail_drivers": tail_drivers,
    }


def _power_fragility_metrics(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Structural critical-leg diagnostic based on governed lower bounds.

    The relative marginal contribution is used only for card construction. It is
    not a new sporting probability, calibrated probability, or joint model.
    """
    if not legs:
        return {
            "method": "STRUCTURAL_MARGINAL_APPROXIMATION",
            "critical_row_id": None,
            "critical_leg_share": 0.0,
            "marginal_contributions": {},
        }
    probabilities = [_quality(leg) for leg in legs]
    joint = prod(probabilities)
    raw_contributions: list[float] = []
    for i, probability in enumerate(probabilities):
        others = probabilities[:i] + probabilities[i + 1 :]
        joint_without = prod(others) if others else 1.0
        raw_contributions.append(max(0.0, joint_without - joint))
    total = sum(raw_contributions)
    shares = [value / total if total > 0 else 0.0 for value in raw_contributions]
    ranked = sorted(range(len(legs)), key=lambda i: shares[i], reverse=True)
    for rank, idx in enumerate(ranked, start=1):
        leg = legs[idx]
        leg.setdefault("portfolio_governance", {}).update(
            {
                "marginal_joint_failure_contribution": round(raw_contributions[idx], 8),
                "critical_leg_failure_share": round(shares[idx], 8),
                "critical_leg_rank": rank,
                "sporting_probability_mutated": False,
                "can_execute": False,
            }
        )
    critical_idx = ranked[0]
    return {
        "method": "STRUCTURAL_MARGINAL_APPROXIMATION",
        "critical_row_id": str(legs[critical_idx].get("row_id") or ""),
        "critical_leg_share": shares[critical_idx],
        "marginal_contributions": {
            str(leg.get("row_id") or i): round(raw_contributions[i], 8) for i, leg in enumerate(legs)
        },
        "relative_failure_shares": {
            str(leg.get("row_id") or i): round(shares[i], 8) for i, leg in enumerate(legs)
        },
    }


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
    critical_leg_actions: tuple[dict[str, Any], ...] = ()
    probability_fields_mutated: bool = False
    can_execute: bool = CAN_EXECUTE


def _best_replacement(
    *,
    alternatives: list[dict[str, Any]],
    duplicate_leg: dict[str, Any],
    retained_portfolio: list[dict[str, Any]],
    require_zero_event_ready: bool = False,
) -> dict[str, Any] | None:
    duplicate_quality = _quality(duplicate_leg)
    viable: list[dict[str, Any]] = []
    for alt in alternatives:
        if _quality(alt) <= duplicate_quality or not _is_independent(alt, retained_portfolio):
            continue
        if require_zero_event_ready:
            zero_ready, _, _ = _zero_event_audit(alt)
            if not zero_ready:
                continue
        viable.append(alt)
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


def _power_floor(card: dict[str, Any]) -> float | None:
    """Optional stricter admission floor supplied by governed card policy.

    No universal floor is invented here. Model qualification remains owned by the
    controlling specialist; Power admission may be stricter when the runtime policy
    explicitly supplies ``power_admission_lower_bound_floor``.
    """
    return _prob(card.get("power_admission_lower_bound_floor"))


def optimize_portfolio(
    cards: list[dict[str, Any]],
    *,
    alternatives: list[dict[str, Any]] | None = None,
    prior_session_legs: list[dict[str, Any]] | None = None,
    duplicate_penalty: float = DUPLICATE_THESIS_PENALTY,
    min_card_legs: int = MIN_CARD_LEGS,
    max_power_critical_failure_share: float = DEFAULT_MAX_POWER_CRITICAL_FAILURE_SHARE,
) -> PortfolioOptimizationResult:
    """Enforce session exposure, weakest-leg replacement, Power fragility and shrink.

    ``prior_session_legs`` lets a caller include already-proposed theses from the
    same governed session. The optimizer is intentionally stateless; a persistence
    layer may feed that ledger without changing this contract.

    Repeated, overlapping, under-floor, unresolved zero-event, or structurally
    critical Power hinges are never retained merely to preserve requested card size.
    A replacement must be strictly stronger and independent. Otherwise the card
    shrinks. None of these structure decisions may mutate sporting probability.
    """
    out = deepcopy(cards)
    alternatives = deepcopy(alternatives or [])
    prior = deepcopy(prior_session_legs or [])
    incoming = _all_legs(out)
    all_session = prior + incoming

    if not 0.0 < float(max_power_critical_failure_share) <= 1.0:
        raise ValueError("INVALID_MAX_POWER_CRITICAL_FAILURE_SHARE")

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
    critical_actions: list[dict[str, Any]] = []

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
            comparison_pool = retained_portfolio + kept
            overlapping_rows = [
                str(existing.get("row_id"))
                for existing in comparison_pool
                if component_composite_overlap(leg, existing)
            ]
            repeated_exact = exact_count > 1 and (
                seen_exact.get(exact_id, 0) > 0
                or any(canonical_thesis_identity(existing) == exact_id for existing in kept)
            )
            repeated_family = family_count > 1 and (
                seen_family.get(family_id, 0) > 0
                or any(thesis_identity(existing) == family_id for existing in kept)
            )
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
                    retained_portfolio=comparison_pool,
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

        structure = _norm(card.get("structure") or card.get("slip_type"))
        is_power = structure in POWER_STRUCTURES

        # Zero-event props (for example LESS 0.5 event-count markets) are a distinct
        # all-or-nothing fragility class. If the controlling specialist has marked a
        # leg as such, P(0), P(1+) and the tail path must be explicit before the leg
        # can remain a mandatory Power hinge.
        if is_power:
            zero_checked: list[dict[str, Any]] = []
            for leg in kept:
                zero_ready, zero_blockers, zero_audit = _zero_event_audit(leg)
                leg.setdefault("portfolio_governance", {})["zero_event_audit"] = zero_audit
                if zero_ready:
                    zero_checked.append(leg)
                    continue
                existing = retained_portfolio + zero_checked + [item for item in kept if item is not leg]
                replacement = _best_replacement(
                    alternatives=alternatives,
                    duplicate_leg=leg,
                    retained_portfolio=existing,
                    require_zero_event_ready=True,
                )
                reason = zero_blockers[0] if zero_blockers else "POWER_ZERO_EVENT_AUDIT_BLOCKED"
                if replacement is not None:
                    replacement = deepcopy(replacement)
                    replacement.setdefault("portfolio_governance", {}).update(
                        {
                            "replacement_for_row_id": leg.get("row_id"),
                            "replacement_reason": reason,
                            "sporting_probability_mutated": False,
                            "can_execute": False,
                        }
                    )
                    zero_checked.append(replacement)
                    replacements.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": leg.get("row_id"),
                            "replacement_row_id": replacement.get("row_id"),
                            "canonical_thesis_identity": canonical_thesis_identity(leg),
                            "reason": reason,
                        }
                    )
                else:
                    card_shrunk = True
                    removals.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": leg.get("row_id"),
                            "canonical_thesis_identity": canonical_thesis_identity(leg),
                            "reason": f"{reason}_SHRINK_NO_SUPERIOR_INDEPENDENT_REPLACEMENT",
                        }
                    )
            kept = zero_checked

        # A runtime may set a stricter Power-admission floor than the specialist's
        # model-qualification floor. This preserves probability qualification while
        # preventing a marginal row from becoming a mandatory all-or-nothing hinge.
        power_floor = _power_floor(card) if is_power else None
        if is_power and power_floor is not None:
            rebuilt: list[dict[str, Any]] = []
            for leg in kept:
                if _quality(leg) >= power_floor:
                    rebuilt.append(leg)
                    continue
                existing = retained_portfolio + rebuilt + [item for item in kept if item is not leg]
                replacement = _best_replacement(
                    alternatives=alternatives,
                    duplicate_leg=leg,
                    retained_portfolio=existing,
                    require_zero_event_ready=True,
                )
                if replacement is not None and _quality(replacement) >= power_floor:
                    replacement = deepcopy(replacement)
                    rebuilt.append(replacement)
                    replacements.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": leg.get("row_id"),
                            "replacement_row_id": replacement.get("row_id"),
                            "canonical_thesis_identity": canonical_thesis_identity(leg),
                            "reason": "POWER_ADMISSION_FLOOR_REPLACEMENT",
                        }
                    )
                else:
                    card_shrunk = True
                    removals.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": leg.get("row_id"),
                            "canonical_thesis_identity": canonical_thesis_identity(leg),
                            "reason": "POWER_ADMISSION_FLOOR_SHRINK_NO_SUPERIOR_REPLACEMENT",
                        }
                    )
            kept = rebuilt

        # Critical-leg cycle. A highly concentrated Power hinge is replaced only by
        # a strictly stronger independent candidate; otherwise the card shrinks and
        # is rescored. The diagnostic uses existing governed lower bounds only.
        if is_power:
            safety_counter = 0
            while len(kept) >= int(min_card_legs):
                safety_counter += 1
                if safety_counter > max(8, len(legs) + len(alternatives) + 2):
                    raise RuntimeError("POWER_CRITICAL_LEG_CYCLE_DID_NOT_CONVERGE")
                metrics = _power_fragility_metrics(kept)
                critical_share = float(metrics.get("critical_leg_share") or 0.0)
                if critical_share <= float(max_power_critical_failure_share):
                    break
                critical_row_id = str(metrics.get("critical_row_id") or "")
                critical_leg = next(
                    (leg for leg in kept if str(leg.get("row_id") or "") == critical_row_id),
                    None,
                )
                if critical_leg is None:
                    break
                others = [leg for leg in kept if leg is not critical_leg]
                replacement = _best_replacement(
                    alternatives=alternatives,
                    duplicate_leg=critical_leg,
                    retained_portfolio=retained_portfolio + others,
                    require_zero_event_ready=True,
                )
                action = {
                    "card_id": card_id,
                    "critical_row_id": critical_row_id,
                    "critical_leg_share": round(critical_share, 8),
                    "threshold": float(max_power_critical_failure_share),
                    "method": metrics.get("method"),
                }
                if replacement is not None:
                    replacement = deepcopy(replacement)
                    replacement.setdefault("portfolio_governance", {}).update(
                        {
                            "replacement_for_row_id": critical_row_id,
                            "replacement_reason": "POWER_CRITICAL_LEG_CONCENTRATION",
                            "sporting_probability_mutated": False,
                            "can_execute": False,
                        }
                    )
                    kept = [replacement if leg is critical_leg else leg for leg in kept]
                    action.update({"action": "REPLACE", "replacement_row_id": replacement.get("row_id")})
                    replacements.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": critical_row_id,
                            "replacement_row_id": replacement.get("row_id"),
                            "canonical_thesis_identity": canonical_thesis_identity(critical_leg),
                            "reason": "POWER_CRITICAL_LEG_CONCENTRATION",
                        }
                    )
                else:
                    kept = others
                    card_shrunk = True
                    action.update({"action": "SHRINK", "replacement_row_id": None})
                    removals.append(
                        {
                            "card_id": card_id,
                            "removed_row_id": critical_row_id,
                            "canonical_thesis_identity": canonical_thesis_identity(critical_leg),
                            "reason": "POWER_CRITICAL_LEG_CONCENTRATION_SHRINK_NO_SUPERIOR_INDEPENDENT_REPLACEMENT",
                        }
                    )
                critical_actions.append(action)

        final_fragility = _power_fragility_metrics(kept) if is_power else None
        card["legs"] = kept
        blockers: list[str] = []
        if len(kept) < int(min_card_legs):
            blockers.append("INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK")
        if structure in {"flex", "power"} and _same_event_dependency(kept) and not _joint_dependence_resolved(card):
            blockers.append("PP_CORRELATION_UNRESOLVED")

        card_governance = card.setdefault("portfolio_governance", {})
        card_governance.update(
            {
                "portfolio_optimized": True,
                "card_shrunk": card_shrunk,
                "shrink_reason": "WEAKEST_OR_CRITICAL_HINGE_NO_SUPERIOR_INDEPENDENT_REPLACEMENT" if card_shrunk else None,
                "power_admission_separate_from_model_qualification": is_power,
                "power_admission_lower_bound_floor": power_floor,
                "max_power_critical_failure_share": float(max_power_critical_failure_share) if is_power else None,
                "power_fragility": final_fragility,
                "portfolio_qualified": not blockers,
                "portfolio_status": "QUALIFIED" if not blockers else "HELD",
                "blockers": blockers,
                "sporting_probability_mutated": False,
                "can_execute": False,
            }
        )

        # Only final surviving legs become session exposure for later cards.
        for leg in kept:
            seen_exact[canonical_thesis_identity(leg)] += 1
            seen_family[thesis_identity(leg)] += 1
        retained_portfolio.extend(kept)

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
        critical_leg_actions=tuple(critical_actions),
        probability_fields_mutated=probability_mutated,
        can_execute=False,
    )


__all__ = [
    "CAN_EXECUTE",
    "DUPLICATE_THESIS_PENALTY",
    "MIN_CARD_LEGS",
    "DEFAULT_MAX_POWER_CRITICAL_FAILURE_SHARE",
    "PortfolioOptimizationResult",
    "canonical_thesis_identity",
    "component_composite_overlap",
    "find_component_overlap_pairs",
    "optimize_portfolio",
    "thesis_identity",
]
