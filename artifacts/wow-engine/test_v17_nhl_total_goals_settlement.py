"""Deterministic research-only NHL totals settlement tests for issue #2121."""
import pytest

from v17.nhl_total_goals_settlement import (
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    NHLTotalSettlementError,
    totals_outcomes_from_pmf,
)

# Synthetic math-only fixture, NOT a model-derived prediction.
PMF = {5: 0.473, 6: 0.106, 7: 0.421}


def test_under_55_has_no_push():
    o = totals_outcomes_from_pmf(PMF, line=5.5, side="UNDER")
    assert o.win == pytest.approx(0.473)
    assert o.push == 0.0
    assert o.loss == pytest.approx(0.527)
    assert o.fair_decimal_odds == pytest.approx(1 / 0.473)


def test_under_60_pushes_at_exactly_six():
    o = totals_outcomes_from_pmf(PMF, line=6.0, side="UNDER")
    assert (o.win, o.push, o.loss) == pytest.approx((0.473, 0.106, 0.421))
    assert o.conditional_win_probability == pytest.approx(0.473 / 0.894)
    assert o.fair_decimal_odds == pytest.approx(1 + 0.421 / 0.473)


def test_over_60_pushes_at_six():
    o = totals_outcomes_from_pmf(PMF, line=6, side="OVER")
    assert (o.win, o.push, o.loss) == pytest.approx((0.421, 0.106, 0.473))


def test_over_55_has_no_push():
    o = totals_outcomes_from_pmf(PMF, line=5.5, side="OVER")
    assert (o.win, o.push, o.loss) == pytest.approx((0.527, 0, 0.473))


def test_ev_arithmetic_requires_price_and_keeps_push_refund():
    o = totals_outcomes_from_pmf(PMF, line=6.0, side="UNDER")
    assert o.unit_expected_net_return(1 + 0.421 / 0.473) == pytest.approx(0)
    with pytest.raises(NHLTotalSettlementError, match="DECIMAL_ODDS_INVALID"):
        o.unit_expected_net_return(float("nan"))


@pytest.mark.parametrize("line", [-1, 5.25, 5.75, float("inf"), True])
def test_unsupported_exact_lines_fail_closed(line):
    with pytest.raises(NHLTotalSettlementError, match="TOTAL_LINE_UNSUPPORTED"):
        totals_outcomes_from_pmf(PMF, line=line, side="UNDER")


@pytest.mark.parametrize("side", ["MORE", "", None])
def test_unknown_side_fails_closed(side):
    with pytest.raises(NHLTotalSettlementError, match="TOTAL_DIRECTION_UNRESOLVED"):
        totals_outcomes_from_pmf(PMF, line=5.5, side=side)


@pytest.mark.parametrize("pmf,code", [
    ({5: 0.473, 6: 0.106}, "GOAL_PMF_NOT_NORMALIZED"),
    ({5: -0.1, 6: 1.1}, "GOAL_PMF_INVALID"),
    ({5: float("nan"), 6: 1.0}, "GOAL_PMF_INVALID"),
    ({-1: 0.5, 5: 0.5}, "GOAL_COUNT_INVALID"),
    ({5.5: 1.0}, "GOAL_COUNT_INVALID"),
    ({True: 1.0}, "GOAL_COUNT_INVALID"),
    ({}, "GOAL_PMF_MISSING"),
])
def test_invalid_distribution_fails_closed(pmf, code):
    with pytest.raises(NHLTotalSettlementError, match=code):
        totals_outcomes_from_pmf(pmf, line=5.5, side="UNDER")


def test_all_push_possible_and_has_no_fair_price():
    o = totals_outcomes_from_pmf({6: 1.0}, line=6, side="UNDER")
    assert o.push == 1
    assert o.fair_decimal_odds is None
    assert o.conditional_win_probability is None


def test_math_is_research_only_without_execution():
    assert CAN_EXECUTE is False
    assert PROBABILITY_PUBLISHABLE is False
