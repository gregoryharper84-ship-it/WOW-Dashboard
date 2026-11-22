"""Deterministic exact-head QA/Release readiness and re-entry regressions."""
from dataclasses import replace

from v17 import independent_release_checks
from v17.qa_release_readiness_policy import (
    CHANGE_IMPACT_CHECK,
    GITHUB_ACTIONS_APP_ID,
    MANDATORY_CHECKS,
    SourceIdentity,
    advise_reentry,
    evaluate_exact_head,
)

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = "a" * 40
OTHER_SHA = "b" * 40
PR_NUMBER = 1940
SOURCE = SourceIdentity(GITHUB_ACTIONS_APP_ID, "wow-v17-change-impact-gate", 12345, 1)
REQUIRED = MANDATORY_CHECKS


def pr():
    return {
        "number": PR_NUMBER,
        "state": "open",
        "draft": False,
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }


def check(name, *, status="completed", conclusion="success", app_id=GITHUB_ACTIONS_APP_ID, head=SHA, id=1):
    return {
        "id": id,
        "name": name,
        "head_sha": head,
        "status": status,
        "conclusion": conclusion,
        "app_id": app_id,
    }


def checks():
    return [check(name, id=i + 1) for i, (name, _) in enumerate(REQUIRED)]


def decision(*, rows=None, identity=None, requirements=REQUIRED, complete=True, source=SOURCE):
    return evaluate_exact_head(
        repository=REPO,
        pr_number=PR_NUMBER,
        head_sha=SHA,
        source=source,
        pr=pr() if identity is None else identity,
        checks=checks() if rows is None else rows,
        requirements=requirements,
        inventory_complete=complete,
    )


def test_required_checks_match_independent_release_policy_and_app():
    assert REQUIRED == tuple(
        (name, independent_release_checks.GITHUB_ACTIONS_APP_ID)
        for name in independent_release_checks.QA_EVIDENCE_CHECKS
    )
    assert CHANGE_IMPACT_CHECK in {name for name, _ in REQUIRED}


def test_all_required_contexts_are_ready_only_for_independent_review():
    result = decision()
    assert result.state == "READY_FOR_INDEPENDENT_REVIEW"
    assert result.evidence_ids == (1, 2, 3, 4)
    assert result.source == SOURCE
    assert not result.can_execute
    assert not result.can_publish_qa
    assert not result.can_publish_release
    assert not result.can_merge
    assert not result.can_deploy


def test_1940_running_prerequisite_waits_then_new_success_allows_one_candidate():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], status="in_progress", conclusion=None, id=1)
    waiting = decision(rows=rows)
    assert waiting.state == "WAIT_UPSTREAM"
    assert waiting.reasons == ("WAIT_UPSTREAM:" + REQUIRED[0][0],)

    ready = decision()
    reentry = advise_reentry(
        previous=waiting, current=ready, role="qa", active_lease=False,
        provider_state="available",
    )
    assert reentry.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not reentry.dispatch_authorized
    assert reentry.lease_key.endswith(f":{SHA}:qa")


def test_missing_required_check_waits_instead_of_becoming_qa_reject():
    result = decision(rows=checks()[1:])
    assert result.state == "WAIT_UPSTREAM"
    assert all(reason.startswith("WAIT_UPSTREAM:") for reason in result.reasons)


def test_failed_latest_required_check_holds_with_exact_context():
    rows = checks()
    rows[1] = check(REQUIRED[1][0], conclusion="failure", id=5)
    result = decision(rows=rows)
    assert result.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_FAILED:" + REQUIRED[1][0] + ":failure" in result.reasons


def test_newer_authentic_success_supersedes_old_failure_and_old_success():
    rows = checks() + [
        check(REQUIRED[0][0], conclusion="failure", id=5),
        check(REQUIRED[0][0], conclusion="success", id=9),
    ]
    result = decision(rows=rows)
    assert result.state == "READY_FOR_INDEPENDENT_REVIEW"
    assert 9 in result.evidence_ids


