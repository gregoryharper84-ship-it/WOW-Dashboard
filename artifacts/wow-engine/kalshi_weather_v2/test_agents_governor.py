from kalshi_weather_v2 import (
    ContractSettlementAgent,
    ContractSnapshot,
    KalshiWeatherTerminalGovernor,
    MarketCalibrationAuditor,
    MarketSnapshot,
    ProbabilityPackage,
    WeatherEvidenceSnapshot,
    WeatherProbabilityAgent,
)


def contract(**overrides):
    values = dict(
        market_title="NYC daily high",
        contract_title="Will the high be 90F or above?",
        ticker="KXHIGHNY-TEST",
        lane="DAILY_HIGH_TEMPERATURE",
        yes_condition="Official settlement high >= 90F",
        no_condition="Official settlement high < 90F",
        location="New York City",
        metric="daily maximum temperature",
        units="F",
        observation_window="2026-09-03 local day",
        timezone="America/New_York",
        settlement_source="contract-named official source",
        settlement_station_id="KNYC",
        settlement_station_name="Central Park",
        rounding_convention="whole degree per contract",
        trace_measurement_rules="not applicable",
        market_close_time="2026-09-03T23:00:00-04:00",
        rule_snapshot_id="rule-1",
    )
    values.update(overrides)
    return ContractSnapshot(**values)


def evidence(**overrides):
    values = dict(
        analysis_time="2026-09-03T12:00:00-04:00",
        latest_official_observation_time="2026-09-03T11:51:00-04:00",
        forecast_issue_time="2026-09-03T10:00:00-04:00",
        model_cycle_times=("2026-09-03T12:00:00Z",),
        providers=("NWS", "Open-Meteo", "NOAA/NCEI"),
        source_roles={"NWS": "OFFICIAL_OBSERVATION", "Open-Meteo": "NUMERICAL_GUIDANCE"},
        source_snapshot_ids=("obs-1", "forecast-1", "model-1"),
        observed_extreme_so_far=86.0,
        central_estimate=89.4,
        disagreement_magnitude=1.1,
        evidence_complete=True,
        station_identity_verified=True,
        settlement_source_verified=True,
        temporal_provenance_verified=True,
    )
    values.update(overrides)
    return WeatherEvidenceSnapshot(**values)


def probability(**overrides):
    values = dict(
        p_yes=0.62,
        p_no=0.38,
        central_estimate=89.4,
        lower_bound_yes=0.57,
        upper_bound_yes=0.67,
        threshold_distance="NEAR_THRESHOLD",
        calibration_method="STATION_HORIZON_CALIBRATION_V1",
        probability_source="KALSHI_WEATHER_MODEL_V2",
        market_price_used_as_input=False,
        coherent=True,
        calibrated=True,
    )
    values.update(overrides)
    return ProbabilityPackage(**values)


def market(**overrides):
    values = dict(
        yes_price=0.50,
        no_price=0.52,
        price_time="2026-09-03T12:00:10-04:00",
        market_open=True,
        orderbook_nonempty=True,
        executable_price_verified=True,
        fee_known=False,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def run_all(c=None, e=None, p=None, m=None):
    c = c or contract()
    e = e or evidence()
    p = p or probability()
    m = m or market()
    settlement_result = ContractSettlementAgent().evaluate(c, e)
    probability_result = WeatherProbabilityAgent().evaluate(e, p)
    market_result = MarketCalibrationAuditor().evaluate(p, m)
    decision = KalshiWeatherTerminalGovernor().reduce(
        settlement=settlement_result,
        probability=probability_result,
        market=market_result,
    )
    return settlement_result, probability_result, market_result, decision


def test_happy_path_produces_qualified_edge_without_inventing_strong_threshold():
    settlement_result, probability_result, market_result, decision = run_all()
    assert settlement_result.ok is True
    assert probability_result.ok is True
    assert market_result.ok is True
    assert decision.status == "QUALIFIED_EDGE"
    assert decision.rank_eligible is True
    assert decision.probability_publishable is True
    assert decision.edge_publishable is True
    assert decision.payload["best_side"] == "YES"
    assert round(decision.payload["best_uncertainty_adjusted_edge"], 6) == 0.07
    assert decision.can_execute is False


def test_unresolved_station_is_settlement_ambiguity_and_blocks_probability_publication():
    _, _, _, decision = run_all(c=contract(settlement_station_id=None, settlement_station_name=None))
    assert decision.status == "NO_PLAY_SETTLEMENT_AMBIGUITY"
    assert decision.probability_publishable is False
    assert decision.edge_publishable is False
    assert "SETTLEMENT_STATION_UNRESOLVED" in decision.blockers


def test_market_price_substitution_is_data_insufficient_not_market_hold():
    _, probability_result, _, decision = run_all(p=probability(market_price_used_as_input=True))
    assert probability_result.ok is False
    assert "MARKET_PRICE_SUBSTITUTION_PROHIBITED" in probability_result.blockers
    assert decision.status == "NO_PLAY_DATA_INSUFFICIENT"
    assert decision.probability_publishable is False


def test_stale_or_unverified_market_holds_edge_but_preserves_weather_probability():
    _, _, market_result, decision = run_all(m=market(executable_price_verified=False))
    assert market_result.ok is False
    assert decision.status == "WATCH"
    assert decision.probability_publishable is True
    assert decision.edge_publishable is False
    assert decision.rank_eligible is False


def test_no_positive_conservative_edge_is_no_edge():
    _, _, _, decision = run_all(m=market(yes_price=0.60, no_price=0.45))
    assert decision.status == "NO_EDGE"
    assert decision.rank_eligible is False
    assert decision.edge_publishable is True


def test_probability_incoherence_fails_closed():
    _, probability_result, _, decision = run_all(p=probability(p_yes=0.62, p_no=0.40))
    assert "YES_NO_PROBABILITY_INCOHERENT" in probability_result.blockers
    assert decision.status == "NO_PLAY_DATA_INSUFFICIENT"


def test_calibration_missing_fails_closed():
    _, probability_result, _, decision = run_all(p=probability(calibrated=False, calibration_method=None))
    assert "CALIBRATION_NOT_READY" in probability_result.blockers
    assert decision.status == "NO_PLAY_DATA_INSUFFICIENT"


def test_model_disagreement_is_warning_not_automatic_blocker():
    _, probability_result, _, decision = run_all(e=evidence(disagreement_magnitude=2.5))
    assert probability_result.ok is True
    assert "MODEL_DISAGREEMENT_PRESENT" in probability_result.warnings
    assert decision.status == "QUALIFIED_EDGE"


def test_xweather_is_not_required_for_probability_agent():
    e = evidence(providers=("NWS", "Open-Meteo", "NOAA/NCEI"))
    _, probability_result, _, decision = run_all(e=e)
    assert probability_result.ok is True
    assert decision.status == "QUALIFIED_EDGE"
