from __future__ import annotations

import pytest

from v17.certified_numerical_engine import (
    CertifiedComputationRequest,
    CertifiedNumericalAdapter,
    CertifiedNumericalRegistry,
    GovernedProbabilityEnvelope,
    ModelFamily,
    NumericalComputationResult,
    NumericalFailure,
    V17Lane,
    VerificationStatus,
    execute_certified_computation,
    supported_model_families,
    verify_independent_probability,
)


def _result(*, lane: V17Lane, sport: str, market: str, probability: float = 0.62):
    return NumericalComputationResult(
        candidate_id=f"{sport}:{market}",
        lane=lane,
        sport=sport,
        market_or_stat=market,
        controlling_specialist="certified.specialist",
        model_version="v1",
        model_family=(ModelFamily.BRADLEY_TERRY if lane is V17Lane.TEAM_EVENT_ML else ModelFamily.MONTE_CARLO),
        computation_engine="PYTHON_PRIMARY",
        computation_method="fixture",
        raw_probability=probability,
        unconditional_probability=probability,
    )


def _primary(request: CertifiedComputationRequest) -> NumericalComputationResult:
    probability = float(request.certified_inputs["probability"])
    return NumericalComputationResult(
        candidate_id=request.candidate_id,
        lane=request.lane,
        sport=request.sport,
        market_or_stat=request.market_or_stat,
        controlling_specialist=request.controlling_specialist,
        model_version=request.model_version,
        model_family=request.model_family,
        computation_engine="ignored",
        computation_method="certified_fixture",
        raw_probability=probability,
        unconditional_probability=probability,
        simulation_count=request.simulation_count,
        random_seed=request.random_seed,
        convergence_status="PASS",
    )


def _verify(request: CertifiedComputationRequest, result: NumericalComputationResult):
    return result.unconditional_probability, "independent_fixture"


def _registry(*, lane: V17Lane, sport: str, market: str, specialist: str, family: ModelFamily, verifier=True):
    registry = CertifiedNumericalRegistry()
    registry.register(CertifiedNumericalAdapter(
        adapter_id=f"{sport}-{market}-{family.value}",
        lane=lane,
        sport=sport,
        market_or_stat=market,
        controlling_specialist=specialist,
        model_family=family,
        computation_version="engine-v1",
        primary=_primary,
        verifier=_verify if verifier else None,
    ))
    return registry


def test_prop_contract_is_sport_agnostic():
    for sport, stat in (
        ("MLB", "pitcher_strikeouts"), ("NBA", "points"), ("WNBA", "assists"),
        ("NFL", "passing_yards"), ("NHL", "shots_on_goal"), ("SOCCER", "player_shots"),
        ("TENNIS", "aces"), ("GOLF", "birdies"), ("MMA", "significant_strikes"),
    ):
        CertifiedComputationRequest(
            candidate_id=f"{sport}:{stat}", lane=V17Lane.PROP, sport=sport, market_or_stat=stat,
            controlling_specialist="sport.stat.specialist", model_version="v1",
            model_family=ModelFamily.MONTE_CARLO, certified_inputs={"probability": 0.62},
        ).validate()


def test_moneyline_contract_is_sport_agnostic():
    for sport in ("MLB", "NBA", "WNBA", "NFL", "NHL", "SOCCER", "TENNIS", "MMA", "BOXING", "GOLF", "NASCAR"):
        CertifiedComputationRequest(
            candidate_id=f"{sport}:event", lane=V17Lane.TEAM_EVENT_ML, sport=sport,
            market_or_stat="moneyline", controlling_specialist="llp.team.event.specialist",
            model_version="v1", model_family=ModelFamily.BRADLEY_TERRY,
            certified_inputs={"probability": 0.62},
        ).validate()


def test_model_family_registry_separates_prop_and_ml_families():
    prop = supported_model_families(V17Lane.PROP)
    ml = supported_model_families(V17Lane.TEAM_EVENT_ML)
    assert ModelFamily.POISSON in prop
    assert ModelFamily.EVENT_TREE in prop
    assert ModelFamily.BRADLEY_TERRY in ml
    assert ModelFamily.SPORT_SPECIFIC_EVENT_SIMULATION in ml
    assert ModelFamily.POISSON not in ml


