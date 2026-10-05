import pytest

from kalshi_weather_v2.models import ContractSnapshot
from kalshi_weather_v2.settlement_digital_twin import (
    PredicateKind,
    SettlementPredicate,
    SettlementState,
    SettlementTwinError,
    build_atomic_temperature_twin,
)


def _contract(**overrides):
    base = dict(
        market_title="DFW high",
        contract_title="82F or above",
        ticker="KXHIGHDFW-TEST",
        lane="DAILY_HIGH_TEMPERATURE",
        yes_condition="82F or above",
        no_condition="below 82F",
        location="Dallas",
        metric="daily_max_temperature",
        units="F",
        observation_window="2026-10-05 local day",
        timezone="America/Chicago",
        settlement_source="The Weather Company",
        settlement_station_id="KDFW",
        settlement_station_name="Dallas/Fort Worth International",
        rounding_convention="contract exact",
        trace_measurement_rules="none",
        market_close_time="2026-10-06T05:00:00Z",
        rule_snapshot_id="rule-1",
        threshold_lower=82.0,
        threshold_upper=None,
        lower_inclusive=True,
        upper_inclusive=True,
        settlement_location_code="KDFW",
    )
    base.update(overrides)
    return ContractSnapshot(**base)


def test_atomic_above_predicate_and_complement_are_exact():
    twin = build_atomic_temperature_twin(
        twin_snapshot_id="twin-1",
        contract=_contract(),
        built_at="2026-10-05T18:00:00Z",
        state_as_of="2026-10-05T18:00:00Z",
        settlement_source_url="https://example.invalid/rules",
        source_snapshot_ids=("obs-1",),
    )
    assert twin.yes_predicate.kind is PredicateKind.ABOVE
    assert twin.yes_predicate.matches(82.0)
    assert not twin.no_predicate.matches(82.0)
    assert twin.no_predicate.matches(81.99)
    assert twin.settlement_state is SettlementState.OPEN
    assert twin.market_data_used_as_weather_probability_input is False
    assert twin.can_execute is False


def test_daily_high_crossing_above_threshold_is_irreversible_yes_lock():
    twin = build_atomic_temperature_twin(
        twin_snapshot_id="twin-2",
        contract=_contract(),
        built_at="2026-10-05T18:00:00Z",
        state_as_of="2026-10-05T18:00:00Z",
        settlement_source_url=None,
        source_snapshot_ids=("obs-1",),
        observed_extreme=83.0,
    )
    assert twin.settlement_state is SettlementState.LOCKED_YES
    assert twin.impossible_outcomes == ("NO",)
    assert twin.remaining_outcomes == ("YES",)


def test_daily_high_below_threshold_remains_open_because_future_warming_is_possible():
    twin = build_atomic_temperature_twin(
        twin_snapshot_id="twin-3",
        contract=_contract(),
        built_at="2026-10-05T18:00:00Z",
        state_as_of="2026-10-05T18:00:00Z",
        settlement_source_url=None,
        source_snapshot_ids=("obs-1",),
        observed_extreme=79.0,
    )
    assert twin.settlement_state is SettlementState.OPEN
    assert twin.remaining_outcomes == ("YES", "NO")


def test_market_contamination_and_execution_are_forbidden():
    yes = SettlementPredicate(kind=PredicateKind.ABOVE, lower=82.0)
    with pytest.raises(SettlementTwinError) as exc:
        from kalshi_weather_v2.settlement_digital_twin import SettlementDigitalTwin
        SettlementDigitalTwin(
            twin_snapshot_id="t",
            rule_snapshot_id="r",
            ticker="k",
            built_at="2026-10-05T18:00:00Z",
            state_as_of="2026-10-05T18:00:00Z",
            settlement_source_name="s",
            settlement_source_url=None,
            settlement_location_code="KDFW",
            settlement_station_id="KDFW",
            station_timezone="America/Chicago",
            observation_window="day",
            metric="daily_max_temperature",
            units="F",
            rounding_convention="exact",
            yes_predicate=yes,
            no_predicate=SettlementPredicate(kind=PredicateKind.BELOW, upper=82.0, upper_inclusive=False),
            possible_outcomes=("YES", "NO"),
            impossible_outcomes=(),
            remaining_outcomes=("YES", "NO"),
            observed_value=None,
            observed_extreme=None,
            settlement_state=SettlementState.OPEN,
            source_snapshot_ids=(),
            blockers=(),
            warnings=(),
            market_data_used_as_weather_probability_input=True,
        )
    assert exc.value.code == "MARKET_DATA_PROBABILITY_CONTAMINATION"