def test_newer_authentic_failure_supersedes_stale_success():
    rows = checks() + [check(REQUIRED[0][0], conclusion="failure", id=9)]
    assert decision(rows=rows).state == "HOLD_CI_OR_EVIDENCE"


def test_wrong_app_cannot_satisfy_required_context():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], app_id=5245832)
    result = decision(rows=rows)
    assert result.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_APP_MISMATCH:" + REQUIRED[0][0] in result.reasons


def test_wrong_source_run_app_is_held():
    source = replace(SOURCE, app_id=5245832)
    result = decision(source=source)
    assert result.state == "HOLD_EVIDENCE"
    assert result.reasons == ("SOURCE_RUN_IDENTITY_INVALID",)


def test_missing_or_malformed_source_run_identity_is_held():
    for source in (
        replace(SOURCE, run_id=0),
        replace(SOURCE, run_attempt=0),
        replace(SOURCE, workflow=" "),
    ):
        assert decision(source=source).state == "HOLD_EVIDENCE"


def test_stale_sha_check_does_not_count_as_current_success():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], head=OTHER_SHA)
    assert decision(rows=rows).state == "WAIT_UPSTREAM"


def test_foreign_pr_head_and_fork_identity_are_held():
    identity = pr()
    identity["head"]["repo"]["full_name"] = "foreign/fork"
    assert decision(identity=identity).state == "HOLD_IDENTITY"
    identity = pr()
    identity["head"]["sha"] = OTHER_SHA
    assert decision(identity=identity).state == "HOLD_IDENTITY"


def test_draft_wrong_base_or_mismatched_pr_is_held():
    identity = pr()
    identity["draft"] = True
    assert decision(identity=identity).state == "HOLD_IDENTITY"
    identity = pr()
    identity["base"]["ref"] = "develop"
    assert decision(identity=identity).state == "HOLD_IDENTITY"
    identity = pr()
    identity["number"] = PR_NUMBER + 1
    assert decision(identity=identity).state == "HOLD_IDENTITY"


def test_incomplete_check_inventory_cannot_be_ready():
    assert decision(complete=False).state == "HOLD_EVIDENCE"


def test_callers_cannot_omit_or_rebind_mandatory_contexts():
    for omitted in range(len(REQUIRED)):
        requirements = tuple(row for index, row in enumerate(REQUIRED) if index != omitted)
        result = decision(requirements=requirements)
        assert result.state == "HOLD_EVIDENCE"
        assert result.reasons == ("MANDATORY_REQUIREMENTS_OMITTED",)
    rebound = ((REQUIRED[0][0], 5245832), *REQUIRED[1:])
    assert decision(requirements=rebound).reasons == ("MANDATORY_REQUIREMENTS_OMITTED",)


def test_duplicate_identical_check_run_is_collapsed_safely():
    rows = checks()
    rows.append(dict(rows[0]))
    assert decision(rows=rows).state == "READY_FOR_INDEPENDENT_REVIEW"


def test_conflicting_duplicate_id_holds_both_orders():
    for rows in (
        checks()[:1] + [check(REQUIRED[0][0], conclusion="failure", id=1)] + checks()[1:],
        [check(REQUIRED[0][0], conclusion="failure", id=1)] + checks(),
    ):
        result = decision(rows=rows)
        assert result.state == "HOLD_CI_OR_EVIDENCE"
        assert "CHECK_ID_COLLISION:" + REQUIRED[0][0] in result.reasons


def test_same_id_cross_app_collision_is_not_hidden_by_app_filter():
    rows = checks()
    rows.append(check(REQUIRED[0][0], app_id=5245832, id=1))
    result = decision(rows=rows)
    assert result.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:" + REQUIRED[0][0] in result.reasons


def test_same_id_cross_name_collision_holds_each_required_context():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=99)
    rows[1] = check(REQUIRED[1][0], id=99)
    result = decision(rows=rows)
    assert result.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:" + REQUIRED[0][0] in result.reasons
    assert "CHECK_ID_COLLISION:" + REQUIRED[1][0] in result.reasons


