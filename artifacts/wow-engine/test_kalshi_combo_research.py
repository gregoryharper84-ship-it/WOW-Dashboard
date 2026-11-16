"""Frozen quote diagnostics and actual visible Kalshi combo accounting."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from v17.kalshi_combo_research import (
    CAN_EXECUTE, KALSHI_COMBOS_ALLOWED, FrozenLeg, FrozenComboQuote,
    SettledCombo, audit_settled, evaluate_frozen_quote,
    ResearchLegSettlement, grade_research_slip,
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
        FrozenLeg("a", "NBA:eventA:ML:YES", "eventA", start, quote_at-timedelta(minutes=2), .78, .72, "PASS",
                  market_family="MONEYLINE", model_market_family="MONEYLINE", model_class="CERTIFIED_FITTED",
                  contract_settlement_rule="FULL_GAME_ML_OVERTIME", event_status="SCHEDULED",
                  event_status_source="OFFICIAL_SCHEDULE", event_status_receipt_id="official-A",
                  event_status_at_utc=quote_at-timedelta(minutes=3)),
        FrozenLeg("b", "NBA:eventB:ML:YES", "eventB", start, quote_at-timedelta(minutes=2), .80, .73, "PASS",
                  market_family="MONEYLINE", model_market_family="MONEYLINE", model_class="CERTIFIED_FITTED",
                  contract_settlement_rule="FULL_GAME_ML_OVERTIME", event_status="SCHEDULED",
                  event_status_source="OFFICIAL_SCHEDULE", event_status_receipt_id="official-B",
                  event_status_at_utc=quote_at-timedelta(minutes=3)),
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
    assert verdict.status == "RESEARCH_HOLD_NO_CONSERVATIVE_EDGE"
    assert "NO_CONSERVATIVE_ECONOMIC_EDGE" in verdict.blockers
    assert verdict.conservative_return_diagnostic == Decimal("-20.000")


def test_leg_lower_bound_zero_is_valid():
    quote = example_quote()
    leg0 = replace(quote.legs[0], calibrated_lower_bound=0.0)
    verdict = evaluate_frozen_quote(replace(quote, legs=(leg0, quote.legs[1]), joint_lower_bound=0.0))
    assert verdict.status == "RESEARCH_HOLD_NO_CONSERVATIVE_EDGE"


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


@pytest.mark.parametrize("field", ["receipt_id", "exact_market_identity"])
def test_unhashable_leg_identity_results_in_typed_hold(field):
    quote = example_quote()
    malformed = replace(quote.legs[0], **{field: []})
    verdict = evaluate_frozen_quote(replace(quote, legs=(malformed, quote.legs[1])))
    assert verdict.status == "RESEARCH_HOLD_INSUFFICIENT_EVIDENCE"
    assert "LEG_RECEIPT_EVENT_IDENTITY_MISSING" in verdict.blockers
    assert verdict.conservative_return_diagnostic is None


@pytest.mark.parametrize("status", ["POSTPONED", "CANCELLED", "IN_PROGRESS", "FINAL", ""])
def test_live_postponed_cancelled_or_unknown_event_never_qualifies(status):
    q = example_quote()
    bad_leg = replace(q.legs[0], event_status=status)
    verdict = evaluate_frozen_quote(replace(q, legs=(bad_leg, q.legs[1])))
    assert "OFFICIAL_PREGAME_STATUS_INVALID" in verdict.blockers
    assert verdict.conservative_return_diagnostic is None
    assert verdict.can_execute is False


def test_missing_or_stale_official_schedule_receipt_is_held():
    q = example_quote()
    for change, reason in [
        ({"event_status_source": "SPORTSBOOK"}, "OFFICIAL_STATUS_RECEIPT_UNVERIFIED"),
        ({"event_status_receipt_id": ""}, "OFFICIAL_STATUS_RECEIPT_UNVERIFIED"),
        ({"event_status_at_utc": None}, "PREGAME_STATUS_SNAPSHOT_MISSING"),
        ({"event_status_at_utc": q.quote_at_utc - timedelta(hours=2)}, "PREGAME_STATUS_STALE_OR_FUTURE"),
        ({"event_status_at_utc": q.quote_at_utc + timedelta(seconds=1)}, "PREGAME_STATUS_STALE_OR_FUTURE"),
    ]:
        changed = replace(q.legs[0], **change)
        verdict = evaluate_frozen_quote(replace(q, legs=(changed, q.legs[1])))
        assert reason in verdict.blockers


def test_moneyline_model_cannot_approve_a_spread_or_total():
    q = example_quote()
    for family in ("SPREAD", "TOTAL"):
        changed = replace(q.legs[0], market_family=family, market_line=Decimal("-3.5"))
        verdict = evaluate_frozen_quote(replace(q, legs=(changed, q.legs[1])))
        assert "MARKET_SPECIFIC_FITTED_MODEL_UNVERIFIED" in verdict.blockers
        assert verdict.conservative_return_diagnostic is None


def test_spread_total_exact_line_required_even_with_fitted_specialist():
    q = example_quote()
    for family in ("SPREAD", "TOTAL"):
        changed = replace(q.legs[0], market_family=family,
                          model_market_family=family, market_line=None)
        verdict = evaluate_frozen_quote(replace(q, legs=(changed, q.legs[1])))
        assert "MARKET_SPECIFIC_EXACT_LINE_UNVERIFIED" in verdict.blockers


def test_uncertified_or_unknown_market_model_fails_closed():
    q = example_quote()
    for change in (
        {"market_family": ""},
        {"model_class": "RESEARCH_CHALLENGER"},
        {"model_market_family": "TOTAL"},
        {"contract_settlement_rule": ""},
    ):
        changed = replace(q.legs[0], **change)
        verdict = evaluate_frozen_quote(replace(q, legs=(changed, q.legs[1])))
        assert verdict.status == "RESEARCH_HOLD_INSUFFICIENT_EVIDENCE"
        assert verdict.conservative_return_diagnostic is None


def test_same_event_different_markets_are_not_assumed_independent():
    q = example_quote()
    changed = replace(q.legs[1], event_id=q.legs[0].event_id)
    verdict = evaluate_frozen_quote(replace(q, legs=(q.legs[0], changed)))
    assert "SAME_EVENT_CROSS_MARKET_DEPENDENCY_UNVERIFIED" in verdict.blockers


def test_conservative_economic_edge_dual_gate():
    q = replace(example_quote(), joint_lower_bound=.4)
    verdict = evaluate_frozen_quote(q)
    assert verdict.status == "RESEARCH_HOLD_NO_CONSERVATIVE_EDGE"
    assert "NO_CONSERVATIVE_ECONOMIC_EDGE" in verdict.blockers
    assert verdict.conservative_return_diagnostic == Decimal("-4.000")


def sample_settlement(event_id, market_identity, outcome):
    start = datetime(2026, 11, 15, 18, tzinfo=timezone.utc)
    return ResearchLegSettlement(
        event_id=event_id, market_identity=market_identity,
        pregame_receipt_id=f"pregame-{event_id}-{market_identity}",
        pregame_locked_at_utc=start-timedelta(hours=2),
        event_start_utc=start,
        official_outcome=outcome,
        official_result_receipt_id=f"official-{event_id}",
        official_result_at_utc=start+timedelta(hours=4),
        exact_contract_settlement_rule="FULL_GAME_OVERTIME_INCLUDED",
    )


def test_frozen_postmortem_win_loss_separate_from_actual_trades():
    grade = grade_research_slip([
        sample_settlement("game-A", "ML", "WIN"),
        sample_settlement("game-B", "TOTAL", "LOSS"),
    ])
    assert grade.status == "RESEARCH_SLIP_LOST"
    assert (grade.wins, grade.losses, grade.pending) == (1, 1, 0)
    assert not grade.trade_submission_verified and not grade.realized_pnl_verified
    assert not grade.official_outcomes_attested and not grade.can_execute


def test_postponed_push_and_missing_result_remain_unresolved():
    grade = grade_research_slip([
        sample_settlement("game-A", "ML", "WIN"),
        sample_settlement("game-B", "TOTAL", "POSTPONED"),
        sample_settlement("game-C", "SPREAD", "PUSH"),
        sample_settlement("game-D", "ML", "PENDING"),
    ])
    assert grade.status == "RESEARCH_SLIP_UNRESOLVED"
    assert grade.settlement_dependent == 2 and grade.pending == 1


def test_no_retroactive_win_from_unverified_result_or_started_model():
    good = sample_settlement("game-A", "ML", "WIN")
    bad = replace(sample_settlement("game-B", "SPREAD", "WIN"),
                  pregame_locked_at_utc=good.event_start_utc,
                  official_result_receipt_id=None)
    grade = grade_research_slip([good, bad])
    assert grade.status == "RESEARCH_GRADE_HELD"
    assert "PREGAME_RECEIPT_LOOKAHEAD_INVALID" in grade.blockers
    assert "INDEPENDENT_RESULT_OR_SETTLEMENT_UNVERIFIED" in grade.blockers
    assert grade.wins == 1 and grade.pending == 1


def test_repeated_frozen_leg_cannot_count_twice():
    a = sample_settlement("game-A", "ML", "WIN")
    grade = grade_research_slip([a,a])
    assert grade.status == "RESEARCH_GRADE_HELD"
    assert "DUPLICATE_FROZEN_LEG" in grade.blockers
