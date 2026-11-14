"""Research-only, exact-line NHL total-goals settlement mathematics.

This does NOT fit, predict, calibrate, or publish a sporting probability.
The caller must supply a complete, validated probability mass function over the
exact goal count defined by the relevant platform's verified settlement rule.
The caller, not this module, owns regulation/OT/shootout score conversion.

No sportsbook odds, projection or market movement is accepted as a goal PMF.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
_DISTRIBUTION_TOLERANCE = 1e-8


class NHLTotalSettlementError(ValueError):
    """Invalid line, direction, or complete count distribution."""


@dataclass(frozen=True)
class TotalSettlementProbabilities:
    win: float
    push: float
    loss: float

    @property
    def conditional_win_probability(self) -> float | None:
        """Break-even win fraction conditional on not pushing."""
        resolved = self.win + self.loss
        return self.win / resolved if resolved > 0 else None

    @property
    def fair_decimal_odds(self) -> float | None:
        """Refunded-push fair decimal odds, not market evidence."""
        return 1.0 + self.loss / self.win if self.win > 0 else None

    def unit_expected_net_return(self, decimal_odds: float) -> float:
        """Arithmetic only; price legitimacy and fees belong to the market lane."""
        if isinstance(decimal_odds, bool) or not isinstance(decimal_odds, (int, float)):
            raise NHLTotalSettlementError("DECIMAL_ODDS_INVALID")
        if not isfinite(decimal_odds) or decimal_odds <= 1:
            raise NHLTotalSettlementError("DECIMAL_ODDS_INVALID")
        return self.win * (float(decimal_odds) - 1.0) - self.loss


def totals_outcomes_from_pmf(
    goal_count_pmf: Mapping[int, float], *, line: float, side: str
) -> TotalSettlementProbabilities:
    """Compute exact UNDER/OVER win/push/loss for an already-fitted goal PMF.

    Supports nonnegative whole-goal counts and exact .0/.5 NHL totals only.
    No tail truncation, renormalization, interpolation, or model estimation.
    """
    if isinstance(line, bool) or not isinstance(line, (int, float)):
        raise NHLTotalSettlementError("TOTAL_LINE_UNSUPPORTED")
    if not isfinite(line) or line < 0 or abs(line * 2 - round(line * 2)) > 1e-9:
        raise NHLTotalSettlementError("TOTAL_LINE_UNSUPPORTED")
    if not isinstance(side, str) or side.upper() not in {"UNDER", "OVER"}:
        raise NHLTotalSettlementError("TOTAL_DIRECTION_UNRESOLVED")
    if not isinstance(goal_count_pmf, Mapping) or not goal_count_pmf:
        raise NHLTotalSettlementError("GOAL_PMF_MISSING")

    mass = 0.0
    win = push = loss = 0.0
    for goal_count, probability in goal_count_pmf.items():
        if isinstance(goal_count, bool) or not isinstance(goal_count, int) or goal_count < 0:
            raise NHLTotalSettlementError("GOAL_COUNT_INVALID")
        if isinstance(probability, bool) or not isinstance(probability, (float, int)):
            raise NHLTotalSettlementError("GOAL_PMF_INVALID")
        probability = float(probability)
        if not isfinite(probability) or not (0.0 <= probability <= 1.0):
            raise NHLTotalSettlementError("GOAL_PMF_INVALID")
        mass += probability
        if goal_count == line:
            push += probability
        elif (goal_count < line) == (side.upper() == "UNDER"):
            win += probability
        else:
            loss += probability

    if abs(mass - 1.0) > _DISTRIBUTION_TOLERANCE:
        raise NHLTotalSettlementError("GOAL_PMF_NOT_NORMALIZED")
    return TotalSettlementProbabilities(win=win, push=push, loss=loss)
