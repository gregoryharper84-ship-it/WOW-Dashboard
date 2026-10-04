import pytest

from kalshi_weather_v2.decision_policy import DecisionAction, DecisionPolicy
from kalshi_weather_v2.information_events import (
    InformationEvent,
    InformationEventError,
    InformationEventRegistry,
    InformationEventType,
)
from kalshi_weather_v2.voi_engine import (
    DecisionGateState,
    PosteriorShiftDistribution,
    PosteriorShiftScenario,
    VoIEngineError,
    VoIEngineV2,
)


def event(event_id="metar-1", **overrides):
    values = dict(
        event_id=event_id,
        version="v1",
        event_type=InformationEventType.ASOS_METAR_OBSERVATION,
        lane="HOURLY_TEMPERATURE",
        source="KDFW ASOS",
        evidence_id="shadow-replay-metars-v1",
        expected_at="2026-10-04T16:00:00Z",
        lead_time_bucket="H1",
        regime="CLEAR",
        threshold_distance="NEAR_THRESHOLD",
    )
    values.update(overrides)
    return InformationEvent(**values)


def distribution(**overrides):
    values = dict(
        event_type=InformationEventType.ASOS_METAR_OBSERVATION,
        lane="HOURLY_TEMPERATURE",
        evidence_id="posterior-response-replay-v1",
        method="EMPIRICAL_EVENT_RESPONSE_V1",
        lead_time_bucket="H1",
        regime="CLEAR",
        threshold_distance="NEAR_THRESHOLD",
        scenarios=(
            PosteriorShiftScenario(delta_probability=0.08, weight=0.5),
            PosteriorShiftScenario(delta_probability=-0.04, weight=0.5),
        ),
    )
    values.update(overrides)
    return PosteriorShiftDistribution(**values)


def gates(**overrides):
    values = dict(settlement_ready=True, calibration_ready=True, execution_quality_ready=True)
    values.update(overrides)
    return DecisionGateState(**values)


def test_information_event_registry_is_versioned_and_deterministic():
    registry = InformationEventRegistry()
    later = event("z-event", expected_at="2026-10-04T17:00:00Z")
    earlier = event("a-event", expected_at="2026-10-04T16:00:00Z")
    registry.register_many((later, earlier))
    assert [item.event_id for item in registry.future_as_of("2026-10-04T15:00:00Z")] == ["a-event", "z-event"]
    assert registry.register(earlier) == earlier
    with pytest.raises(InformationEventError, match="INFORMATION_EVENT_VERSION_COLLISION"):
        registry.register(event("a-event", source="OTHER SOURCE"))


def test_point_in_time_registry_excludes_future_available_evidence():
    registry = InformationEventRegistry()
    realized = event("realized", expected_at="2026-10-04T14:00:00Z", available_at="2026-10-04T14:02:00Z")
    future_available = event("future-available", expected_at="2026-10-04T14:00:00Z", available_at="2026-10-04T15:02:00Z")
    upcoming = event("upcoming", expected_at="2026-10-04T16:00:00Z")
    registry.register_many((realized, future_available, upcoming))
    assert [item.event_id for item in registry.available_as_of("2026-10-04T15:00:00Z")] == ["realized"]
    assert [item.event_id for item in registry.future_as_of("2026-10-04T15:00:00Z")] == ["future-available", "upcoming"]


def test_information_event_rejects_execution_capability():
    with pytest.raises(InformationEventError, match="INFORMATION_EVENT_EXECUTION_PROHIBITED"):
        event(can_execute=True)


def test_posterior_distribution_requires_governed_evidence_and_no_market_feedback():
    with pytest.raises(VoIEngineError, match="POSTERIOR_SHIFT_EVIDENCE_MISSING"):
        distribution(evidence_id="")
    with pytest.raises(VoIEngineError, match="MARKET_PRICE_WEATHER_INPUT_PROHIBITED"):
        distribution(market_price_used_as_weather_input=True)