def test_distinct_wrong_app_run_is_mismatch_not_id_collision():
    rows = checks()
    rows.append(check(REQUIRED[0][0], app_id=5245832, id=99))
    result = decision(rows=rows)
    assert result.state == "READY_FOR_INDEPENDENT_REVIEW"
    assert not any(reason.startswith("CHECK_ID_COLLISION:") for reason in result.reasons)


def test_malformed_check_provenance_holds():
    rows = checks()
    rows[0] = {**rows[0], "app_id": None}
    result = decision(rows=rows)
    assert result.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_PROVENANCE_INVALID:" + REQUIRED[0][0] in result.reasons


def test_conflicting_nested_and_normalized_app_id_holds():
    rows = checks()
    rows[0] = {
        **rows[0],
        "app_id": GITHUB_ACTIONS_APP_ID,
        "app": {"id": 5245832},
    }
    result = decision(rows=rows)
    assert result.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_PROVENANCE_INVALID:" + REQUIRED[0][0] in result.reasons


def test_quota_outage_never_yields_dispatch_candidate_then_recovery_reenters_once():
    ready = decision()
    outage = advise_reentry(
        previous=ready, current=ready, role="qa", active_lease=False,
        provider_state="quota_exceeded",
    )
    assert outage.state == "WAIT"
    assert outage.reason == "REVIEW_PROVIDER_UNAVAILABLE:quota_exceeded"
    recovered = advise_reentry(
        previous=ready, current=ready, role="qa", active_lease=False,
        previous_provider_state="quota_exceeded", provider_state="available",
    )
    assert recovered.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not recovered.dispatch_authorized
    assert not recovered.can_publish_check
    assert not recovered.can_execute


def test_missing_or_unknown_review_credential_fails_closed():
    ready = decision()
    missing = advise_reentry(
        previous=None, current=ready, role="qa", active_lease=False,
    )
    assert missing.state == "HOLD"
    assert missing.reason == "REVIEW_PROVIDER_IDENTITY_OR_STATE_INVALID"


def test_release_requires_independently_verified_qa_receipt_and_reenters_once():
    ready = decision()
    missing = advise_reentry(
        previous=None, current=ready, role="release", active_lease=False,
    )
    assert missing.state == "HOLD"
    assert missing.reason == "QA_RECEIPT_NOT_INDEPENDENTLY_VERIFIED"
    recovered = advise_reentry(
        previous=ready, current=ready, role="release", active_lease=False,
        qa_receipt_verified=True, previous_qa_receipt_verified=False,
    )
    assert recovered.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not recovered.dispatch_authorized
    assert not recovered.can_publish_check


def test_active_lease_and_unchanged_ready_state_do_not_repeat_dispatch():
    ready = decision()
    assert advise_reentry(
        previous=None, current=ready, role="qa", active_lease=True,
        provider_state="available",
    ).state == "WAIT"
    unchanged = advise_reentry(
        previous=ready, current=ready, role="qa", active_lease=False,
        provider_state="available",
    )
    assert unchanged.state == "WAIT"
    assert unchanged.reason == "NO_NEW_UPSTREAM_TRANSITION"


def test_stale_head_cannot_reuse_previous_readiness_or_receipt():
    old = replace(decision(), head_sha=OTHER_SHA)
    result = advise_reentry(
        previous=old, current=decision(), role="qa", active_lease=False,
        provider_state="available",
    )
    assert result.state == "HOLD"
    assert result.reason == "HEAD_CHANGED_REVALIDATE"


def test_invalid_role_and_completed_role_do_not_dispatch():
    ready = decision()
    assert advise_reentry(
        previous=None, current=ready, role="sirt", active_lease=False,
    ).state == "HOLD"
    completed = advise_reentry(
        previous=None, current=ready, role="qa", active_lease=False,
        already_completed=True, provider_state="available",
    )
    assert completed.state == "NOOP"
