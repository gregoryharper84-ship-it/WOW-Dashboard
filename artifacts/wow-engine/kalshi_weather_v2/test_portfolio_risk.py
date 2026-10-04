import pytest

from kalshi_weather_v2.portfolio_risk import (
    DependenceMode,
    PortfolioPosition,
    PortfolioRiskError,
    PortfolioScenarioEngine,
    PositionSide,
    WeatherEventDescriptor,
    WeatherScenario,
    build_weather_event,
    weather_event_key,
)


def event(
    *,
    location="CLIDFW",
    observation_window="2026-10-04 local day",
    region_id="DFW",
    factor_ids=("SYNOPTIC_REGIME",),
):
    return build_weather_event(
        lane="DAILY_HIGH_TEMPERATURE",
        settlement_source="The Weather Company",
        settlement_location_code=location,
        observation_window=observation_window,
        metric="daily_max_temperature",
        units="F",
        region_id=region_id,
        factor_ids=factor_ids,
    )


def position(
    position_id,
    *,
    event_obj=None,
    side=PositionSide.YES,
    quantity=10,
    cost=0.50,
    lower=85.0,
    upper=None,
    lower_inclusive=True,
    upper_inclusive=True,
    model_p_yes=0.65,
    prediction_time="2026-10-04T16:00:00Z",
    market_time="2026-10-04T16:01:00Z",
):
    return PortfolioPosition(
        position_id=position_id,
        ticker=f"TICKER-{position_id}",
        rule_snapshot_id=f"rule-{position_id}",
        prediction_id=f"prediction-{position_id}",
        market_snapshot_id=f"market-{position_id}",
        prediction_time=prediction_time,
        market_time=market_time,
        event=event_obj or event(),
        side=side,
        quantity=quantity,
        entry_cost_per_contract=cost,
        threshold_lower=lower,
        threshold_upper=upper,
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
        model_p_yes=model_p_yes,
    )


def scenario(scenario_id, value, *, event_obj=None, weight=1.0, factors=None, available_at="2026-10-04T16:02:00Z"):
    evt = event_obj or event()
    return WeatherScenario(
        scenario_id=scenario_id,
        available_at=available_at,
        weight=weight,
        event_values={evt.event_key: value},
        factor_states=factors or {},
    )


def test_weather_event_identity_is_deterministic_and_case_normalized():
    a = weather_event_key(
        lane="daily_high_temperature",
        settlement_source="The Weather Company",
        settlement_location_code="clidfw",
        observation_window="2026-10-04 local day",
        metric="DAILY_MAX_TEMPERATURE",
        units="f",
    )
    b = event().event_key
    assert a == b


def test_same_station_ladder_uses_exact_nested_outcomes_not_independent_bets():
    evt = event(factor_ids=())
    positions = (
        position("85", event_obj=evt, lower=85, cost=0.40),
        position("86", event_obj=evt, lower=86, cost=0.30),
        position("87", event_obj=evt, lower=87, cost=0.20),
    )
    scenarios = (
        scenario("s84", 84, event_obj=evt, weight=1),
        scenario("s85", 85, event_obj=evt, weight=2),
        scenario("s86", 86, event_obj=evt, weight=4),
        scenario("s87", 87, event_obj=evt, weight=3),
    )
    result = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=positions,
        scenarios=scenarios,
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
    )
    by_id = {row.scenario_id: row for row in result.scenario_results}
    assert by_id["s84"].position_pnl["85"] == pytest.approx(-4.0)
    assert by_id["s85"].position_pnl["85"] == pytest.approx(6.0)
    assert by_id["s85"].position_pnl["86"] == pytest.approx(-3.0)
    assert by_id["s86"].position_pnl["85"] == pytest.approx(6.0)
    assert by_id["s86"].position_pnl["86"] == pytest.approx(7.0)
    assert by_id["s86"].position_pnl["87"] == pytest.approx(-2.0)
    assert by_id["s87"].position_pnl["87"] == pytest.approx(8.0)
    assert len(result.event_exposures) == 1
    assert result.max_event_concentration == pytest.approx(1.0)
    assert result.can_execute is False


