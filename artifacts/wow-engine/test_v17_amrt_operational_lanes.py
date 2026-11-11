"""Adversarial coverage of all seven AMRT lanes, with no real source mutation."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.amrt_closure_gate import TEAM
from v17.amrt_operational_lanes import (
    code_ci_recovery, data_integration_recovery, detect_blockers,
    dispatch_preflight, independent_qa, investigate_root_cause,
    model_recovery, release_verification, RoleReceipt,
)

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
SHA = "a" * 40


def case(**overrides):
    record = {
        "incident_id": "1841", "typed_failure": "STAGE_PROOF_NOT_EMITTED",
        "root_cause_ref": "github:1841#reproduction",
        "reproduction_ref": "supabase:validation-run",
        "change_class": "B", "head_sha": SHA,
        "implementation_actor": "wow-engineering[bot]",
        "owner_id": "wow-engineering[bot]",
        "lease_epoch": 13,
        "lease_expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "lease_authority": "EXISTING_AUTHENTICATED_ENGINEERING_WORKER",
    }
    record.update(overrides)
    return record


def signal(**overrides):
    record = {
        "source": "GITHUB", "incident_id": "1841",
        "typed_failure": "STAGE_PROOF_NOT_EMITTED",
        "failure_boundary": "NFL_PUBLICATION", "severity": "P0",
        "evidence_ref": "github:issue/1841",
    }
    record.update(overrides)
    return record


def test_all_roles_are_executable_and_distinct():
    assert len(TEAM) == 7
    assert {
        "BLOCKER_SENTINEL", "ROOT_CAUSE_INVESTIGATOR",
        "MODEL_RECOVERY_ENGINEER", "DATA_INTEGRATION_ENGINEER",
        "CODE_CI_ENGINEER", "INDEPENDENT_QA", "RELEASE_VERIFICATION"
    } == set(TEAM)


def test_sentinel_dedupes_and_routes_without_creating_second_issue():
    receipts = detect_blockers([signal(), signal()])
    assert len(receipts) == 1
    assert receipts[0].role == "BLOCKER_SENTINEL"
    assert receipts[0].next_owner == "ROOT_CAUSE_INVESTIGATOR"
    assert receipts[0].state == "ROUTED"


@pytest.mark.parametrize("updates", [
    {"source": "unknown"}, {"severity": "P9"}, {"evidence_ref": ""},
    {"typed_failure": ""}, {"incident_id": ""},
])
def test_sentinel_holds_missing_proof(updates):
    receipt = detect_blockers([signal(**updates)])[0]
    assert receipt.state == "HELD"


def test_sentinel_source_boundary_stays_distinct():
    receipts = detect_blockers([signal(), signal(source="DATADOG")])
    assert len(receipts) == 2


def test_investigator_requires_actual_cause_and_reproduction():
    assert investigate_root_cause(case()).next_owner == "MODEL_RECOVERY_ENGINEER"
    assert investigate_root_cause(case(root_cause_ref=None)).state == "HELD"


def test_investigator_routes_identity_to_data_specialist():
    assert investigate_root_cause(case(typed_failure="EVENT_IDENTITY_COLLISION")).next_owner == "DATA_INTEGRATION_ENGINEER"


def test_unknown_failure_is_not_guessed():
    assert investigate_root_cause(case(typed_failure="UNCLASSIFIED")).state == "HELD"


def test_model_lane_never_approves_class_c():
    assert model_recovery(case()).next_owner == "CODE_CI_ENGINEER"
    assert model_recovery(case(change_class="C")).state == "HELD"
    assert "CLASS_C_MODEL_OWNER_CERTIFICATION_REQUIRED" in model_recovery(case(change_class="C")).reasons


def test_model_lane_wrong_route_or_missing_evidence_is_held():
    assert model_recovery(case(typed_failure="EVENT_IDENTITY_COLLISION")).state == "HELD"
    assert model_recovery(case(root_cause_ref=None)).state == "HELD"


def test_data_lane_routes_but_never_mutates_probability():
    result = data_integration_recovery(case(typed_failure="EVENT_IDENTITY_COLLISION"))
    assert result.role == "DATA_INTEGRATION_ENGINEER"
    assert result.next_owner == "CODE_CI_ENGINEER"
    assert result.can_execute is False
    assert data_integration_recovery(case(typed_failure="EVENT_IDENTITY_COLLISION", change_class="C")).state == "HELD"


def test_existing_pr_blocks_duplicate_code_writer():
    r = code_ci_recovery(case(), now=NOW, active_source_pr=True,
                         attempts=0, repeated_failure=False, authorized_fallback=True)
    assert r.state == "PR_REVIEW" and r.next_owner == "INDEPENDENT_QA"


def test_writer_lease_is_mandatory():
    r = code_ci_recovery(case(lease_expires_at=(NOW - timedelta(seconds=1)).isoformat()),
                         now=NOW, active_source_pr=False, attempts=0,
                         repeated_failure=False, authorized_fallback=True)
    assert r.state == "REENTRY_REQUIRED"
    r = code_ci_recovery(case(lease_authority="UNTRUSTED"), now=NOW,
                         active_source_pr=False, attempts=0, repeated_failure=False,
                         authorized_fallback=True)
    assert r.state == "HELD"


def test_provider_retries_are_bounded():
    kwargs = dict(now=NOW, active_source_pr=False, authorized_fallback=True)
    assert code_ci_recovery(case(), attempts=0, repeated_failure=False, **kwargs).state == "READY"
    assert code_ci_recovery(case(), attempts=1, repeated_failure=True, **kwargs).state == "READY"
    assert code_ci_recovery(case(), attempts=3, repeated_failure=True, **kwargs).state == "HELD"


def test_independent_qa_must_bind_actor_and_exact_sha_and_negative_tests():
    accepted = {"actor": "qa-independent", "head_sha": SHA, "receipt_ref": "qa:sha",
                "decision": "PASS", "negative_regression_pass": True}
    assert independent_qa(case(), accepted).next_owner == "RELEASE_VERIFICATION"
    assert independent_qa(case(), {**accepted, "actor": "wow-engineering[bot]"}).state == "HELD"
    assert independent_qa(case(), {**accepted, "head_sha": "b" * 40}).state == "HELD"
    assert independent_qa(case(), {**accepted, "negative_regression_pass": False}).state == "HELD"
    assert independent_qa(case(), {**accepted, "receipt_ref": None}).state == "HELD"


def production_receipt():
    keys = (
        "focused_tests_pass", "regression_tests_pass", "ci_green",
        "security_authorized", "independent_review_approved",
        "independent_qa_approved", "release_authorized",
        "production_db_identity_verified", "action_acceptance_pass",
        "persistence_readback_pass", "reconciliation_pass",
        "terminal_states_concordant",
    )
    row = {key: True for key in keys}
    row.update({
        "incident_id": "1841", "implementation_actor": "wow-engineering[bot]",
        "review_actor": "product-review", "qa_actor": "qa-independent",
        "root_cause_evidence": "github:1841", "change_class": "B",
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        "reviewed_head_sha": SHA, "ci_sha": SHA, "review_sha": SHA,
        "authorized_merge_sha": "b" * 40, "deployed_sha": "b" * 40,
        "action_sha": "b" * 40, "action_run_id": "run",
        "persistence_run_id": "run", "reconciliation_run_id": "run",
        "release_receipt_ref": "release:authority",
    })
    return row


def test_release_requires_independent_qa_and_real_receipts():
    qa = independent_qa(case(), {"actor": "qa-independent", "head_sha": SHA,
                                "receipt_ref": "qa:sha", "decision": "PASS",
                                "negative_regression_pass": True})
    good = release_verification(case(), production_receipt=production_receipt(), qa_receipt=qa)
    assert good.state == "FIXED_AND_VERIFIED"
    held = release_verification(case(), production_receipt=production_receipt(),
                                qa_receipt=RoleReceipt("INDEPENDENT_QA", "HELD", "1841", "INDEPENDENT_QA"))
    assert held.state == "HELD"
    receipt = production_receipt()
    receipt["deployed_sha"] = SHA
    assert "EXACT_SHA_MISMATCH" in release_verification(case(), production_receipt=receipt,
                                                         qa_receipt=qa).reasons
    receipt = production_receipt()
    receipt["release_receipt_ref"] = None
    assert release_verification(case(), production_receipt=receipt, qa_receipt=qa).state == "HELD"


def manifest_case(**overrides):
    result = {"issue_number": 1841, "amrt_owned": True, "severity": "P0",
              "execution_lane": "RAPID", "change_class": "B",
              "lease_group": "P0_LLP_RESTORE",
              "conflict_keys": ["NFL_PUBLICATION", "FULL_SLATE_ACCEPTANCE"],
              "can_execute": False}
    result.update(overrides)
    return result


def test_manifest_dispatch_preflight_only_allows_admitted_p0():
    assert dispatch_preflight(manifest_case()).state == "READY"
    for updates in ({"amrt_owned": False}, {"severity": "P1"},
                    {"lease_group": "GLOBAL"}, {"change_class": "C"},
                    {"conflict_keys": []}, {"can_execute": True}):
        assert dispatch_preflight(manifest_case(**updates)).state == "HELD"