def test_sequential_voi_can_prefer_wait():
    first = event("metar-1", expected_at="2026-10-04T16:00:00Z")
    second = event(
        "hrrr-1",
        expected_at="2026-10-04T17:00:00Z",
        event_type=InformationEventType.HRRR_MODEL_CYCLE,
        source="HRRR",
        evidence_id="shadow-hrrr-v1",
        lead_time_bucket="H2",
    )
    d1 = distribution()
    d2 = distribution(
        event_type=InformationEventType.HRRR_MODEL_CYCLE,
        lead_time_bucket="H2",
        scenarios=(
            PosteriorShiftScenario(delta_probability=0.06, weight=0.5),
            PosteriorShiftScenario(delta_probability=-0.02, weight=0.5),
        ),
    )
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.52,
        raw_market_probability=0.50,
        effective_break_even_probability=0.53,
        events=(first, second),
        distributions={"metar-1": d1, "hrrr-1": d2},
        gates=gates(),
    )
    assert result.action is DecisionAction.WAIT
    assert result.best_wait_event_id == "metar-1"
    assert len(result.edge_survival) == 2
    assert result.wait_value > result.trade_now_value
    assert result.can_execute is False


def test_wait_cost_can_make_trade_now_optimal():
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.58,
        raw_market_probability=0.50,
        effective_break_even_probability=0.54,
        events=(event(),),
        distributions={"metar-1": distribution()},
        gates=gates(),
        wait_costs={"metar-1": 0.08},
    )
    assert result.action is DecisionAction.TRADE_NOW
    assert result.current_effective_edge == pytest.approx(0.04)


def test_governance_gates_block_trade_and_wait():
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.60,
        raw_market_probability=0.50,
        effective_break_even_probability=0.55,
        events=(event(),),
        distributions={"metar-1": distribution()},
        gates=gates(calibration_ready=False),
    )
    assert result.action is DecisionAction.ABSTAIN
    assert "CALIBRATION_GATE_INCOMPLETE" in result.blockers


def test_market_state_changes_decision_edge_not_weather_probability():
    engine = VoIEngineV2()
    kwargs = dict(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.60,
        events=(event(),),
        distributions={"metar-1": distribution()},
        gates=gates(),
    )
    cheap = engine.evaluate(raw_market_probability=0.45, effective_break_even_probability=0.47, **kwargs)
    expensive = engine.evaluate(raw_market_probability=0.62, effective_break_even_probability=0.64, **kwargs)
    assert cheap.current_probability == expensive.current_probability == 0.60
    assert cheap.edge_survival[0].expected_probability == pytest.approx(expensive.edge_survival[0].expected_probability)
    assert cheap.current_effective_edge != expensive.current_effective_edge


def test_edge_survival_reports_effective_survival_and_sign_reversal():
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.56,
        raw_market_probability=0.50,
        effective_break_even_probability=0.55,
        events=(event(),),
        distributions={"metar-1": distribution(scenarios=(
            PosteriorShiftScenario(delta_probability=0.08, weight=0.5),
            PosteriorShiftScenario(delta_probability=-0.08, weight=0.5),
        ))},
        gates=gates(),
    )
    metric = result.edge_survival[0]
    assert metric.probability_effective_edge_positive == pytest.approx(0.5)
    assert metric.probability_sign_reversal == pytest.approx(0.5)


def test_posterior_shift_out_of_bounds_fails_instead_of_clipping():
    with pytest.raises(VoIEngineError, match="POSTERIOR_SHIFT_OUT_OF_BOUNDS"):
        VoIEngineV2().evaluate(
            decision_time="2026-10-04T15:00:00Z",
            current_probability=0.98,
            raw_market_probability=0.50,
            effective_break_even_probability=0.55,
            events=(event(),),
            distributions={"metar-1": distribution(scenarios=(
                PosteriorShiftScenario(delta_probability=0.05, weight=1.0),
            ))},
            gates=gates(),
        )


def test_no_implicit_minimum_edge_threshold():
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.501,
        raw_market_probability=0.499,
        effective_break_even_probability=0.500,
        events=(),
        distributions={},
        gates=gates(),
    )
    assert result.action is DecisionAction.TRADE_NOW
    assert result.current_effective_edge == pytest.approx(0.001)


