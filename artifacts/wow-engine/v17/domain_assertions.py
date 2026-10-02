from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence


class AssertionTier(str, Enum):
    HARD_INVARIANT = "HARD_INVARIANT"
    PLAUSIBILITY_GUARD = "PLAUSIBILITY_GUARD"


class AssertionDisposition(str, Enum):
    PASS = "PASS"
    FAIL_CLOSED = "FAIL_CLOSED"
    TELEMETRY_FLAG = "TELEMETRY_FLAG"


@dataclass(frozen=True)
class DomainAssertion:
    assertion_id: str
    tier: AssertionTier
    predicate: Callable[[Mapping[str, Any]], bool]
    failure_code: str
    description: str


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    tier: AssertionTier
    disposition: AssertionDisposition
    failure_code: str | None
    description: str


def evaluate_domain_assertions(
    telemetry: Mapping[str, Any],
    assertions: Sequence[DomainAssertion],
) -> Dict[str, Any]:
    """Evaluate legal/physical invariants separately from empirical plausibility guards.

    Hard-invariant failures fail closed. Plausibility failures are telemetry only and
    must never be promoted to a model/certification failure by this layer.
    """
    results: List[AssertionResult] = []
    hard_failures: List[str] = []
    telemetry_flags: List[str] = []

    for assertion in assertions:
        try:
            passed = bool(assertion.predicate(telemetry))
        except Exception:
            passed = False

        if passed:
            disposition = AssertionDisposition.PASS
            failure_code = None
        elif assertion.tier == AssertionTier.HARD_INVARIANT:
            disposition = AssertionDisposition.FAIL_CLOSED
            failure_code = assertion.failure_code
            hard_failures.append(assertion.failure_code)
        else:
            disposition = AssertionDisposition.TELEMETRY_FLAG
            failure_code = assertion.failure_code
            telemetry_flags.append(assertion.failure_code)

        results.append(
            AssertionResult(
                assertion_id=assertion.assertion_id,
                tier=assertion.tier,
                disposition=disposition,
                failure_code=failure_code,
                description=assertion.description,
            )
        )

    return {
        "hard_gate_passed": not hard_failures,
        "hard_failures": hard_failures,
        "telemetry_flags": telemetry_flags,
        "results": [
            {
                "assertion_id": r.assertion_id,
                "tier": r.tier.value,
                "disposition": r.disposition.value,
                "failure_code": r.failure_code,
                "description": r.description,
            }
            for r in results
        ],
    }
