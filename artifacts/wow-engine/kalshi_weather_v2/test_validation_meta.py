from dataclasses import replace

import pytest

from kalshi_weather_v2.decision_policy import DecisionAction
from kalshi_weather_v2.validation_meta import (
    BaselineKind,
    BaselinePrediction,
    ComplexityGatePolicy,
    ComplexityRecommendation,
    CounterfactualDecisionSample,
    SettlementRedTeamCategory,
    SettlementRedTeamFixture,
    SettlementRedTeamObservation,
    SettlementRedTeamRegistry,
    ValidationMetaError,
    ValidationMetaLayer,
    ProbabilityValidationSample,
)


def baseline(kind=BaselineKind.NBM, *, ident="base", version="v1", p=0.60, available="2026-10-04T15:00:00Z"):
    return BaselinePrediction(
        baseline_id=ident,
        kind=kind,
        version=version,
        p_yes=p,
        available_at=available,
        evidence_ids=(f"evidence-{ident}",),
        market_observational_only=(kind is BaselineKind.MARKET),
    )


def sample(sample_id, *, champion=0.70, outcome=True, confidence=0.90, baselines=None):
    resolved_baselines = (baseline(),) if baselines is None else tuple(baselines)
    return ProbabilityValidationSample(
        sample_id=sample_id,
        prediction_id=f"prediction-{sample_id}",
        decision_time="2026-10-04T16:00:00Z",
        settled_at="2026-10-04T18:00:00Z",
        champion_p_yes=champion,
        yes_outcome=outcome,
        confidence_score=confidence,
        champion_evidence_ids=(f"champion-evidence-{sample_id}",),
        baselines=resolved_baselines,
        lane="HOURLY_TEMPERATURE",
    )


def counterfactual(sample_id, action=DecisionAction.TRADE_NOW, *, policy_version="v1", trade=0.10, wait=0.05, abstain=0.0):
    return CounterfactualDecisionSample(
        sample_id=sample_id,
        policy_id="DECISION_POLICY",
        policy_version=policy_version,
        decision_time="2026-10-04T16:00:00Z",
        realized_at="2026-10-04T18:00:00Z",
        recommended_action=action,
        action_utilities={
            "TRADE_NOW": trade,
            "WAIT": wait,
            "ABSTAIN": abstain,
        },
        evidence_ids=(f"decision-evidence-{sample_id}",),
    )


def fixture(fid="source-conflict", code="NO_PLAY_SETTLEMENT_AMBIGUITY"):
    return SettlementRedTeamFixture(
        fixture_id=fid,
        version="v1",
        category=SettlementRedTeamCategory.SETTLEMENT_SOURCE_CONFLICT,
        expected_terminal_code=code,
        evidence_ids=(f"fixture-{fid}",),
        description="Conflicting controlling settlement sources must fail closed.",
    )


def observation(item, code=None):
    return SettlementRedTeamObservation(
        fixture_identity=item.identity,
        observed_terminal_code=code or item.expected_terminal_code,
        observed_at="2026-10-04T18:30:00Z",
        evidence_ids=(f"observed-{item.fixture_id}",),
    )


def gate(**overrides):
    values = dict(
        policy_id="VALIDATION_COMPLEXITY_GATE",
        version="research-v1",
        evidence_id="holdout-policy-evidence",
        minimum_holdout_n=2,
        minimum_brier_advantage=0.0,
        minimum_log_loss_advantage=0.0,
        minimum_red_team_pass_rate=1.0,
        minimum_selected_coverage=0.5,
        maximum_selected_brier=0.25,
    )
    values.update(overrides)
    return ComplexityGatePolicy(**values)


def test_market_baseline_is_comparison_only_and_cannot_feed_weather():
    item = baseline(BaselineKind.MARKET, ident="market", p=0.55)
    assert item.market_observational_only is True
    assert item.market_price_used_as_weather_input is False

    with pytest.raises(ValidationMetaError, match="MARKET_BASELINE_MUST_BE_OBSERVATIONAL_ONLY"):
        BaselinePrediction(
            baseline_id="bad-market",
            kind=BaselineKind.MARKET,
            version="v1",
            p_yes=0.55,
            available_at="2026-10-04T15:00:00Z",
            evidence_ids=("market-evidence",),
            market_observational_only=False,
        )
    with pytest.raises(ValidationMetaError, match="MARKET_PRICE_WEATHER_INPUT_PROHIBITED"):
        replace(item, market_price_used_as_weather_input=True)


