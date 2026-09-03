from __future__ import annotations

import pytest

from v17.certified_numerical_engine import (
    CertifiedComputationRequest,
    GovernedProbabilityEnvelope,
    ModelFamily,
    NumericalComputationResult,
    V17Lane,
    VerificationStatus,
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
        model_family=(
            ModelFamily.BRADLEY_TERRY
            if lane is V17Lane.TEAM_EVENT_ML
            else ModelFamily.MONTE_CARLO
        ),
        computation_engine="PYTHON_PRIMARY",
        computation_method="fixture",
        raw_probability=probability,
        unconditional_probability=probability,
    )


def test_prop_contract_is_sport_agnostic():
    for sport, stat in (
        ("MLB", "pitcher_strikeouts"),
        ("NBA", "points"),
        ("WNBA", "assists"),
        ("NFL", "passing_yards"),
        ("NHL", "shots_on_goal"),
        ("SOCCER", "player_shots"),
        ("TENNIS", "aces"),
        ("GOLF", "birdies"),
    ):
        request = CertifiedComputationRequest(
            candidate_id=f"{sport}:{stat}",
            lane=V17Lane.PROP,
            sport=sport,
            market_or_stat=stat,
            controlling_specialist="sport.stat.specialist",
            model_version="v1",
            model_family=ModelFamily.MONTE_CARLO,
            certified_inputs={"fixture": 1},
        )
        request.validate()


def test_moneyline_contract_is_sport_agnostic():
    for sport in ("MLB", "NBA", "WNBA", "NFL", "NHL", "SOCCER", "TENNIS", "MMA", "BOXING", "GOLF"):
        request = CertifiedComputationRequest(
            candidate_id=f"{sport}:event",
            lane=V17Lane.TEAM_EVENT_ML,
            sport=sport,
            market_or_stat="moneyline",
            controlling_specialist="llp.team.event.specialist",
            model_version="v1",
            model_family=ModelFamily.BRADLEY_TERRY,
            certified_inputs={"fixture": 1},
        )
        request.validate()


def test_model_family_registry_separates_prop_and_ml_families():
    prop = supported_model_families(V17Lane.PROP)
    ml = supported_model_families(V17Lane.TEAM_EVENT_ML)
    assert ModelFamily.POISSON in prop
    assert ModelFamily.EVENT_TREE in prop
    assert ModelFamily.BRADLEY_TERRY in ml
    assert ModelFamily.SPORT_SPECIFIC_EVENT_SIMULATION in ml


def test_independent_verification_conflict_is_typed():
    status, delta = verify_independent_probability(
        primary_probability=0.62,
        verifier_probability=0.60,
        tolerance=0.005,
    )
    assert status is VerificationStatus.CONFLICT
    assert delta == pytest.approx(0.02)


def test_rank_eligibility_requires_calibrated_lower_bound():
    envelope = GovernedProbabilityEnvelope(
        numerical_result=_result(lane=V17Lane.PROP, sport="NBA", market="points"),
        calibration_status="COMPLETE",
        calibrated_probability=0.61,
        calibrated_lower_bound=None,
        calibrated_upper_bound=0.65,
        rank_eligible=True,
        model_qualified=True,
        market_status="NO_MARKET",
        terminal_label="MODEL_QUALIFIED_HOLD",
    )
    with pytest.raises(ValueError, match="rank_eligible_without_lower_bound"):
        envelope.validate()


def test_missing_market_does_not_erase_completed_sporting_probability():
    envelope = GovernedProbabilityEnvelope(
        numerical_result=_result(lane=V17Lane.TEAM_EVENT_ML, sport="NFL", market="moneyline"),
        calibration_status="COMPLETE",
        calibrated_probability=0.61,
        calibrated_lower_bound=0.57,
        calibrated_upper_bound=0.65,
        rank_eligible=False,
        model_qualified=False,
        market_status="NO_MARKET",
        terminal_label="MODEL_COMPLETE_MARKET_HELD",
        blockers=("NO_MARKET",),
    )
    envelope.validate()
    assert envelope.calibrated_probability == pytest.approx(0.61)
    assert envelope.market_status == "NO_MARKET"
