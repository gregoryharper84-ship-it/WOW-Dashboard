"""V17 dependency/correlation and session/directional/duplicate-thesis gate.

This module governs portfolio structure only. It never changes a sporting model
probability, calibrated probability, or calibrated lower bound. V17 terminal
publication remains owned by V17_TERMINAL_REDUCER.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

try:
    from .slip_portfolio_optimizer import (
        canonical_thesis_identity,
        component_composite_overlap,
        thesis_identity,
    )
except ImportError:  # pragma: no cover - supports direct script-style test imports
    from slip_portfolio_optimizer import canonical_thesis_identity, component_composite_overlap, thesis_identity


def _event_key(leg: dict[str, Any]) -> str:
    return str(leg.get("event_id") or leg.get("event_key") or leg.get("game_id") or "").strip().casefold()


def _row_id(leg: dict[str, Any]) -> str:
    return str(leg.get("row_id") or leg.get("candidate_id") or "")


def evaluate_dependency_correlation_structure(legs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Detect factual same-event dependency without fabricating correlation."""
    by_event: dict[str, list[str]] = {}
    for leg in legs:
        key = _event_key(leg)
        if key:
            by_event.setdefault(key, []).append(_row_id(leg))

    result: dict[str, dict[str, Any]] = {}
    for leg in legs:
        row_id = _row_id(leg)
        key = _event_key(leg)
        co_dependent = [rid for rid in by_event.get(key, []) if rid != row_id] if key else []
        result[row_id] = {
            "same_event_dependent": bool(co_dependent),
            "co_dependent_row_ids": co_dependent,
        }
    return result


def evaluate_session_directional_duplicate_thesis_exposure(
    legs: list[dict[str, Any]],
    *,
    prior_session_legs: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Stage SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE.

    ``prior_session_legs`` allows the caller/session ledger to carry exposure from
    already-proposed cards into a later card-build call. Exact duplicates and the
    broader adjacent-line thesis family are reported separately.
    """
    prior = list(prior_session_legs or [])
    universe = prior + list(legs)
    event_counts: Counter = Counter(_event_key(leg) for leg in universe if _event_key(leg))
    direction_counts_by_event: dict[str, Counter] = {}
    exact_counts: Counter = Counter(canonical_thesis_identity(leg) for leg in universe)
    family_counts: Counter = Counter(thesis_identity(leg) for leg in universe)

    for leg in universe:
        key = _event_key(leg)
        if not key:
            continue
        direction = str(leg.get("direction") or leg.get("side") or "").strip().upper()
        direction_counts_by_event.setdefault(key, Counter())[direction] += 1

    result: dict[str, dict[str, Any]] = {}
    for leg in legs:
        row_id = _row_id(leg)
        key = _event_key(leg)
        direction = str(leg.get("direction") or leg.get("side") or "").strip().upper()
        exact_id = canonical_thesis_identity(leg)
        family_id = thesis_identity(leg)
        overlapping_row_ids = [
            _row_id(other)
            for other in universe
            if other is not leg and component_composite_overlap(leg, other)
        ]
        result[row_id] = {
            "session_event_leg_count": event_counts.get(key, 1) if key else 1,
            "directional_exposure": bool(key)
            and direction_counts_by_event.get(key, Counter()).get(direction, 0) > 1,
            "canonical_thesis_identity": exact_id,
            "exact_duplicate_count": exact_counts.get(exact_id, 0),
            "exact_duplicate_thesis": exact_counts.get(exact_id, 0) > 1,
            "thesis_identity": family_id,
            "thesis_family_count": family_counts.get(family_id, 0),
            "directional_thesis_family_exposure": family_counts.get(family_id, 0) > 1,
            "component_composite_overlap": bool(overlapping_row_ids),
            "overlapping_row_ids": overlapping_row_ids,
        }
    return result


def evaluate_portfolio_qualification(
    legs: list[dict[str, Any]],
    *,
    duplicate_thesis_flagged: dict[str, bool] | None = None,
    prior_session_legs: list[dict[str, Any]] | None = None,
    joint_dependence_resolved: bool = False,
) -> dict[str, dict[str, Any]]:
    """Combine structural and session exposure into per-row portfolio gating.

    Caller-provided duplicate flags remain supported, but V17 no longer depends on
    the caller to discover exact duplicate or component/composite exposure.
    """
    duplicate_thesis_flagged = duplicate_thesis_flagged or {}
    dependency = evaluate_dependency_correlation_structure(legs)
    exposure = evaluate_session_directional_duplicate_thesis_exposure(
        legs, prior_session_legs=prior_session_legs
    )

    result: dict[str, dict[str, Any]] = {}
    for leg in legs:
        row_id = _row_id(leg)
        dep = dependency.get(row_id, {"same_event_dependent": False, "co_dependent_row_ids": []})
        exp = exposure.get(row_id, {})
        externally_flagged = bool(duplicate_thesis_flagged.get(row_id, False))

        blockers: list[str] = []
        if dep["same_event_dependent"] and not joint_dependence_resolved:
            blockers.append("DEPENDENCE_UNQUANTIFIED_SAME_EVENT")
        if exp.get("directional_exposure"):
            blockers.append("SESSION_DIRECTIONAL_EXPOSURE")
        if exp.get("exact_duplicate_thesis"):
            blockers.append("EXACT_THESIS_DUPLICATE_SESSION_EXPOSURE")
        elif exp.get("directional_thesis_family_exposure") or externally_flagged:
            blockers.append("DUPLICATE_THESIS_COMMON_HINGE")
        if exp.get("component_composite_overlap"):
            blockers.append("COMPONENT_COMPOSITE_OVERLAP")

        blockers = list(dict.fromkeys(blockers))
        qualified = not blockers
        result[row_id] = {
            "same_event_dependent": dep["same_event_dependent"],
            "co_dependent_row_ids": dep["co_dependent_row_ids"],
            "session_event_leg_count": exp.get("session_event_leg_count", 1),
            "directional_exposure": bool(exp.get("directional_exposure")),
            "canonical_thesis_identity": exp.get("canonical_thesis_identity"),
            "exact_duplicate_count": exp.get("exact_duplicate_count", 1),
            "exact_duplicate_thesis": bool(exp.get("exact_duplicate_thesis")),
            "thesis_identity": exp.get("thesis_identity"),
            "thesis_family_count": exp.get("thesis_family_count", 1),
            "component_composite_overlap": bool(exp.get("component_composite_overlap")),
            "overlapping_row_ids": exp.get("overlapping_row_ids", []),
            "duplicate_thesis_flagged": externally_flagged,
            "downstream_portfolio_evaluation_allowed": qualified,
            "portfolio_qualification": "QUALIFIED" if qualified else "HELD_FOR_DEPENDENCE",
            "blockers": blockers,
            "sporting_probability_mutated": False,
            "can_execute": False,
        }
    return result


__all__ = [
    "evaluate_dependency_correlation_structure",
    "evaluate_session_directional_duplicate_thesis_exposure",
    "evaluate_portfolio_qualification",
]