def test_explicit_policy_threshold_can_abstain_without_becoming_default():
    policy = DecisionPolicy(
        policy_id="KALSHI_WEATHER_DECISION_POLICY",
        version="shadow-v2",
        evidence_id="chronological-replay-1",
        effective_from="2026-10-04T00:00:00Z",
        minimum_edge=0.02,
    )
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.51,
        raw_market_probability=0.49,
        effective_break_even_probability=0.50,
        events=(),
        distributions={},
        gates=gates(),
        policy=policy,
    )
    assert result.action is DecisionAction.ABSTAIN
    assert result.policy_version == "shadow-v2"


def test_wait_to_trade_now_fallback_reapplies_minimum_edge_policy():
    policy = DecisionPolicy(
        policy_id="KALSHI_WEATHER_DECISION_POLICY",
        version="fallback-regression",
        evidence_id="chronological-replay-fallback",
        effective_from="2026-10-04T00:00:00Z",
        minimum_edge=0.02,
        minimum_voi_gain=0.01,
    )
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.501,
        raw_market_probability=0.49,
        effective_break_even_probability=0.50,
        events=(event(),),
        distributions={"metar-1": distribution(scenarios=(
            PosteriorShiftScenario(delta_probability=0.01, weight=0.5),
            PosteriorShiftScenario(delta_probability=-0.005, weight=0.5),
        ))},
        gates=gates(),
        policy=policy,
    )
    assert result.voi_gain < policy.minimum_voi_gain
    assert result.action is DecisionAction.ABSTAIN


def test_survival_policy_uses_worst_modeled_horizon_not_only_first_event():
    first = event("metar-1", expected_at="2026-10-04T16:00:00Z")
    second = event(
        "hrrr-1",
        expected_at="2026-10-04T17:00:00Z",
        event_type=InformationEventType.HRRR_MODEL_CYCLE,
        source="HRRR",
        evidence_id="shadow-hrrr-v1",
        lead_time_bucket="H2",
    )
    policy = DecisionPolicy(
        policy_id="KALSHI_WEATHER_DECISION_POLICY",
        version="survival-all-horizons",
        evidence_id="chronological-replay-survival",
        effective_from="2026-10-04T00:00:00Z",
        minimum_edge_survival_probability=0.75,
    )
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.60,
        raw_market_probability=0.50,
        effective_break_even_probability=0.55,
        events=(first, second),
        distributions={
            "metar-1": distribution(scenarios=(PosteriorShiftScenario(delta_probability=0.05, weight=1.0),)),
            "hrrr-1": distribution(
                event_type=InformationEventType.HRRR_MODEL_CYCLE,
                lead_time_bucket="H2",
                scenarios=(
                    PosteriorShiftScenario(delta_probability=0.02, weight=0.5),
                    PosteriorShiftScenario(delta_probability=-0.20, weight=0.5),
                ),
            ),
        },
        gates=gates(),
        policy=policy,
    )
    assert result.edge_survival[0].probability_effective_edge_positive == pytest.approx(1.0)
    assert result.edge_survival[1].probability_effective_edge_positive == pytest.approx(0.5)
    assert result.action is DecisionAction.ABSTAIN


def test_engine_rejects_event_not_future_as_of_decision_time():
    with pytest.raises(VoIEngineError, match="INFORMATION_EVENT_NOT_FUTURE_AS_OF_DECISION"):
        VoIEngineV2().evaluate(
            decision_time="2026-10-04T16:30:00Z",
            current_probability=0.60,
            raw_market_probability=0.50,
            effective_break_even_probability=0.55,
            events=(event(expected_at="2026-10-04T16:00:00Z"),),
            distributions={"metar-1": distribution()},
            gates=gates(),
        )


def test_fractional_second_event_order_is_chronological_not_lexicographic():
    registry = InformationEventRegistry()
    half = event("half", expected_at="2026-10-04T16:00:00.500000Z")
    whole = event("whole", expected_at="2026-10-04T16:00:00Z")
    registry.register_many((half, whole))
    assert [item.event_id for item in registry.future_as_of("2026-10-04T15:00:00Z")] == ["whole", "half"]