def test_between_and_no_side_settlement_predicates_are_exact():
    evt = event(factor_ids=())
    yes_bucket = position("bucket", event_obj=evt, lower=85, upper=86, cost=0.5)
    no_bucket = position("no-bucket", event_obj=evt, side=PositionSide.NO, lower=85, upper=86, cost=0.5)
    assert yes_bucket.yes_outcome(85) is True
    assert yes_bucket.yes_outcome(86) is True
    assert yes_bucket.yes_outcome(87) is False
    assert yes_bucket.scenario_pnl(85) == pytest.approx(5.0)
    assert no_bucket.scenario_pnl(85) == pytest.approx(-5.0)
    assert no_bucket.scenario_pnl(87) == pytest.approx(5.0)


def test_cross_event_portfolio_requires_explicit_regional_factor_scenarios():
    dfw = event()
    hou = event(location="CLIHOU", region_id="HOU")
    positions = (position("dfw", event_obj=dfw), position("hou", event_obj=hou))
    scenarios = (
        WeatherScenario(
            scenario_id="ridge",
            available_at="2026-10-04T16:02:00Z",
            weight=1,
            event_values={dfw.event_key: 90, hou.event_key: 91},
            factor_states={"SYNOPTIC_REGIME": "RIDGE"},
        ),
    )
    with pytest.raises(PortfolioRiskError, match="CROSS_EVENT_DEPENDENCE_UNSUPPORTED"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=positions,
            scenarios=scenarios,
            dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        )


def test_regional_factor_mode_requires_region_and_factor_mapping():
    dfw = event(factor_ids=())
    hou = event(location="CLIHOU", region_id="HOU", factor_ids=())
    with pytest.raises(PortfolioRiskError, match="REGIONAL_EVENT_FACTOR_IDS_MISSING"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("dfw", event_obj=dfw), position("hou", event_obj=hou)),
            scenarios=(
                WeatherScenario(
                    scenario_id="s",
                    available_at="2026-10-04T16:02:00Z",
                    weight=1,
                    event_values={dfw.event_key: 90, hou.event_key: 90},
                    factor_states={},
                ),
            ),
            dependence_mode=DependenceMode.REGIONAL_FACTOR_SCENARIOS,
        )


def test_regional_factor_scenarios_produce_explainable_factor_state_risk():
    dfw = event(region_id="NORTH_TX", factor_ids=("SYNOPTIC_REGIME", "RIDGE_STRENGTH"))
    hou = event(location="CLIHOU", region_id="GULF_TX", factor_ids=("SYNOPTIC_REGIME", "RIDGE_STRENGTH"))
    positions = (
        position("dfw", event_obj=dfw, lower=95, cost=0.40, model_p_yes=0.60),
        position("hou", event_obj=hou, lower=96, cost=0.35, model_p_yes=0.55),
    )
    scenarios = (
        WeatherScenario(
            scenario_id="strong-ridge",
            available_at="2026-10-04T16:02:00Z",
            weight=3,
            event_values={dfw.event_key: 97, hou.event_key: 98},
            factor_states={"SYNOPTIC_REGIME": "RIDGE", "RIDGE_STRENGTH": "STRONG"},
        ),
        WeatherScenario(
            scenario_id="weak-ridge",
            available_at="2026-10-04T16:02:30Z",
            weight=1,
            event_values={dfw.event_key: 93, hou.event_key: 94},
            factor_states={"SYNOPTIC_REGIME": "RIDGE", "RIDGE_STRENGTH": "WEAK"},
        ),
    )
    result = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=positions,
        scenarios=scenarios,
        dependence_mode=DependenceMode.REGIONAL_FACTOR_SCENARIOS,
        fractional_kelly_multiplier=0.25,
    )
    assert result.expected_pnl == pytest.approx(7.5)
    assert result.probability_of_loss == pytest.approx(0.25)
    assert result.max_region_concentration == pytest.approx(4.0 / 7.5)
    assert {item.exposure_key for item in result.factor_exposures} == {"RIDGE_STRENGTH", "SYNOPTIC_REGIME"}
    state = {(item.factor_id, item.factor_state): item for item in result.factor_state_risk}
    assert state[("RIDGE_STRENGTH", "STRONG")].conditional_probability == pytest.approx(0.75)
    assert state[("RIDGE_STRENGTH", "WEAK")].worst_case_pnl < 0
    assert "REGIONAL_DEPENDENCE_SCENARIO_BASED_NOT_COPULA_ASSUMED" in result.warnings
    assert "FRACTIONAL_KELLY_RESEARCH_ONLY_NOT_PORTFOLIO_OPTIMIZED" in result.warnings


