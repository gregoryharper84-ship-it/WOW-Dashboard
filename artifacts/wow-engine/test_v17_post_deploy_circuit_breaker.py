import pytest

from v17.post_deploy_circuit_breaker import RollbackEvidence, evaluate_rollback


def _evidence(**overrides):
    values = dict(
        change_class="A",
        failure_class="APPLICATION_HTTP_5XX",
        current_deploy_sha="a" * 40,
        previous_sha="b" * 40,
        deployment_attributed=True,
        confirmations=2,
        previous_production_accepted=True,
        schema_compatible=True,
        persistence_compatible=True,
        can_execute=False,
    )
    values.update(overrides)
    return RollbackEvidence(**values)


def test_confirmed_deployment_caused_failure_can_authorize_rollback():
    assert evaluate_rollback(_evidence())[0] == "ROLLBACK_AUTHORIZED"


@pytest.mark.parametrize(
    ("overrides", "decision"),
    [
        ({"deployment_attributed": False}, "RETRY_REQUIRED"),
        ({"confirmations": 1}, "RETRY_REQUIRED"),
        ({"previous_production_accepted": False}, "BLOCKED_WITH_EXACT_REASON"),
        ({"schema_compatible": False}, "BLOCKED_WITH_EXACT_REASON"),
        ({"persistence_compatible": False}, "BLOCKED_WITH_EXACT_REASON"),
        ({"change_class": "C"}, "BLOCKED_WITH_EXACT_REASON"),
        ({"failure_class": "PROVIDER_OUTAGE"}, "NO_ROLLBACK"),
    ],
)
def test_ambiguous_or_unsafe_rollback_is_blocked(overrides, decision):
    assert evaluate_rollback(_evidence(**overrides))[0] == decision


def test_deterministic_safety_header_failure_needs_one_confirmation_not_two():
    evidence = _evidence(failure_class="SAFETY_HEADER_MISSING", confirmations=1)
    assert evaluate_rollback(evidence)[0] == "ROLLBACK_AUTHORIZED"
