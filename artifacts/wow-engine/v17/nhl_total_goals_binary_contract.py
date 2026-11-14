"""Nonpublishable Kalshi NHL totals binary predicate calculator.

IMPORTANT: An exchange contract's ABOVE/BELOW/AT_LEAST/EXACTLY/BETWEEN
predicate is not a sportsbook over/under bet, even at the same numeric
threshold. An integer BELOW 6 contract is false at six and has no push.
Exact Kalshi contract/ticker/period + authoritative source and lifecycle
must be verified *outside* this research-only mathematical adapter.

For suspended/cancelled events, the contract may resolve to last fair
price rather than a Boolean; no model probability may predict that event.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from v17.nhl_total_goals_settlement import (
    NHLTotalSettlementError,
    totals_outcomes_from_pmf,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False

# These refer to *already validated* PMF score definitions, not evidence that
# the operator actually listed a matching contract for the event.
PERIODS = frozenset({"FULL_GAME_OFFICIAL_FINAL_SCORE", "REGULATION_ONLY"})


@dataclass(frozen=True)
class BinaryPredicateOutcome:
    yes_probability: float
    no_probability: float
    push_probability: float = 0.0
    probability_publishable: bool = False
    can_execute: bool = False


def kalshi_hockey_totals_predicate(
    goal_count_pmf: Mapping[int, float], *,
    operator: str,
    threshold: float | tuple[float, float],
    contract_period: str,
    pmf_period: str,
) -> BinaryPredicateOutcome:
    """Calculate a binary event condition ONLY from a validated exact-period PMF.

    No contract price, commissions, liquidity, void/refund settlement or
    model certification are inferred. No bookmaker implied odds are inputs.
    """
    if contract_period not in PERIODS or pmf_period not in PERIODS or contract_period != pmf_period:
        raise NHLTotalSettlementError("KALSHI_TOTALS_PERIOD_SCOPE_UNVERIFIED")
    if operator not in {"ABOVE", "BELOW", "AT_LEAST", "EXACTLY", "BETWEEN"}:
        raise NHLTotalSettlementError("KALSHI_TOTALS_OPERATOR_UNSUPPORTED")
    def valid(x: object) -> bool:
        return type(x) in (int, float) and isfinite(x) and x >= 0 and x * 2 == round(x * 2)
    if operator == "BETWEEN":
        if (not isinstance(threshold, tuple) or len(threshold) != 2
                or not all(valid(v) for v in threshold) or threshold[0] > threshold[1]):
            raise NHLTotalSettlementError("KALSHI_TOTALS_THRESHOLD_INVALID")
        low, high = threshold
    else:
        if not valid(threshold):
            raise NHLTotalSettlementError("KALSHI_TOTALS_THRESHOLD_INVALID")
        low = high = float(threshold)
    # Reuse exhaustive PMF simplex/goal-count validation from the accepted
    # settlement math component. This call cannot and does not publish odds.
    totals_outcomes_from_pmf(goal_count_pmf, line=0.5, side="UNDER")
    def predicate(g: int) -> bool:
        if operator == "ABOVE":
            return g > high
        if operator == "BELOW":
            return g < low
        if operator == "AT_LEAST":
            return g >= low
        if operator == "EXACTLY":
            return g == low
        return low <= g <= high
    yes = sum(float(p) for g, p in goal_count_pmf.items() if predicate(g))
    no = sum(float(p) for g, p in goal_count_pmf.items() if not predicate(g))
    if abs(yes + no - 1) > 1e-8:
        raise NHLTotalSettlementError("KALSHI_TOTALS_PROBABILITY_MASS_INVALID")
    return BinaryPredicateOutcome(yes_probability=yes, no_probability=no)