def test_fractional_kelly_is_research_only_and_has_no_implicit_default():
    evt = event(factor_ids=())
    pos = position("yes", event_obj=evt, cost=0.40, model_p_yes=0.60)
    scenarios = (scenario("s", 90, event_obj=evt),)
    no_request = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(pos,),
        scenarios=scenarios,
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
    )
    assert no_request.kelly_research[0].status == "NOT_REQUESTED"
    assert no_request.kelly_research[0].research_fraction is None

    requested = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(pos,),
        scenarios=scenarios,
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        fractional_kelly_multiplier=0.25,
    )
    sizing = requested.kelly_research[0]
    assert sizing.full_kelly_fraction == pytest.approx((0.60 - 0.40) / 0.60)
    assert sizing.research_fraction == pytest.approx(((0.60 - 0.40) / 0.60) * 0.25)
    assert sizing.can_execute is False


def test_no_side_kelly_uses_no_win_probability():
    evt = event(factor_ids=())
    pos = position("no", event_obj=evt, side=PositionSide.NO, cost=0.30, model_p_yes=0.40)
    result = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(pos,),
        scenarios=(scenario("s", 80, event_obj=evt),),
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        fractional_kelly_multiplier=0.5,
    )
    sizing = result.kelly_research[0]
    assert sizing.side_win_probability == pytest.approx(0.60)
    assert sizing.full_kelly_fraction == pytest.approx((0.60 - 0.30) / 0.70)


@pytest.mark.parametrize("value", [0, -0.1, 1.1, True])
def test_fractional_kelly_multiplier_is_explicit_and_bounded(value):
    evt = event(factor_ids=())
    with pytest.raises(PortfolioRiskError, match="FRACTIONAL_KELLY_MULTIPLIER_INVALID"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("p", event_obj=evt),),
            scenarios=(scenario("s", 90, event_obj=evt),),
            dependence_mode=DependenceMode.SAME_EVENT_EXACT,
            fractional_kelly_multiplier=value,
        )


def test_missing_model_probability_does_not_invent_kelly_sizing():
    evt = event(factor_ids=())
    pos = position("p", event_obj=evt, model_p_yes=None)
    result = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(pos,),
        scenarios=(scenario("s", 90, event_obj=evt),),
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        fractional_kelly_multiplier=0.25,
    )
    assert result.kelly_research[0].status == "MODEL_PROBABILITY_UNAVAILABLE"
    assert result.kelly_research[0].research_fraction is None


def test_scenario_must_cover_exact_portfolio_events():
    dfw = event()
    hou = event(location="CLIHOU", region_id="HOU")
    bad = WeatherScenario(
        scenario_id="bad",
        available_at="2026-10-04T16:02:00Z",
        weight=1,
        event_values={dfw.event_key: 90},
        factor_states={"SYNOPTIC_REGIME": "RIDGE"},
    )
    with pytest.raises(PortfolioRiskError, match="WEATHER_SCENARIO_EVENT_COVERAGE_MISMATCH"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("dfw", event_obj=dfw), position("hou", event_obj=hou)),
            scenarios=(bad,),
            dependence_mode=DependenceMode.REGIONAL_FACTOR_SCENARIOS,
        )


def test_regional_scenario_requires_all_declared_factor_states():
    dfw = event(factor_ids=("SYNOPTIC_REGIME", "CLOUD_FIELD"))
    hou = event(location="CLIHOU", region_id="HOU", factor_ids=("SYNOPTIC_REGIME", "CLOUD_FIELD"))
    bad = WeatherScenario(
        scenario_id="bad",
        available_at="2026-10-04T16:02:00Z",
        weight=1,
        event_values={dfw.event_key: 90, hou.event_key: 91},
        factor_states={"SYNOPTIC_REGIME": "RIDGE"},
    )
    with pytest.raises(PortfolioRiskError, match="REGIONAL_FACTOR_STATE_MISSING"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("dfw", event_obj=dfw), position("hou", event_obj=hou)),
            scenarios=(bad,),
            dependence_mode=DependenceMode.REGIONAL_FACTOR_SCENARIOS,
        )


