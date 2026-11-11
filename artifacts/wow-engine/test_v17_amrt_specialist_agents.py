"""Source-only AMRT specialist regression/adversarial tests.

No external credentials, deployment, probability computation or real writes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from v17.amrt_specialist_agents import (
    AGENT_TO_EXISTING_ROLE, REPLAY_CASES, STAGES,
    ModelRecoveryCommander, ModelDependencyInvestigator,
    ModelRestorationEngineer, ModelRecoveryRegressionSpecialist,
    RecoveryEscalationDeadlockBreaker, SpecialistDecision,
    to_existing_role_receipt,
)

NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def case(**changes):
    item = dict(
        incident_id="1841", issue_ref="github:issue/1841",
        owner_id="wow-engineering[bot]", lease_owner="wow-engineering[bot]",
        lease_epoch=14, lease_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        lease_authority="EXISTING_AUTHENTICATED_ENGINEERING_WORKER",
        reproduction_ref="run:nfl-20261018", root_cause_ref="github:1841#root-cause",
        acceptance_ref="github:1841#acceptance", sport="NFL", market="ML",
        event_id="nfl-game-1", model_id="registered-nfl-ml",
        change_class="B", changed_contracts=("publication_adapter",),
        head_sha=SHA, provider_attempts=1, last_progress_at=(NOW - timedelta(hours=1)).isoformat(),
        authorized_fallback=False,
    )
    item.update(changes)
    return item


def stage_evidence(**override_status):
    stages = {stage: {"status": "PASS", "evidence_ref": "producer:" + stage}
              for stage in STAGES}
    for key, value in override_status.items():
        stages[key]["status"] = value
    return stages


def evidence_all():
    return {name: {"pass": True, "sha": SHA, "evidence_ref": "ci:" + name}
            for name in REPLAY_CASES}


def test_all_five_are_real_callable_specialists_not_independent_authorities():
    assert len(AGENT_TO_EXISTING_ROLE) == 5
    calls = (
        ModelRecoveryCommander().run(case()),
        ModelDependencyInvestigator().run(case(), stage_evidence()),
        ModelRestorationEngineer().run(case()),
        ModelRecoveryRegressionSpecialist().run(case()),
        RecoveryEscalationDeadlockBreaker().run(case(), now=NOW),
    )
    assert len({r.agent for r in calls}) == 5
    for decision in calls:
        assert decision.can_execute is False
        assert decision.terminal_authority == "V17_TERMINAL_REDUCER"
        assert decision.activation_status == "SOURCE_ONLY_NOT_ACTIVATED"
        receipt = to_existing_role_receipt(decision)
        assert receipt.role in AGENT_TO_EXISTING_ROLE.values()
        assert receipt.evidence_refs[0] == "specialist:" + decision.agent


def test_commander_preserves_owner_through_external_release():
    commander = ModelRecoveryCommander()
    result = commander.run(case(stage="REPAIR"))
    assert result.next_owner == "MODEL_RECOVERY_ENGINEER"
    assert result.accountable_owner == "wow-engineering[bot]"
    assert commander.run(case(stage="CLOSURE")).state == "HELD"
    ready = commander.run(case(stage="CLOSURE", independent_qa_ref="qa:independent",
                               release_authority_ref="release:independent"))
    assert ready.next_owner == "RELEASE_VERIFICATION"
    assert ready.state == "ROUTED"
    assert "INDEPENDENT_PRODUCTION_CLOSURE_REQUIRED" in ready.reasons
    assert commander.run(case(closed=True)).state == "HELD"
    assert commander.run(case(lease_owner="other-agent")).next_owner == "ECOSYSTEM_CONDUCTOR"
    assert commander.run(case(owner_id=None)).state == "HELD"
    assert commander.run(case(stage="BOGUS")).state == "HELD"


def test_dependency_stops_at_first_actual_missing_proof_not_guessed_pass():
    investigator = ModelDependencyInvestigator()
    stages = stage_evidence(dynamic_calibration="EVIDENCE_NOT_EMITTED",
                            final_refresh="EVIDENCE_NOT_EMITTED")
    result = investigator.run(case(), stages)
    assert result.next_owner == "MODEL_RECOVERY_ENGINEER"
    assert result.reasons == ("FIRST_UNPROVEN_DEPENDENCY:dynamic_calibration:EVIDENCE_NOT_EMITTED",)
    assert result.evidence_refs[-1] == "producer:dynamic_calibration"
    stages["fitted_artifact"]["status"] = "MODEL_UNAVAILABLE"
    assert "fitted_artifact:MODEL_UNAVAILABLE" in investigator.run(case(), stages).reasons[0]


def test_dependency_no_assumed_artifacts_and_identity_attribution():
    i = ModelDependencyInvestigator()
    assert i.run(case(model_id=None), stage_evidence()).state == "HELD"
    assert i.run(case(), {}).reasons == ("DEPENDENCY_STAGE_UNOBSERVED:discovery",)
    broken = stage_evidence(canonical_identity="FAIL")
    assert i.run(case(), broken).next_owner == "DATA_INTEGRATION_ENGINEER"
    broken["canonical_identity"]["evidence_ref"] = None
    assert i.run(case(), broken).state == "HELD"
    assert i.run(case(), stage_evidence()).state == "HELD"


@pytest.mark.parametrize("change,scope", [
    ("C", ()),
    ("B", ("fitted_probabilities",)),
    ("A", ("calibration_math",)),
    ("B", ("terminal_precedence",)),
])
def test_restoration_routes_class_c_without_model_mutation(change, scope):
    result = ModelRestorationEngineer().run(
        case(change_class=change, changed_contracts=scope))
    assert result.state == "HELD"
    assert result.next_owner == "MODEL_EXCELLENCE"
    assert "CLASS_C_MODEL_CERTIFICATION_REQUIRED" in result.reasons


def test_restoration_requires_cause_acceptance_and_single_authorized_writer():
    r = ModelRestorationEngineer()
    assert r.run(case(root_cause_ref=None)).state == "HELD"
    assert r.run(case(changed_contracts=None)).state == "HELD"
    assert r.run(case(change_class="B", source_pr_open=True)).state == "PR_REVIEW"
    decision = r.run(case())
    assert decision.state == "ROUTED" and decision.next_owner == "CODE_CI_ENGINEER"
    assert "AUTHORIZED_WORKER_AND_ATOMIC_LEASE_REQUIRED" in decision.reasons
    assert "CLASS_B_INDEPENDENT_REVIEW" in decision.required_checks


def test_regression_matrix_does_not_self_certify_qa():
    specialist = ModelRecoveryRegressionSpecialist()
    plan = specialist.run(case())
    assert plan.required_checks == REPLAY_CASES and plan.next_owner == "CODE_CI_ENGINEER"
    accepted = specialist.run(case(), evidence_all())
    assert accepted.state == "PR_REVIEW" and accepted.next_owner == "INDEPENDENT_QA"
    assert accepted.can_execute is False
    asserted = evidence_all()
    asserted["MISSING_STAGE_PROOF_REMAINS_HELD"]["pass"] = False
    assert "REPLAY_NOT_PROVEN" in specialist.run(case(), asserted).reasons[0]
    asserted = evidence_all()
    asserted["FULL_COMPACT_ACTION_PARITY"]["sha"] = "b" * 40
    assert "REPLAY_SHA_OR_RECEIPT_MISMATCH" in specialist.run(case(), asserted).reasons[0]
    assert specialist.run(case(head_sha="short"), evidence_all()).state == "HELD"


def test_deadlock_never_claims_another_writer_or_stalled_pr():
    d = RecoveryEscalationDeadlockBreaker()
    assert d.run(case(source_pr_open=True), now=NOW).state == "PR_REVIEW"
    assert d.run(case(source_pr_open=True, ci_failed=True), now=NOW).next_owner == "CODE_CI_ENGINEER"
    assert d.run(case(lease_expires_at=(NOW - timedelta(seconds=1)).isoformat()), now=NOW).state == "REENTRY_REQUIRED"
    assert d.run(case(lease_authority="UNVERIFIED"), now=NOW).state == "HELD"
    assert d.run(case(owner_id=None), now=NOW).next_owner == "ECOSYSTEM_CONDUCTOR"


def test_deadlock_fallback_requires_active_atomic_lease_and_bounded_attempts():
    d = RecoveryEscalationDeadlockBreaker()
    held = d.run(case(authorized_fallback=False), now=NOW)
    assert held.state == "HELD" and "PROVIDER_STAGNANT_NO_AUTHORIZED_FALLBACK" in held.reasons
    fallback = d.run(case(authorized_fallback=True), now=NOW)
    assert fallback.state == "ROUTED"
    assert "SAME_LEASE_ONLY" in fallback.reasons[0]
    assert d.run(case(provider_attempts=3, authorized_fallback=True), now=NOW).state == "HELD"
    assert d.run(case(last_progress_at=(NOW - timedelta(minutes=1)).isoformat()), now=NOW).state == "READY"
    assert d.run(case(last_progress_at="bad-time"), now=NOW).state == "HELD"
    assert d.run(case(provider_attempts=None), now=NOW).state == "HELD"
    with pytest.raises(ValueError, match="OFFSET_AWARE"):
        d.run(case(), now=datetime(2026, 10, 19))


def test_untrusted_specialist_result_cannot_forge_closure_or_execution():
    legit = ModelRecoveryCommander().run(case())
    fake = SpecialistDecision(**{**legit.__dict__, "can_execute": True})
    with pytest.raises(ValueError, match="INVALID_AMRT_SPECIALIST_HANDOFF"):
        to_existing_role_receipt(fake)
    fake = SpecialistDecision(**{**legit.__dict__, "state": "FIXED_AND_VERIFIED"})
    with pytest.raises(ValueError, match="INVALID_AMRT_SPECIALIST_HANDOFF"):
        to_existing_role_receipt(fake)
