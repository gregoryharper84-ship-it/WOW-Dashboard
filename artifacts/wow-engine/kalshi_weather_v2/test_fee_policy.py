from decimal import Decimal

import pytest

from kalshi_weather_v2.fee_policy import (
    FeePolicyError,
    quote_new_order_single_fill_cash_fee,
    quote_trade_fee,
    resolve_fee_policy,
)


AS_OF = "2026-09-10T14:40:00Z"


def series(**overrides):
    value = {"ticker": "KXHIGHNY", "fee_type": "quadratic", "fee_multiplier": 1}
    value.update(overrides)
    return value


def event(**overrides):
    value = {
        "event_ticker": "KXHIGHNY-26SEP10",
        "series_ticker": "KXHIGHNY",
        "fee_type_override": None,
        "fee_multiplier_override": None,
    }
    value.update(overrides)
    return value


def market(**overrides):
    value = {
        "ticker": "KXHIGHNY-26SEP10-T80",
        "event_ticker": "KXHIGHNY-26SEP10",
        "fee_waiver_expiration_time": None,
    }
    value.update(overrides)
    return value


def test_live_weather_shape_resolves_quadratic_multiplier_one():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    assert policy.series_ticker == "KXHIGHNY"
    assert policy.event_ticker == "KXHIGHNY-26SEP10"
    assert policy.fee_type == "quadratic"
    assert policy.fee_multiplier == Decimal("1")
    assert policy.taker_coefficient == Decimal("0.07")
    assert policy.maker_coefficient is None
    assert policy.policy_source == "SERIES"
    assert policy.can_execute is False


def test_event_override_supersedes_series_policy_without_mutating_series_identity():
    policy = resolve_fee_policy(
        series=series(),
        event=event(fee_type_override="quadratic_with_maker_fees", fee_multiplier_override="2"),
        market=market(),
        resolved_at=AS_OF,
    )
    assert policy.fee_type == "quadratic_with_maker_fees"
    assert policy.fee_multiplier == Decimal("2")
    assert policy.taker_coefficient == Decimal("0.14")
    assert policy.maker_coefficient == Decimal("0.0350")
    assert policy.policy_source == "EVENT_OVERRIDE"


def test_generic_trade_quote_uses_six_decimal_fee_rounding_and_holds_cash_friction():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    quote = quote_trade_fee(policy, price="0.50", quantity="1.00")
    assert quote.raw_trade_fee == Decimal("0.017500")
    assert quote.trade_fee == Decimal("0.017500")
    assert quote.pre_cash_rounding_break_even == Decimal("0.5175")
    assert quote.cash_rounding_included is False
    assert quote.effective_break_even is None
    assert quote.friction_model_verified is False


def test_non_direct_single_fill_cent_alignment_reconstructs_cash_debit():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    quote = quote_new_order_single_fill_cash_fee(
        policy, price="0.50", quantity="1.00", balance_quantum="0.01"
    )
    assert quote.trade_fee == Decimal("0.017500")
    assert quote.rounding_fee == Decimal("0.002500")
    assert quote.net_fee == Decimal("0.020000")
    assert quote.effective_break_even == Decimal("0.520000")
    assert quote.balance_quantum == Decimal("0.01")
    assert quote.friction_model_verified is True
    assert quote.can_execute is False


def test_direct_single_fill_uses_direct_member_balance_precision():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    quote = quote_new_order_single_fill_cash_fee(
        policy, price="0.50", quantity="1.00", balance_quantum="0.0001"
    )
    assert quote.trade_fee == Decimal("0.017500")
    assert quote.rounding_fee == Decimal("0.000000")
    assert quote.net_fee == Decimal("0.017500")
    assert quote.effective_break_even == Decimal("0.517500")


def test_unknown_balance_precision_fails_closed_instead_of_guessing_member_type():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    with pytest.raises(FeePolicyError) as exc:
        quote_new_order_single_fill_cash_fee(
            policy, price="0.50", quantity="1.00", balance_quantum="0.001"
        )
    assert exc.value.code == "FEE_POLICY_UNRESOLVED"
    assert "BALANCE_QUANTUM_UNSUPPORTED:0.001" in exc.value.blockers


def test_trade_fee_rounds_up_to_nearest_six_decimal_dollar():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    quote = quote_trade_fee(policy, price="0.33", quantity="0.30")
    assert quote.raw_trade_fee == Decimal("0.00464310")
    assert quote.trade_fee == Decimal("0.004644")