def test_future_baseline_evidence_fails_point_in_time_validation():
    with pytest.raises(ValidationMetaError, match="BASELINE_FUTURE_EVIDENCE"):
        sample("future", baselines=(baseline(available="2026-10-04T16:01:00Z"),))


def test_champion_vs_baseline_scoring_uses_paired_outcomes():
    samples = (
        sample("a", champion=0.80, outcome=True, baselines=(baseline(p=0.60),)),
        sample("b", champion=0.20, outcome=False, baselines=(baseline(p=0.40),)),
    )
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=samples,
        selective_confidence_thresholds=(0.0, 0.8),
        counterfactual_samples=(counterfactual("d1"),),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(),
    )
    champion = next(item for item in report.model_scores if item.model_id == "CHAMPION")
    delta = report.baseline_deltas[0]
    assert champion.brier_score == pytest.approx(0.04)
    assert delta.champion_brier_advantage > 0
    assert delta.champion_log_loss_advantage > 0
    assert report.complexity_gate.recommendation is ComplexityRecommendation.ELIGIBLE_FOR_GOVERNED_REVIEW
    assert report.input_manifest["probability_samples"][0]["market_price_used_as_weather_input"] is False
    assert report.can_execute is False


def test_multiple_baseline_kinds_remain_separate_in_report():
    nbm = baseline(BaselineKind.NBM, ident="nbm", p=0.55)
    nws = baseline(BaselineKind.NWS, ident="nws", p=0.60)
    clim = baseline(BaselineKind.CLIMATOLOGY, ident="clim", p=0.50)
    market = baseline(BaselineKind.MARKET, ident="market", p=0.52)
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(
            sample("a", baselines=(nbm, nws, clim, market)),
            sample("b", champion=0.30, outcome=False, baselines=(nbm, nws, clim, market)),
        ),
        selective_confidence_thresholds=(0.0,),
        counterfactual_samples=(),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(),
    )
    kinds = {item.kind for item in report.model_scores}
    assert {"CHAMPION", "NBM", "NWS", "CLIMATOLOGY", "MARKET"} <= kinds


def test_selective_curve_reports_coverage_and_empty_tail():
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(
            sample("high", champion=0.80, outcome=True, confidence=0.95),
            sample("low", champion=0.55, outcome=False, confidence=0.40),
        ),
        selective_confidence_thresholds=(0.0, 0.8, 1.0),
        counterfactual_samples=(),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(maximum_selected_brier=0.10),
    )
    curve = {item.minimum_confidence: item for item in report.selective_curve}
    assert curve[0.0].coverage == pytest.approx(1.0)
    assert curve[0.8].coverage == pytest.approx(0.5)
    assert curve[0.8].brier_score == pytest.approx(0.04)
    assert curve[1.0].selected_n == 0
    assert curve[1.0].brier_score is None


def test_counterfactual_policy_grading_reports_regret_and_abstention():
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(sample("a"), sample("b", champion=0.30, outcome=False)),
        selective_confidence_thresholds=(0.0,),
        counterfactual_samples=(
            counterfactual("d1", DecisionAction.TRADE_NOW, trade=0.10, wait=0.05),
            counterfactual("d2", DecisionAction.ABSTAIN, trade=-0.10, wait=-0.05, abstain=0.0),
        ),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(),
    )
    score = report.counterfactual_scores[0]
    assert score.sample_n == 2
    assert score.abstention_rate == pytest.approx(0.5)
    assert score.regret_mean == pytest.approx(0.0)
    assert score.recommended_utility_mean == pytest.approx(0.05)


def test_counterfactual_requires_all_actions_and_is_non_executable():
    with pytest.raises(ValidationMetaError, match="COUNTERFACTUAL_UTILITY_MISSING:WAIT"):
        CounterfactualDecisionSample(
            sample_id="bad",
            policy_id="P",
            policy_version="v1",
            decision_time="2026-10-04T16:00:00Z",
            realized_at="2026-10-04T18:00:00Z",
            recommended_action=DecisionAction.TRADE_NOW,
            action_utilities={"TRADE_NOW": 0.1, "ABSTAIN": 0.0},
            evidence_ids=("e",),
        )
    with pytest.raises(ValidationMetaError, match="COUNTERFACTUAL_EXECUTION_PROHIBITED"):
        replace(counterfactual("x"), can_execute=True)


