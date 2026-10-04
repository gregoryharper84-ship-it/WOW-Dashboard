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
    assert [item.event_id for item in registry.future_as_of("2026-10-04T15:00:00Z")] == ["upcoming"]


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
