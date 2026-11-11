"""Exact threshold semantics: binary exchange contracts != sportsbook over/under."""
import pytest

from v17.nhl_total_goals_binary_contract import (
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    kalshi_hockey_totals_predicate,
)
from v17.nhl_total_goals_settlement import NHLTotalSettlementError, totals_outcomes_from_pmf

# Synthetic, NOT a real NHL sporting or market probability.
MASS = {5: 0.473, 6: 0.106, 7: 0.421}
SCOPE = "FULL_GAME_OFFICIAL_FINAL_SCORE"


def predicate(operator, threshold, **kwargs):
    return kalshi_hockey_totals_predicate(
        MASS, operator=operator, threshold=threshold,
        contract_period=SCOPE, pmf_period=SCOPE, **kwargs
    )


def test_binary_below_six_is_not_sportsbook_under_six():
    exchange = predicate("BELOW", 6)
    book = totals_outcomes_from_pmf(MASS, line=6.0, side="UNDER")
    assert exchange.yes_probability == pytest.approx(0.473)
    assert exchange.no_probability == pytest.approx(0.527)
    assert exchange.push_probability == 0.0
    assert book.win == pytest.approx(0.473)
    assert book.push == pytest.approx(0.106)
    assert book.loss == pytest.approx(0.421)


def test_below_half_line_does_not_push():
    p = predicate("BELOW", 5.5)
    assert (p.yes_probability, p.no_probability, p.push_probability) == pytest.approx((0.473,0.527,0))


def test_other_kalshi_predicates_preserve_threshold_literal():
    assert predicate("AT_LEAST", 6).yes_probability == pytest.approx(0.527)
    assert predicate("ABOVE", 6).yes_probability == pytest.approx(0.421)
    assert predicate("EXACTLY", 6).yes_probability == pytest.approx(0.106)
    assert predicate("BETWEEN", (5.5, 6.5)).yes_probability == pytest.approx(0.106)


@pytest.mark.parametrize("operator,threshold", [
    ("UNDER", 6), ("OVER", 5.5), ("AT_LEAST", "6"),
    ("BELOW", float("nan")), ("BELOW", 5.25),
    ("BELOW", True), ("BETWEEN", (5.5, 5)), ("BETWEEN", (5, 6, 7)),
])
def test_unsupported_operator_and_threshold_fail_closed(operator, threshold):
    with pytest.raises(NHLTotalSettlementError):
        predicate(operator, threshold)


@pytest.mark.parametrize("scope,pmf_scope", [
    ("UNKNOWN", SCOPE),
    (SCOPE, "REGULATION_ONLY"),
    ("REGULATION_ONLY", SCOPE),
    ("REGULATION_ONLY", None),
])
def test_unmatched_market_period_is_blocked(scope, pmf_scope):
    with pytest.raises(NHLTotalSettlementError, match="KALSHI_TOTALS_PERIOD_SCOPE_UNVERIFIED"):
        kalshi_hockey_totals_predicate(
            MASS, operator="BELOW", threshold=6,
            contract_period=scope, pmf_period=pmf_scope
        )


def test_missing_probability_mass_fails_closed():
    with pytest.raises(NHLTotalSettlementError, match="GOAL_PMF_NOT_NORMALIZED"):
        kalshi_hockey_totals_predicate({5: 0.48, 6: 0.06}, operator="BELOW",
                                      threshold=6, contract_period=SCOPE, pmf_period=SCOPE)


def test_nonpublishable_and_execution_disabled():
    p = predicate("ABOVE", 6)
    assert p.can_execute is False and p.probability_publishable is False
    assert CAN_EXECUTE is False and PROBABILITY_PUBLISHABLE is False
