import pytest

from prop_settlement import (
    SETTLEMENT_RULE_UNRESOLVED,
    SettlementRule,
    audit_exact_line,
    settle_prop_probability,
    settlement_self_acceptance,
)


def _rule(*, boundary="GT", equality="PUSH", void_mass=0.0):
    return SettlementRule(
        settlement_basis="FULL_GAME_STAT",
        boundary_operator=boundary,
        equality_treatment=equality,
        void_treatment="RETURN_STAKE",
        rule_version="TEST_V1",
        source="UNIT_TEST",
        void_probability_mass=void_mass,
    )


def test_more_integer_line_push_and_push_aware_ev():
    result = settle_prop_probability(
        direction="MORE",
        probability_more=0.55,
        probability_less=0.35,
        equality_probability=0.10,
        rule=_rule(),
        american_odds=-110,
    )
    assert result.status == "PASS"
    assert result.p_win == pytest.approx(0.55)
    assert result.p_loss == pytest.approx(0.35)
    assert result.p_push == pytest.approx(0.10)
    assert result.p_void == pytest.approx(0.0)
    assert result.expected_profit_per_unit_staked == pytest.approx(0.55 * (100 / 110) - 0.35)
    assert result.break_even_unconditional == pytest.approx(0.90 / (1 + 100 / 110))
    assert result.break_even_conditional_graded == pytest.approx(1 / (1 + 100 / 110))


def test_less_half_line_has_no_push():
    result = settle_prop_probability(
        direction="LESS",
        probability_more=0.44,
        probability_less=0.56,
        equality_probability=0.0,
        rule=_rule(boundary="LT"),
        american_odds=120,
    )
    assert result.status == "PASS"
    assert result.p_win == pytest.approx(0.56)
    assert result.p_loss == pytest.approx(0.44)
    assert result.p_push == 0.0


def test_equality_can_be_explicit_win_or_loss():
    win = settle_prop_probability(
        direction="MORE", probability_more=0.50, probability_less=0.40,
        equality_probability=0.10, rule=_rule(boundary="GE", equality="WIN"), american_odds=-105,
    )
    loss = settle_prop_probability(
        direction="MORE", probability_more=0.50, probability_less=0.40,
        equality_probability=0.10, rule=_rule(equality="LOSS"), american_odds=-105,
    )
    assert win.p_win == pytest.approx(0.60)
    assert loss.p_loss == pytest.approx(0.50)


def test_void_return_stake_scales_graded_masses_and_normalizes():
    result = settle_prop_probability(
        direction="LESS",
        probability_more=0.40,
        probability_less=0.60,
        equality_probability=0.0,
        rule=_rule(boundary="LT", void_mass=0.20),
        american_odds=120,
    )
    assert result.status == "PASS"
    assert result.p_win == pytest.approx(0.48)
    assert result.p_loss == pytest.approx(0.32)
    assert result.p_void == pytest.approx(0.20)
    assert result.p_win + result.p_loss + result.p_push + result.p_void == pytest.approx(1.0)


def test_unresolved_settlement_fails_closed():
    result = settle_prop_probability(
        direction="MORE", probability_more=0.55, probability_less=0.45,
        equality_probability=0.0, rule=None, american_odds=-110,
    )
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_RULE_UNRESOLVED
    assert result.can_execute is False


def test_wrong_boundary_direction_fails_closed():
    result = settle_prop_probability(
        direction="MORE", probability_more=0.55, probability_less=0.45,
        equality_probability=0.0, rule=_rule(boundary="LT"), american_odds=-110,
    )
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_RULE_UNRESOLVED


def test_exact_line_match_defaults_to_zero_business_tolerance_with_float_guard():
    assert audit_exact_line(candidate_line=5.5, quote_line=5.5)
    assert audit_exact_line(candidate_line=5.5, quote_line=5.5000000005)
    assert not audit_exact_line(candidate_line=5.5, quote_line=6.5)


def test_settlement_self_acceptance_fixture_is_green():
    assert settlement_self_acceptance() is True