def test_future_inputs_fail_point_in_time_validation():
    evt = event(factor_ids=())
    with pytest.raises(PortfolioRiskError, match="PORTFOLIO_POSITION_FUTURE_PREDICTION"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("p", event_obj=evt, prediction_time="2026-10-04T16:06:00Z"),),
            scenarios=(scenario("s", 90, event_obj=evt),),
            dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        )
    with pytest.raises(PortfolioRiskError, match="WEATHER_SCENARIO_FUTURE_EVIDENCE"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("p2", event_obj=evt),),
            scenarios=(scenario("future", 90, event_obj=evt, available_at="2026-10-04T16:06:00Z"),),
            dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        )


def test_market_and_portfolio_state_cannot_feed_weather_probability():
    evt = event(factor_ids=())
    base = dict(
        position_id="p",
        ticker="T",
        rule_snapshot_id="rule",
        prediction_id="prediction",
        market_snapshot_id="market",
        prediction_time="2026-10-04T16:00:00Z",
        market_time="2026-10-04T16:01:00Z",
        event=evt,
        side=PositionSide.YES,
        quantity=1,
        entry_cost_per_contract=0.5,
        threshold_lower=85,
    )
    with pytest.raises(PortfolioRiskError, match="MARKET_PRICE_WEATHER_INPUT_PROHIBITED"):
        PortfolioPosition(**base, market_price_used_as_weather_input=True)
    with pytest.raises(PortfolioRiskError, match="PORTFOLIO_STATE_WEATHER_INPUT_PROHIBITED"):
        PortfolioPosition(**base, risk_state_used_as_weather_input=True)


def test_snapshot_identity_is_deterministic_and_order_independent():
    evt = event(factor_ids=())
    p1 = position("a", event_obj=evt, lower=85)
    p2 = position("b", event_obj=evt, lower=86)
    s1 = scenario("a", 85, event_obj=evt, weight=1)
    s2 = scenario("b", 87, event_obj=evt, weight=2)
    first = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(p1, p2),
        scenarios=(s1, s2),
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
    )
    second = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(p2, p1),
        scenarios=(s2, s1),
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
    )
    assert first.risk_snapshot_id == second.risk_snapshot_id


def test_same_event_identity_collision_fails_closed():
    base = event(region_id="DFW", factor_ids=("SYNOPTIC_REGIME",))
    conflicting = WeatherEventDescriptor(
        event_key=base.event_key,
        lane=base.lane,
        settlement_source=base.settlement_source,
        settlement_location_code=base.settlement_location_code,
        observation_window=base.observation_window,
        metric=base.metric,
        units=base.units,
        region_id="WRONG_REGION",
        factor_ids=("OTHER_FACTOR",),
    )
    with pytest.raises(PortfolioRiskError, match="WEATHER_EVENT_IDENTITY_COLLISION"):
        PortfolioScenarioEngine().evaluate(
            as_of_time="2026-10-04T16:05:00Z",
            positions=(position("a", event_obj=base), position("b", event_obj=conflicting)),
            scenarios=(
                WeatherScenario(
                    scenario_id="s",
                    available_at="2026-10-04T16:02:00Z",
                    weight=1,
                    event_values={base.event_key: 90},
                    factor_states={},
                ),
            ),
            dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        )


def test_execution_flags_fail_closed():
    evt = event(factor_ids=())
    with pytest.raises(PortfolioRiskError, match="PORTFOLIO_POSITION_EXECUTION_PROHIBITED"):
        PortfolioPosition(
            position_id="p",
            ticker="T",
            rule_snapshot_id="rule",
            prediction_id="prediction",
            market_snapshot_id="market",
            prediction_time="2026-10-04T16:00:00Z",
            market_time="2026-10-04T16:01:00Z",
            event=evt,
            side=PositionSide.YES,
            quantity=1,
            entry_cost_per_contract=0.5,
            threshold_lower=85,
            can_execute=True,
        )
    with pytest.raises(PortfolioRiskError, match="WEATHER_SCENARIO_EXECUTION_PROHIBITED"):
        WeatherScenario(
            scenario_id="s",
            available_at="2026-10-04T16:02:00Z",
            weight=1,
            event_values={evt.event_key: 90},
            can_execute=True,
        )
