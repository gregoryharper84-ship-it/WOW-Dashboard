from decimal import Decimal

from kalshi_weather_v2.fee_policy import resolve_fee_policy


AS_OF = "2026-09-10T14:40:00Z"


def _series():
    return {"ticker": "KXHIGHNY", "fee_type": "quadratic", "fee_multiplier": 1}


def _event(**overrides):
    event = {
        "event_ticker": "KXHIGHNY-26SEP10",
        "series_ticker": "KXHIGHNY",
        "fee_type_override": None,
        "fee_multiplier_override": None,
    }
    event.update(overrides)
    return event


def _market():
    return {
        "ticker": "KXHIGHNY-26SEP10-T80",
        "event_ticker": "KXHIGHNY-26SEP10",
        "fee_waiver_expiration_time": None,
    }


def test_older_series_history_does_not_false_stale_when_latest_effective_matches_current():
    policy = resolve_fee_policy(
        series=_series(),
        event=_event(),
        market=_market(),
        fee_changes=(
            {
                "series_ticker": "KXHIGHNY",
                "fee_type": "quadratic_with_maker_fees",
                "fee_multiplier": 2,
                "scheduled_ts": "2026-08-01T00:00:00Z",
            },
            {
                "series_ticker": "KXHIGHNY",
                "fee_type": "quadratic",
                "fee_multiplier": 1,
                "scheduled_ts": "2026-09-01T00:00:00Z",
            },
        ),
        resolved_at=AS_OF,
    )
    assert policy.fee_type == "quadratic"
    assert policy.fee_multiplier == Decimal("1")


def test_older_event_override_history_does_not_false_stale_after_latest_clear():
    policy = resolve_fee_policy(
        series=_series(),
        event=_event(),
        market=_market(),
        event_fee_changes=(
            {
                "event_ticker": "KXHIGHNY-26SEP10",
                "series_ticker": "KXHIGHNY",
                "fee_type_override": "quadratic_with_maker_fees",
                "fee_multiplier_override": 2,
                "scheduled_ts": "2026-09-08T00:00:00Z",
            },
            {
                "event_ticker": "KXHIGHNY-26SEP10",
                "series_ticker": "KXHIGHNY",
                "fee_type_override": None,
                "fee_multiplier_override": None,
                "scheduled_ts": "2026-09-09T00:00:00Z",
            },
        ),
        resolved_at=AS_OF,
    )
    assert policy.policy_source == "SERIES"
    assert policy.fee_type == "quadratic"


def test_latest_effective_event_override_matches_current_override_state():
    policy = resolve_fee_policy(
        series=_series(),
        event=_event(fee_type_override="quadratic_with_maker_fees", fee_multiplier_override=2),
        market=_market(),
        event_fee_changes=(
            {
                "event_ticker": "KXHIGHNY-26SEP10",
                "series_ticker": "KXHIGHNY",
                "fee_type_override": None,
                "fee_multiplier_override": None,
                "scheduled_ts": "2026-09-08T00:00:00Z",
            },
            {
                "event_ticker": "KXHIGHNY-26SEP10",
                "series_ticker": "KXHIGHNY",
                "fee_type_override": "quadratic_with_maker_fees",
                "fee_multiplier_override": 2,
                "scheduled_ts": "2026-09-09T00:00:00Z",
            },
        ),
        resolved_at=AS_OF,
    )
    assert policy.policy_source == "EVENT_OVERRIDE"
    assert policy.fee_type == "quadratic_with_maker_fees"
    assert policy.fee_multiplier == Decimal("2")