def test_red_team_registry_is_versioned_and_collision_safe():
    registry = SettlementRedTeamRegistry()
    first = registry.register(fixture())
    assert registry.register(first) == first
    with pytest.raises(ValidationMetaError, match="RED_TEAM_FIXTURE_VERSION_COLLISION"):
        registry.register(replace(first, expected_terminal_code="OTHER"))
    assert registry.snapshot() == (first,)


def test_red_team_missing_and_wrong_observations_fail_gate():
    fx1 = fixture("one")
    fx2 = fixture("two")
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(sample("a"), sample("b", champion=0.30, outcome=False)),
        selective_confidence_thresholds=(0.0,),
        counterfactual_samples=(),
        red_team_fixtures=(fx1, fx2),
        red_team_observations=(observation(fx1, code="WRONG"),),
        complexity_policy=gate(minimum_red_team_pass_rate=1.0),
    )
    statuses = {item.fixture_identity: item.status for item in report.red_team_results}
    assert statuses[fx1.identity] == "FAIL"
    assert statuses[fx2.identity] == "NOT_OBSERVED"
    assert "RED_TEAM_PASS_RATE_BELOW_POLICY_MINIMUM" in report.complexity_gate.blockers
    assert report.complexity_gate.recommendation is ComplexityRecommendation.HOLD


def test_unknown_red_team_observation_fails_closed():
    fx = fixture()
    unknown = SettlementRedTeamObservation(
        fixture_identity="unknown@v1",
        observed_terminal_code="X",
        observed_at="2026-10-04T18:30:00Z",
        evidence_ids=("e",),
    )
    with pytest.raises(ValidationMetaError, match="RED_TEAM_OBSERVATION_FIXTURE_UNKNOWN"):
        ValidationMetaLayer().evaluate(
            as_of_time="2026-10-04T19:00:00Z",
            probability_samples=(sample("a"), sample("b", champion=0.30, outcome=False)),
            selective_confidence_thresholds=(0.0,),
            counterfactual_samples=(),
            red_team_fixtures=(fx,),
            red_team_observations=(unknown,),
            complexity_policy=gate(),
        )


def test_complexity_gate_has_no_implicit_thresholds_and_holds_when_policy_not_met():
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(
            sample("a", champion=0.60, outcome=True, baselines=(baseline(p=0.80),)),
            sample("b", champion=0.40, outcome=False, baselines=(baseline(p=0.20),)),
        ),
        selective_confidence_thresholds=(0.95,),
        counterfactual_samples=(),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(
            minimum_holdout_n=10,
            minimum_brier_advantage=0.01,
            minimum_log_loss_advantage=0.01,
            minimum_selected_coverage=0.75,
        ),
    )
    blockers = set(report.complexity_gate.blockers)
    assert "HOLDOUT_N_BELOW_POLICY_MINIMUM" in blockers
    assert "BRIER_ADVANTAGE_BELOW_POLICY_MINIMUM" in blockers
    assert "LOG_LOSS_ADVANTAGE_BELOW_POLICY_MINIMUM" in blockers
    assert "SELECTIVE_COVERAGE_POLICY_UNSATISFIED" in blockers
    assert report.complexity_gate.recommendation is ComplexityRecommendation.HOLD


def test_missing_baselines_fail_complexity_gate_not_probability_scoring():
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(
            sample("a", baselines=()),
            sample("b", champion=0.30, outcome=False, baselines=()),
        ),
        selective_confidence_thresholds=(0.0,),
        counterfactual_samples=(),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(),
    )
    assert len(report.model_scores) == 1
    assert "BASELINE_COMPARISON_MISSING" in report.complexity_gate.blockers
    assert "BASELINE_LOG_LOSS_COMPARISON_MISSING" in report.complexity_gate.blockers


def test_report_identity_is_order_independent():
    fx = fixture()
    layer = ValidationMetaLayer()
    kwargs = dict(
        as_of_time="2026-10-04T19:00:00Z",
        selective_confidence_thresholds=(0.8, 0.0),
        red_team_fixtures=(fx,),
        complexity_policy=gate(),
    )
    a = layer.evaluate(
        probability_samples=(sample("a"), sample("b", champion=0.30, outcome=False)),
        counterfactual_samples=(counterfactual("d1"), counterfactual("d2", DecisionAction.WAIT)),
        red_team_observations=(observation(fx),),
        **kwargs,
    )
    b = layer.evaluate(
        probability_samples=(sample("b", champion=0.30, outcome=False), sample("a")),
        counterfactual_samples=(counterfactual("d2", DecisionAction.WAIT), counterfactual("d1")),
        red_team_observations=(observation(fx),),
        **kwargs,
    )
    assert a.report_id == b.report_id