@pytest.mark.parametrize(
    "gate_overrides, blocker",
    [
        ({"settlement_ready": False}, "SETTLEMENT_GATE_INCOMPLETE"),
        ({"execution_quality_ready": False}, "EXECUTION_QUALITY_GATE_INCOMPLETE"),
    ],
)
def test_upstream_gate_blockers_return_abstain_before_bad_voi_inputs(gate_overrides, blocker):
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.60,
        raw_market_probability=0.50,
        effective_break_even_probability=0.55,
        events=(event(),),
        distributions={},
        gates=gates(**gate_overrides),
    )
    assert result.action is DecisionAction.ABSTAIN
    assert blocker in result.blockers
    assert result.edge_survival == ()


def test_scenario_tree_max_paths_fails_closed():
    second = event(
        "hrrr-1",
        expected_at="2026-10-04T17:00:00Z",
        event_type=InformationEventType.HRRR_MODEL_CYCLE,
        source="HRRR",
        evidence_id="shadow-hrrr-v1",
        lead_time_bucket="H2",
    )
    with pytest.raises(VoIEngineError, match="VOI_SCENARIO_TREE_TOO_LARGE"):
        VoIEngineV2().evaluate(
            decision_time="2026-10-04T15:00:00Z",
            current_probability=0.60,
            raw_market_probability=0.50,
            effective_break_even_probability=0.55,
            events=(event(), second),
            distributions={
                "metar-1": distribution(),
                "hrrr-1": distribution(event_type=InformationEventType.HRRR_MODEL_CYCLE, lead_time_bucket="H2"),
            },
            gates=gates(),
            max_paths=3,
        )


def test_wait_cost_on_later_event_reduces_root_wait_value():
    second = event(
        "hrrr-1",
        expected_at="2026-10-04T17:00:00Z",
        event_type=InformationEventType.HRRR_MODEL_CYCLE,
        source="HRRR",
        evidence_id="shadow-hrrr-v1",
        lead_time_bucket="H2",
    )
    kwargs = dict(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.52,
        raw_market_probability=0.50,
        effective_break_even_probability=0.53,
        events=(event(), second),
        distributions={
            "metar-1": distribution(),
            "hrrr-1": distribution(event_type=InformationEventType.HRRR_MODEL_CYCLE, lead_time_bucket="H2"),
        },
        gates=gates(),
    )
    no_cost = VoIEngineV2().evaluate(**kwargs)
    later_cost = VoIEngineV2().evaluate(**kwargs, wait_costs={"hrrr-1": 0.02})
    assert later_cost.wait_value < no_cost.wait_value


def test_multi_event_sign_reversal_accumulates_across_horizons():
    second = event(
        "hrrr-1",
        expected_at="2026-10-04T17:00:00Z",
        event_type=InformationEventType.HRRR_MODEL_CYCLE,
        source="HRRR",
        evidence_id="shadow-hrrr-v1",
        lead_time_bucket="H2",
    )
    result = VoIEngineV2().evaluate(
        decision_time="2026-10-04T15:00:00Z",
        current_probability=0.60,
        raw_market_probability=0.50,
        effective_break_even_probability=0.55,
        events=(event(), second),
        distributions={
            "metar-1": distribution(scenarios=(PosteriorShiftScenario(delta_probability=0.02, weight=1.0),)),
            "hrrr-1": distribution(
                event_type=InformationEventType.HRRR_MODEL_CYCLE,
                lead_time_bucket="H2",
                scenarios=(
                    PosteriorShiftScenario(delta_probability=0.02, weight=0.5),
                    PosteriorShiftScenario(delta_probability=-0.12, weight=0.5),
                ),
            ),
        },
        gates=gates(),
    )
    assert result.edge_survival[0].probability_sign_reversal == pytest.approx(0.0)
    assert result.edge_survival[1].probability_sign_reversal == pytest.approx(0.5)
