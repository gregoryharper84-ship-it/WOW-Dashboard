from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v17.domain_assertions import (
    AssertionDisposition,
    AssertionTier,
    DomainAssertion,
    evaluate_domain_assertions,
)
from v17.specialist_capability_inspector import resolve_capability_matrix


def test_soft_guard_never_fails_closed():
    assertions = [
        DomainAssertion(
            assertion_id="NBA_POSSESSION_PLAUSIBILITY",
            tier=AssertionTier.PLAUSIBILITY_GUARD,
            predicate=lambda row: 88 <= row["possessions"] <= 112,
            failure_code="NBA_POSSESSION_OUTLIER",
            description="Empirical pace plausibility range only.",
        )
    ]
    receipt = evaluate_domain_assertions({"possessions": 120}, assertions)
    assert receipt["hard_gate_passed"] is True
    assert receipt["hard_failures"] == []
    assert receipt["telemetry_flags"] == ["NBA_POSSESSION_OUTLIER"]
    assert receipt["results"][0]["disposition"] == AssertionDisposition.TELEMETRY_FLAG.value


def test_hard_invariant_fails_closed_with_typed_reason():
    assertions = [
        DomainAssertion(
            assertion_id="PROBABILITY_SIMPLEX",
            tier=AssertionTier.HARD_INVARIANT,
            predicate=lambda row: abs(sum(row["probabilities"]) - 1.0) <= 0.0001,
            failure_code="PROBABILITY_NORMALIZATION_INVALID",
            description="Mutually exclusive outcome probabilities must normalize.",
        )
    ]
    receipt = evaluate_domain_assertions({"probabilities": [0.50, 0.40]}, assertions)
    assert receipt["hard_gate_passed"] is False
    assert receipt["hard_failures"] == ["PROBABILITY_NORMALIZATION_INVALID"]


def test_capability_inspector_does_not_self_certify():
    receipt = resolve_capability_matrix(
        [
            {
                "sport": "NFL",
                "market_type": "SPREAD",
                "specialist_id": "nfl_spread_keynumber_v2",
                "registered": True,
                "owner_lane": "research/shadow",
                "certification": {"is_active": True},
            },
            {
                "sport": "SOCCER",
                "market_type": "3WAY",
                "specialist_id": "soccer_3way_dixon_coles_v2",
                "registered": False,
                "owner_lane": "unregistered",
            },
        ]
    )
    nfl = receipt["capability_matrix"]["NFL:SPREAD"]
    soccer = receipt["capability_matrix"]["SOCCER:3WAY"]
    assert nfl["model_qualified"] is False
    assert nfl["typed_gate_reason"] == "RUNTIME_VERIFICATION_REQUIRED"
    assert soccer["typed_gate_reason"] == "SPECIALIST_NOT_REGISTERED"
    assert receipt["can_execute"] is False