def test_quadratic_policy_has_zero_maker_trade_fee():
    policy = resolve_fee_policy(series=series(), event=event(), market=market(), resolved_at=AS_OF)
    quote = quote_trade_fee(policy, price="0.50", quantity="5", liquidity_role="MAKER")
    assert quote.trade_fee == Decimal("0")


def test_future_series_fee_change_is_recorded_without_replacing_current_policy_early():
    policy = resolve_fee_policy(
        series=series(),
        event=event(),
        market=market(),
        fee_changes=(
            {
                "series_ticker": "KXHIGHNY",
                "fee_type": "quadratic_with_maker_fees",
                "fee_multiplier": 2,
                "scheduled_ts": "2026-09-11T00:00:00Z",
            },
        ),
        resolved_at=AS_OF,
    )
    assert policy.fee_type == "quadratic"
    assert policy.next_scheduled_change_at == "2026-09-11T00:00:00Z"


def test_effective_series_fee_change_that_contradicts_current_series_fails_closed():
    with pytest.raises(FeePolicyError) as exc:
        resolve_fee_policy(
            series=series(),
            event=event(),
            market=market(),
            fee_changes=(
                {
                    "series_ticker": "KXHIGHNY",
                    "fee_type": "quadratic_with_maker_fees",
                    "fee_multiplier": 2,
                    "scheduled_ts": "2026-09-10T13:00:00Z",
                },
            ),
            resolved_at=AS_OF,
        )
    assert exc.value.code == "FEE_POLICY_STALE"
    assert "CURRENT_FEE_POLICY_CONTRADICTS_EFFECTIVE_SERIES_CHANGE" in exc.value.blockers


def test_future_event_override_change_sets_earliest_known_transition():
    policy = resolve_fee_policy(
        series=series(),
        event=event(),
        market=market(),
        fee_changes=(
            {
                "series_ticker": "KXHIGHNY",
                "fee_type": "quadratic",
                "fee_multiplier": 2,
                "scheduled_ts": "2026-09-12T00:00:00Z",
            },
        ),
        event_fee_changes=(
            {
                "event_ticker": "KXHIGHNY-26SEP10",
                "series_ticker": "KXHIGHNY",
                "fee_type_override": "quadratic_with_maker_fees",
                "fee_multiplier_override": 1,
                "scheduled_ts": "2026-09-11T00:00:00Z",
            },
        ),
        resolved_at=AS_OF,
    )
    assert policy.next_scheduled_change_at == "2026-09-11T00:00:00Z"


def test_effective_event_override_clear_must_match_current_event_state():
    with pytest.raises(FeePolicyError) as exc:
        resolve_fee_policy(
            series=series(),
            event=event(fee_type_override="quadratic_with_maker_fees", fee_multiplier_override=1),
            market=market(),
            event_fee_changes=(
                {
                    "event_ticker": "KXHIGHNY-26SEP10",
                    "series_ticker": "KXHIGHNY",
                    "fee_type_override": None,
                    "fee_multiplier_override": None,
                    "scheduled_ts": "2026-09-10T13:00:00Z",
                },
            ),
            resolved_at=AS_OF,
        )
    assert exc.value.code == "FEE_POLICY_STALE"
    assert "CURRENT_FEE_POLICY_CONTRADICTS_EFFECTIVE_EVENT_CHANGE" in exc.value.blockers


def test_active_fee_waiver_does_not_silently_become_zero_fee():
    with pytest.raises(FeePolicyError) as exc:
        resolve_fee_policy(
            series=series(),
            event=event(),
            market=market(fee_waiver_expiration_time="2026-09-11T00:00:00Z"),
            resolved_at=AS_OF,
        )
    assert exc.value.code == "FEE_POLICY_UNRESOLVED"
    assert "ACTIVE_FEE_WAIVER_REQUIRES_EXACT_WAIVER_SEMANTICS" in exc.value.blockers


def test_flat_fee_type_fails_closed_without_explicit_flat_amount():
    with pytest.raises(FeePolicyError) as exc:
        resolve_fee_policy(series=series(fee_type="flat"), event=event(), market=market(), resolved_at=AS_OF)
    assert exc.value.code == "FEE_POLICY_UNRESOLVED"
    assert "FLAT_FEE_AMOUNT_NOT_EXPOSED_BY_SERIES_POLICY" in exc.value.blockers


def test_identity_mismatch_fails_closed():
    with pytest.raises(FeePolicyError) as exc:
        resolve_fee_policy(
            series=series(), event=event(series_ticker="OTHER"), market=market(), resolved_at=AS_OF
        )
    assert exc.value.code == "FEE_POLICY_IDENTITY_MISMATCH"
