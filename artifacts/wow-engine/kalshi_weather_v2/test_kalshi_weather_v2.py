from math import isclose

from kalshi_weather_v2.agents import ContractSettlementAgent, MarketCalibrationAuditor, WeatherProbabilityAgent
from kalshi_weather_v2.models import ContractSnapshot, MarketSnapshot, ProbabilityPackage, WeatherEvidenceSnapshot
from kalshi_weather_v2.orchestrator import evaluate_weather_contract
from kalshi_weather_v2.probability_core import CalibrationProfile, WeatherProbabilityCore


def contract(**overrides):
    base = dict(
        market_title="NYC daily high",
        contract_title="Will NYC high be 90 to 91F?",
        ticker="KXHIGHNY-TEST",
        lane="DAILY_HIGH_TEMPERATURE",
        yes_condition="Official daily high is 90F or 91F",
        no_condition="Otherwise",
        location="New York City",
        metric="daily_high_temperature",
        units="F",
        observation_window="2026-09-06 local day",
        timezone="America/New_York",
        settlement_source="CONTRACT_NAMED_SOURCE",
        settlement_station_id="KNYC",
        settlement_station_name="Central Park",
        rounding_convention="nearest whole F",
        trace_measurement_rules="not_applicable",
        market_close_time="2026-09-07T00:00:00-04:00",
        rule_snapshot_id="rule-1",
        threshold_lower=90.0,
        threshold_upper=91.0,
    )
    base.update(overrides)
    return ContractSnapshot(**base)


def evidence(**overrides):
    base = dict(
        analysis_time="2026-09-06T18:00:00-04:00",
        latest_official_observation_time="2026-09-06T17:51:00-04:00",
        forecast_issue_time="2026-09-06T17:00:00-04:00",
        model_cycle_times=("2026-09-06T12:00:00Z",),
        providers=("NWS", "OPEN_METEO", "NOAA_NCEI"),
        source_roles={"NWS": "PRIMARY_FORECAST", "OPEN_METEO": "SECONDARY_FORECAST", "NOAA_NCEI": "HISTORICAL_CALIBRATION"},
        source_snapshot_ids=("nws-1", "om-1", "ncei-1"),
        observed_extreme_so_far=89.0,
        central_estimate=91.0,
        disagreement_magnitude=1.1,
        evidence_complete=True,
        station_identity_verified=True,
        settlement_source_verified=True,
        temporal_provenance_verified=True,
    )
    base.update(overrides)
    return WeatherEvidenceSnapshot(**base)


def calibration(**overrides):
    base = dict(
        station_id="KNYC",
        lane="DAILY_HIGH_TEMPERATURE",
        lead_time_bucket="SAME_DAY_AFTERNOON",
        bias_f=0.2,
        sigma_f=1.8,
        lower_sigma_f=1.5,
        upper_sigma_f=2.2,
        sample_n=250,
        method="STATION_HORIZON_EMPIRICAL_RESIDUAL_V1",
        certified=True,
    )
    base.update(overrides)
    return CalibrationProfile(**base)