def test_registered_prop_adapter_executes_and_verifies():
    specialist = "nba.points.specialist"
    request = CertifiedComputationRequest(
        candidate_id="nba:player:points:23.5", lane=V17Lane.PROP, sport="NBA", market_or_stat="points",
        controlling_specialist=specialist, model_version="v4", model_family=ModelFamily.MONTE_CARLO,
        certified_inputs={"probability": 0.641}, simulation_count=25000, random_seed=17,
        verification_required=True, verification_tolerance=1e-9,
    )
    outcome = execute_certified_computation(
        request,
        registry=_registry(lane=V17Lane.PROP, sport="NBA", market="points", specialist=specialist, family=ModelFamily.MONTE_CARLO),
    )
    assert outcome.completed is True
    assert outcome.result is not None
    assert outcome.result.computation_engine == "PYTHON_PRIMARY"
    assert outcome.result.verification_status is VerificationStatus.PASS
    assert outcome.result.simulation_count == 25000


def test_registered_moneyline_adapter_executes_for_any_sport():
    specialist = "llp.tennis.event.specialist"
    request = CertifiedComputationRequest(
        candidate_id="tennis:event:123", lane=V17Lane.TEAM_EVENT_ML, sport="TENNIS", market_or_stat="moneyline",
        controlling_specialist=specialist, model_version="v2", model_family=ModelFamily.BRADLEY_TERRY,
        certified_inputs={"probability": 0.587},
    )
    outcome = execute_certified_computation(
        request,
        registry=_registry(lane=V17Lane.TEAM_EVENT_ML, sport="TENNIS", market="moneyline", specialist=specialist, family=ModelFamily.BRADLEY_TERRY),
    )
    assert outcome.completed is True
    assert outcome.result.unconditional_probability == pytest.approx(0.587)


def test_missing_adapter_is_typed_not_generic_probability_fallback():
    request = CertifiedComputationRequest(
        candidate_id="new-sport:event", lane=V17Lane.TEAM_EVENT_ML, sport="NEWSPORT", market_or_stat="moneyline",
        controlling_specialist="new.sport.specialist", model_version="v1", model_family=ModelFamily.BRADLEY_TERRY,
        certified_inputs={"probability": 0.60},
    )
    outcome = execute_certified_computation(request, registry=CertifiedNumericalRegistry())
    assert outcome.completed is False
    assert outcome.failure is NumericalFailure.CERTIFIED_ADAPTER_UNAVAILABLE


def test_required_verifier_missing_is_typed_failure_not_model_unavailable():
    specialist = "nfl.event.specialist"
    request = CertifiedComputationRequest(
        candidate_id="nfl:event", lane=V17Lane.TEAM_EVENT_ML, sport="NFL", market_or_stat="moneyline",
        controlling_specialist=specialist, model_version="v1", model_family=ModelFamily.BRADLEY_TERRY,
        certified_inputs={"probability": 0.60}, verification_required=True,
    )
    outcome = execute_certified_computation(
        request,
        registry=_registry(lane=V17Lane.TEAM_EVENT_ML, sport="NFL", market="moneyline", specialist=specialist, family=ModelFamily.BRADLEY_TERRY, verifier=False),
    )
    assert outcome.failure is NumericalFailure.COMPUTATION_VERIFICATION_FAILED


def test_independent_verification_conflict_is_typed():
    status, delta = verify_independent_probability(primary_probability=0.62, verifier_probability=0.60, tolerance=0.005)
    assert status is VerificationStatus.CONFLICT
    assert delta == pytest.approx(0.02)


def test_rank_eligibility_requires_calibrated_lower_bound():
    envelope = GovernedProbabilityEnvelope(
        numerical_result=_result(lane=V17Lane.PROP, sport="NBA", market="points"),
        calibration_status="COMPLETE", calibrated_probability=0.61, calibrated_lower_bound=None,
        calibrated_upper_bound=0.65, rank_eligible=True, model_qualified=True,
        market_status="NO_MARKET", terminal_label="MODEL_QUALIFIED_HOLD",
    )
    with pytest.raises(ValueError, match="rank_eligible_without_lower_bound"):
        envelope.validate()


def test_missing_market_does_not_erase_completed_sporting_probability():
    envelope = GovernedProbabilityEnvelope(
        numerical_result=_result(lane=V17Lane.TEAM_EVENT_ML, sport="NFL", market="moneyline"),
        calibration_status="COMPLETE", calibrated_probability=0.61, calibrated_lower_bound=0.57,
        calibrated_upper_bound=0.65, rank_eligible=False, model_qualified=False,
        market_status="NO_MARKET", terminal_label="MODEL_COMPLETE_MARKET_HELD", blockers=("NO_MARKET",),
    )
    envelope.validate()
    assert envelope.calibrated_probability == pytest.approx(0.61)
    assert envelope.market_status == "NO_MARKET"