def test_future_outcomes_fail_point_in_time_validation():
    fx = fixture()
    with pytest.raises(ValidationMetaError, match="VALIDATION_FUTURE_OUTCOME"):
        ValidationMetaLayer().evaluate(
            as_of_time="2026-10-04T17:00:00Z",
            probability_samples=(sample("a"),),
            selective_confidence_thresholds=(0.0,),
            counterfactual_samples=(),
            red_team_fixtures=(fx,),
            red_team_observations=(),
            complexity_policy=gate(minimum_holdout_n=1),
        )
    with pytest.raises(ValidationMetaError, match="COUNTERFACTUAL_FUTURE_OUTCOME"):
        ValidationMetaLayer().evaluate(
            as_of_time="2026-10-04T17:00:00Z",
            probability_samples=(replace(sample("a"), settled_at="2026-10-04T16:30:00Z"),),
            selective_confidence_thresholds=(0.0,),
            counterfactual_samples=(counterfactual("d1"),),
            red_team_fixtures=(fx,),
            red_team_observations=(),
            complexity_policy=gate(minimum_holdout_n=1),
        )


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan")])
def test_selective_threshold_is_bounded(threshold):
    fx = fixture()
    with pytest.raises(ValidationMetaError, match="SELECTIVE_CONFIDENCE_THRESHOLD"):
        ValidationMetaLayer().evaluate(
            as_of_time="2026-10-04T19:00:00Z",
            probability_samples=(sample("a"),),
            selective_confidence_thresholds=(threshold,),
            counterfactual_samples=(),
            red_team_fixtures=(fx,),
            red_team_observations=(),
            complexity_policy=gate(minimum_holdout_n=1),
        )


def test_execution_flags_fail_closed_across_meta_layer():
    with pytest.raises(ValidationMetaError, match="BASELINE_EXECUTION_PROHIBITED"):
        replace(baseline(), can_execute=True)
    with pytest.raises(ValidationMetaError, match="VALIDATION_SAMPLE_EXECUTION_PROHIBITED"):
        replace(sample("a"), can_execute=True)
    with pytest.raises(ValidationMetaError, match="RED_TEAM_EXECUTION_PROHIBITED"):
        replace(fixture(), can_execute=True)
    with pytest.raises(ValidationMetaError, match="COMPLEXITY_GATE_EXECUTION_PROHIBITED"):
        replace(gate(), can_execute=True)


def test_market_baseline_does_not_control_meteorological_complexity_gate():
    nbm = baseline(BaselineKind.NBM, ident="nbm", p=0.55)
    market = baseline(BaselineKind.MARKET, ident="market", p=0.95)
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(
            sample("a", champion=0.80, outcome=True, baselines=(nbm, market)),
            sample("b", champion=0.20, outcome=False, baselines=(
                replace(nbm, p_yes=0.45),
                replace(market, p_yes=0.05),
            )),
        ),
        selective_confidence_thresholds=(0.0,),
        counterfactual_samples=(),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(),
    )
    market_delta = next(item for item in report.baseline_deltas if item.baseline_kind == "MARKET")
    nbm_delta = next(item for item in report.baseline_deltas if item.baseline_kind == "NBM")
    assert market_delta.champion_brier_advantage < 0
    assert nbm_delta.champion_brier_advantage > 0
    assert report.complexity_gate.recommendation is ComplexityRecommendation.ELIGIBLE_FOR_GOVERNED_REVIEW


def test_validation_report_identity_detects_manifest_tampering():
    fx = fixture()
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=(sample("a"), sample("b", champion=0.30, outcome=False)),
        selective_confidence_thresholds=(0.0,),
        counterfactual_samples=(),
        red_team_fixtures=(fx,),
        red_team_observations=(observation(fx),),
        complexity_policy=gate(),
    )
    manifest = report.input_manifest
    manifest["as_of_time"] = "2026-10-04T19:01:00Z"
    import json
    with pytest.raises(ValidationMetaError, match="VALIDATION_REPORT_IDENTITY_MISMATCH"):
        replace(report, input_manifest_json=json.dumps(manifest))