def market(**overrides):
    base = dict(
        yes_price=0.35,
        no_price=0.67,
        price_time="2026-09-06T17:59:30-04:00",
        market_open=True,
        orderbook_nonempty=True,
        executable_price_verified=True,
        fee_known=False,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def test_probability_core_is_independent_and_coherent():
    p = WeatherProbabilityCore().build(contract=contract(), evidence=evidence(), calibration=calibration())
    assert p.market_price_used_as_input is False
    assert p.coherent is True
    assert p.calibrated is True
    assert 0 < p.p_yes < 1
    assert isclose(p.p_yes + p.p_no, 1.0, abs_tol=1e-9)
    assert p.lower_bound_yes <= p.p_yes <= p.upper_bound_yes


def test_intraday_observed_max_zeroes_impossible_bracket():
    p = WeatherProbabilityCore().build(
        contract=contract(threshold_lower=88.0, threshold_upper=90.0),
        evidence=evidence(observed_extreme_so_far=91.0, central_estimate=91.5),
        calibration=calibration(),
    )
    assert p.p_yes == 0.0
    assert p.p_no == 1.0


def test_intraday_conditioning_changes_remaining_probability():
    core = WeatherProbabilityCore()
    c = contract(threshold_lower=92.0, threshold_upper=None)
    pre = core.build(contract=c, evidence=evidence(observed_extreme_so_far=None), calibration=calibration())
    live = core.build(contract=c, evidence=evidence(observed_extreme_so_far=91.0), calibration=calibration())
    assert live.p_yes > pre.p_yes


def test_contract_agent_fails_closed_on_station_ambiguity():
    result = ContractSettlementAgent().evaluate(
        contract(settlement_station_id=None),
        evidence(station_identity_verified=False),
    )
    assert result.ok is False
    assert result.code == "NO_PLAY_SETTLEMENT_AMBIGUITY"
    assert "SETTLEMENT_STATION_UNRESOLVED" in result.blockers


def test_probability_agent_rejects_market_price_substitution():
    package = ProbabilityPackage(
        p_yes=0.6, p_no=0.4, central_estimate=91.2,
        lower_bound_yes=0.55, upper_bound_yes=0.65,
        threshold_distance="NEAR_THRESHOLD",
        calibration_method="TEST",
        probability_source="BAD_MARKET_COPY",
        market_price_used_as_input=True,
        coherent=True,
        calibrated=True,
    )
    result = WeatherProbabilityAgent().evaluate(evidence(), package)
    assert result.ok is False
    assert "MARKET_PRICE_SUBSTITUTION_PROHIBITED" in result.blockers


def test_market_auditor_holds_stale_unverified_market_without_erasing_probability():
    package = WeatherProbabilityCore().build(contract=contract(), evidence=evidence(), calibration=calibration())
    result = MarketCalibrationAuditor().evaluate(
        package,
        market(orderbook_nonempty=False, executable_price_verified=False),
    )
    assert result.ok is False
    assert "ORDERBOOK_EMPTY" in result.blockers


def test_governor_settlement_failure_has_highest_precedence():
    package = WeatherProbabilityCore().build(contract=contract(), evidence=evidence(), calibration=calibration())
    decision = evaluate_weather_contract(
        contract=contract(settlement_station_id=None),
        evidence=evidence(station_identity_verified=False),
        probability=package,
        market=market(),
    )
    assert decision.status == "NO_PLAY_SETTLEMENT_AMBIGUITY"
    assert decision.probability_publishable is False
    assert decision.edge_publishable is False
    assert decision.can_execute is False


def test_governor_publishes_probability_but_not_edge_when_market_held():
    package = WeatherProbabilityCore().build(contract=contract(), evidence=evidence(), calibration=calibration())
    decision = evaluate_weather_contract(
        contract=contract(), evidence=evidence(), probability=package,
        market=market(orderbook_nonempty=False, executable_price_verified=False),
    )
    assert decision.status == "WATCH"
    assert decision.probability_publishable is True
    assert decision.edge_publishable is False


def test_governor_qualifies_only_positive_uncertainty_adjusted_edge():
    package = ProbabilityPackage(
        p_yes=0.70, p_no=0.30, central_estimate=92.0,
        lower_bound_yes=0.64, upper_bound_yes=0.75,
        threshold_distance="MODERATELY_INSIDE_OR_OUTSIDE",
        calibration_method="TEST",
        probability_source="KALSHI_WEATHER_V2_STATION_HORIZON_MODEL",
        coherent=True, calibrated=True,
    )
    decision = evaluate_weather_contract(
        contract=contract(), evidence=evidence(), probability=package,
        market=market(yes_price=0.50, no_price=0.55),
    )
    assert decision.status == "QUALIFIED_EDGE"
    assert decision.rank_eligible is True
    assert decision.payload["best_side"] == "YES"
    assert decision.payload["best_uncertainty_adjusted_edge"] > 0


def test_governor_no_edge_when_conservative_price_exceeds_model_bound():
    package = ProbabilityPackage(
        p_yes=0.55, p_no=0.45, central_estimate=91.0,
        lower_bound_yes=0.50, upper_bound_yes=0.60,
        threshold_distance="NEAR_THRESHOLD",
        calibration_method="TEST",
        probability_source="KALSHI_WEATHER_V2_STATION_HORIZON_MODEL",
        coherent=True, calibrated=True,
    )
    decision = evaluate_weather_contract(
        contract=contract(), evidence=evidence(), probability=package,
        market=market(yes_price=0.56, no_price=0.46),
    )
    assert decision.status == "NO_EDGE"
    assert decision.rank_eligible is False
