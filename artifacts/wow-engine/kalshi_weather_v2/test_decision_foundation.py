import pytest

from kalshi_weather_v2.decision_policy import DecisionAction, DecisionPolicy, DecisionPolicyError, DecisionPolicyRegistry
from kalshi_weather_v2.uncertainty import UncertaintyDecomposition, UncertaintyError


def policy(**overrides):
    values = {
        "policy_id": "KALSHI_WEATHER_DECISION_POLICY",
        "version": "research-v1",
        "evidence_id": "shadow-replay-2026-10",
        "effective_from": "2026-10-04T00:00:00Z",
    }
    values.update(overrides)
    return DecisionPolicy(**values)


def test_decision_policy_has_no_implicit_numeric_thresholds():
    item = policy()
    assert item.minimum_edge is None
    assert item.minimum_voi_gain is None
    assert item.minimum_edge_survival_probability is None
    assert item.research_only is True
    assert item.can_execute is False


def test_decision_policy_requires_governed_evidence():
    with pytest.raises(DecisionPolicyError, match="DECISION_POLICY_EVIDENCE_MISSING"):
        policy(evidence_id="")


def test_decision_policy_cannot_authorize_execution():
    with pytest.raises(DecisionPolicyError, match="DECISION_POLICY_EXECUTION_PROHIBITED"):
        policy(can_execute=True)


def test_registry_is_versioned_and_collision_safe():
    registry = DecisionPolicyRegistry()
    first = registry.register(policy())
    assert registry.get(first.policy_id, first.version) == first
    assert registry.register(first) == first
    with pytest.raises(DecisionPolicyError, match="DECISION_POLICY_VERSION_COLLISION"):
        registry.register(policy(minimum_edge=0.04))


def test_edge_survival_threshold_is_probability_bounded():
    with pytest.raises(DecisionPolicyError, match="MINIMUM_EDGE_SURVIVAL_PROBABILITY_OUT_OF_RANGE"):
        policy(minimum_edge_survival_probability=1.01)


def test_decision_actions_are_explicit_and_closed():
    assert {action.value for action in DecisionAction} == {"TRADE_NOW", "WAIT", "ABSTAIN"}


def test_uncertainty_decomposition_is_explicit_and_nonnegative():
    item = UncertaintyDecomposition(
        aleatoric=0.03,
        epistemic=0.02,
        data=0.01,
        settlement=0.015,
        evidence={"profile": "shadow-only"},
    )
    assert item.as_dict()["market_price_used_as_weather_input"] is False


def test_uncertainty_rejects_negative_or_nonfinite_components():
    with pytest.raises(UncertaintyError, match="EPISTEMIC_UNCERTAINTY_INVALID"):
        UncertaintyDecomposition(aleatoric=0.01, epistemic=-0.01, data=0.0, settlement=0.0)


def test_market_price_cannot_enter_weather_uncertainty_path():
    with pytest.raises(UncertaintyError, match="MARKET_PRICE_WEATHER_INPUT_PROHIBITED"):
        UncertaintyDecomposition(
            aleatoric=0.01,
            epistemic=0.01,
            data=0.01,
            settlement=0.01,
            market_price_used_as_weather_input=True,
        )
