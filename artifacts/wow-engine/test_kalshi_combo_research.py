"""Frozen quote diagnostics and actual visible Kalshi combo accounting."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from v17.kalshi_combo_research import (
    CAN_EXECUTE, KALSHI_COMBOS_ALLOWED, FrozenLeg, FrozenComboQuote,
    SettledCombo, audit_settled, evaluate_frozen_quote,
)


def test_actual_visible_eight_record_math_not_date_verified():
    costs = ["5.00", "10.00", "39.99", "49.99", "60.99", "9.93", "99.97", "25.00"]
    payouts = ["0", "20.22", "86.13", "0", "0", "0", "0", "0"]
    counts = [2, 2, 2, 2, 2, 7, 5, 2]
    rows = [SettledCombo(str(i), n, Decimal(c), Decimal(p)) for i,(n,c,p) in enumerate(zip(counts,costs,payouts))]
    result = audit_settled(rows)
    assert result.positions == 8
    assert result.winning_positions == 2
    assert result.total_cost == Decimal("300.87")
    assert result.total_payout == Decimal("106.35")
    assert result.net == Decimal("-194.52")
    assert not result.dates_verified
    assert not result.leg_failure_causes_verified
    assert result.by_leg_count[2]["net"] == Decimal("-84.62")
    assert result.by_leg_count[5]["net"] == Decimal("-99.97")
    assert result.by_leg_count[7]["net"] == Decimal("-9.93")


def example_quote():
    start = datetime(2026, 11, 10, 23, tzinfo=timezone.utc)
    quote_at = start - timedelta(minutes=12)
    legs = (
        FrozenLeg("a", "NBA:eventA:ML:YES", "eventA", start, quote_at-timedelta(minutes=2), .78, .72, "PASS"),
        FrozenLeg("b", "NBA:eventB:ML:YES", "eventB", start, quote_at-timedelta(minutes=2), .80, .73, "PASS"),
    )
    return FrozenComboQuote("demo", legs, .65, .58, "j-123", "PASS", "simulation:fit-v4", "rfq-1", "KALSHI_RFQ", quote_at, quote_at+timedelta(seconds=20), Decimal("20.00"), Decimal("40.00"), joint_receipt_asof_utc=quote_at-timedelta(minutes=1))


def test_passing_math_is_still_non_executable_hold():
    assert not CAN_EXECUTE and not KALSHI_COMBOS_ALLOWED
    result = evaluate_frozen_quote(example_quote())
    assert result.status == "RESEARCH_HOLD_KALSHI_RECOVERY_ACTIVE"
    assert not result.can_execute
    assert result.break_even_probability == .5
    assert result.conservative_return_diagnostic == Decimal("3.2000")


def test_missing_calibration_and_dependence_fail_closed():
    q = example_quote()
    result = evaluate_frozen_quote(replace(q, joint_receipt_id=None, dependency_method=None))
    assert "JOINT_MODEL_OR_DEPENDENCE_UNVERIFIED" in result.blockers
    assert result.break_even_probability is None


def test_stale_rfqs_fail_closed():
    q = example_quote()
    result = evaluate_frozen_quote(replace(q, evaluated_at_utc=q.quote_at_utc + timedelta(minutes=6)))
    assert "RFQ_QUOTE_STALE_OR_FUTURE" in result.blockers


def test_invalid_joint_bounds_fail_closed():
    q = example_quote()
    result = evaluate_frozen_quote(replace(q, joint_probability=.90))
    assert "JOINT_OUTSIDE_FRECHET_BOUNDS" in result.blockers


def test_five_leg_no_upgrade():
    q = example_quote()
    legs = tuple(replace(q.legs[0], receipt_id=f"receipt-{i}", exact_market_identity=f"game-{i}") for i in range(5))
    result = evaluate_frozen_quote(replace(q, legs=legs, joint_probability=.5, joint_lower_bound=.4))
    assert "COMBO_SIZE_EXCEEDS_RESEARCH_PAIR_DEFAULT" in result.blockers
    assert not result.can_execute


def test_duplicate_legs_blocked():
    q = example_quote()
    result = evaluate_frozen_quote(replace(q, legs=(q.legs[0], q.legs[0])))
    assert "DUPLICATE_LEG_OR_RECEIPT" in result.blockers


def test_no_independence_guess():
    q = example_quote()
    result = evaluate_frozen_quote(replace(q, joint_probability=None, joint_lower_bound=None))
    assert "JOINT_PROBABILITY_MISSING_OR_INVALID" in result.blockers
    assert result.conservative_return_diagnostic is None


def test_invalid_money_rejected():
    with pytest.raises(ValueError):
        audit_settled([SettledCombo("x", 2, Decimal("-1"), Decimal("0"))])


def test_joint_lower_bound_zero_keeps_conservative_loss_diagnostic():
    quote = replace(example_quote(), joint_lower_bound=0.0)
    verdict = evaluate_frozen_quote(quote)
    assert verdict.status == "RESEARCH_HOLD_KALSHI_RECOVERY_ACTIVE"
    assert verdict.conservative_return_diagnostic == Decimal("-20.000")


def test_leg_lower_bound_zero_is_valid():
    quote = example_quote()
    leg0 = replace(quote.legs[0], calibrated_lower_bound=0.0)
    verdict = evaluate_frozen_quote(replace(quote, legs=(leg0, quote.legs[1]), joint_lower_bound=0.0))
    assert verdict.status == "RESEARCH_HOLD_KALSHI_RECOVERY_ACTIVE"


@pytest.mark.parametrize("field", ["receipt_id", "exact_market_identity", "event_id"])
def test_leg_missing_identity_fails_before_numeric_diagnostics(field):
    quote = example_quote()
    bad_leg = replace(quote.legs[0], **{field: ""})
    verdict = evaluate_frozen_quote(replace(quote, legs=(bad_leg, quote.legs[1])))
    assert "LEG_RECEIPT_EVENT_IDENTITY_MISSING" in verdict.blockers
    assert verdict.conservative_return_diagnostic is None


def test_joint_asof_is_required():
    verdict = evaluate_frozen_quote(replace(example_quote(), joint_receipt_asof_utc=None))
    assert "JOINT_RECEIPT_ASOF_UNVERIFIED" in verdict.blockers


def test_joint_receipt_cannot_look_ahead():
    quote = example_quote()
    verdict = evaluate_frozen_quote(replace(quote, joint_receipt_asof_utc=quote.legs[0].event_start_utc+timedelta(minutes=1)))
    assert "JOINT_RECEIPT_LOOKAHEAD_INVALID" in verdict.blockers
