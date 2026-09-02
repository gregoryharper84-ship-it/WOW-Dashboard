"""V17 dependency/correlation structure and session/directional/duplicate-
thesis exposure gate.

Kept as two separate stages, matching the declared V17 shared gate order in
v17/custom_engine_alignment_contract.json (shared_gate_order):

    ... OBJECTIVE_SEPARATION
    -> DEPENDENCY_CORRELATION_STRUCTURE
    -> SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE
    -> WEAKEST_LEG_ELIMINATION ...

That contract names both stages but defines no numeric threshold or joint-
probability algorithm for either -- there is no certified model anywhere in
this codebase for the actual statistical dependence between two legs. This
module therefore never computes or fabricates a joint/conditional
probability. Where two or more legs in the same synchronous batch are
structurally dependent (the same underlying event) or compound the same
directional/session risk, and that dependence cannot be quantified, the
governed response is not a number: it is to record the dependency and hold
the affected legs out of downstream portfolio/slip qualification, while
leaving each row's own upstream model probability and terminal ceiling
completely untouched. Slip/card-level qualification is a separate objective
lane from model probability -- the same objective-separation pattern
qualification_policy_v2 already uses for downstream_money_evaluation_allowed.

Duplicate-thesis *detection* itself remains owned by
v17.slip_portfolio_optimizer (thesis_identity/optimize_portfolio, unchanged);
this module only folds that existing signal into the same unified
portfolio-qualification decision alongside dependency/exposure.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def _event_key(leg: dict[str, Any]) -> str:
    return str(leg.get("event_id") or leg.get("event_key") or leg.get("game_id") or "").strip().casefold()


def evaluate_dependency_correlation_structure(legs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Stage: DEPENDENCY_CORRELATION_STRUCTURE.

    Detects same-event structural dependency across legs in one synchronous
    batch/session. Two or more legs on the same event are dependent by
    construction -- this is a structural fact read from existing identity
    fields, not a statistical estimate, so it requires no model and
    fabricates no probability.

    Returns {row_id: {"same_event_dependent": bool, "co_dependent_row_ids": [...]}}.
    """
    by_event: dict[str, list[str]] = {}
    for leg in legs:
        key = _event_key(leg)
        if not key:
            continue
        by_event.setdefault(key, []).append(str(leg.get("row_id")))

    result: dict[str, dict[str, Any]] = {}
    for leg in legs:
        row_id = str(leg.get("row_id"))
        key = _event_key(leg)
        co_dependent = [rid for rid in by_event.get(key, []) if rid != row_id] if key else []
        result[row_id] = {
            "same_event_dependent": bool(co_dependent),
            "co_dependent_row_ids": co_dependent,
        }
    return result


def evaluate_session_directional_duplicate_thesis_exposure(
    legs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Stage: SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE.

    A separate stage from DEPENDENCY_CORRELATION_STRUCTURE per the declared
    V17 shared gate order -- computed independently, over its own two
    dimensions:

    - session exposure: how many legs in this one request/session reference
      the same event (there is no cross-request session store anywhere in
      this codebase, so "session" is honestly scoped to the current batch,
      which is what actually exists on this synchronous path).
    - directional exposure: 2+ legs sharing both the same event and the same
      directional lean, which compounds the same underlying risk even when
      the legs are not the same thesis (duplicate-thesis, handled
      separately by v17.slip_portfolio_optimizer, additionally requires a
      matching participant/market).

    Returns {row_id: {"session_event_leg_count": int, "directional_exposure": bool}}.
    """
    event_counts: Counter = Counter(_event_key(leg) for leg in legs if _event_key(leg))
    direction_counts_by_event: dict[str, Counter] = {}
    for leg in legs:
        key = _event_key(leg)
        if not key:
            continue
        direction = str(leg.get("direction") or "").strip().upper()
        direction_counts_by_event.setdefault(key, Counter())[direction] += 1

    result: dict[str, dict[str, Any]] = {}
    for leg in legs:
        row_id = str(leg.get("row_id"))
        key = _event_key(leg)
        direction = str(leg.get("direction") or "").strip().upper()
        session_event_leg_count = event_counts.get(key, 1) if key else 1
        directional_exposure = bool(key) and direction_counts_by_event.get(key, Counter()).get(direction, 0) > 1
        result[row_id] = {
            "session_event_leg_count": session_event_leg_count,
            "directional_exposure": directional_exposure,
        }
    return result


def evaluate_portfolio_qualification(
    legs: list[dict[str, Any]],
    *,
    duplicate_thesis_flagged: dict[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Combine both stages (plus the existing duplicate-thesis signal) into
    one machine-enforced per-row portfolio qualification decision.

    This is the DEPENDENCY_CORRELATION_STRUCTURE and
    SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE gates' shared output
    contract, not a third stage: each row leaves with a real
    downstream_portfolio_evaluation_allowed decision a caller must respect
    before combining rows into a slip/card, exactly mirroring how
    qualification_policy_v2 already gates downstream_money_evaluation_allowed
    as a separate objective from the model probability itself. A row's own
    terminal_status/terminal_label/probability_publishable/rank_eligible are
    never read or written here.
    """
    duplicate_thesis_flagged = duplicate_thesis_flagged or {}
    dependency = evaluate_dependency_correlation_structure(legs)
    exposure = evaluate_session_directional_duplicate_thesis_exposure(legs)

    result: dict[str, dict[str, Any]] = {}
    for leg in legs:
        row_id = str(leg.get("row_id"))
        dep = dependency.get(row_id, {"same_event_dependent": False, "co_dependent_row_ids": []})
        exp = exposure.get(row_id, {"session_event_leg_count": 1, "directional_exposure": False})
        is_duplicate_thesis = bool(duplicate_thesis_flagged.get(row_id, False))

        blockers: list[str] = []
        if dep["same_event_dependent"]:
            blockers.append("DEPENDENCE_UNQUANTIFIED_SAME_EVENT")
        if exp["directional_exposure"]:
            blockers.append("SESSION_DIRECTIONAL_EXPOSURE")
        if is_duplicate_thesis:
            blockers.append("DUPLICATE_THESIS_COMMON_HINGE")

        qualified = not blockers
        result[row_id] = {
            "same_event_dependent": dep["same_event_dependent"],
            "co_dependent_row_ids": dep["co_dependent_row_ids"],
            "session_event_leg_count": exp["session_event_leg_count"],
            "directional_exposure": exp["directional_exposure"],
            "duplicate_thesis_flagged": is_duplicate_thesis,
            "downstream_portfolio_evaluation_allowed": qualified,
            "portfolio_qualification": "QUALIFIED" if qualified else "HELD_FOR_DEPENDENCE",
            "blockers": blockers,
            "can_execute": False,
        }
    return result
