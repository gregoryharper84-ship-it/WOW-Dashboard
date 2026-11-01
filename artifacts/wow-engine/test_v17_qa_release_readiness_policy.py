"""#1944 source-only negative tests for exact-head readiness/re-entry."""
from dataclasses import replace

from v17.qa_release_readiness_policy import evaluate_exact_head, advise_reentry

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = "a" * 40
OTHER = "b" * 40
APP = 15368
REQUIRED = (
    ("WOW required-three regression", APP),
    ("WOW governed probability backend", APP),
    ("WOW additional required regression", APP),
    ("WOW V17 change impact gate", APP),
)


def pr():
    return {
        "number": 1940, "state": "open", "draft": False,
        "head": {"sha": SHA, "ref": "claude/fix", "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }


def check(name, *, status="completed", conclusion="success", app=APP, head=SHA, id=1):
    return {
        "name": name, "head_sha": head, "id": id,
        "app": {"id": app}, "status": status, "conclusion": conclusion,
    }


def checks():
    return [check(name, id=i + 1) for i, (name, _) in enumerate(REQUIRED)]


def decision(*, rows=None, identity=None, requirements=REQUIRED, complete=True):
    return evaluate_exact_head(
        repository=REPO, pr_number=1940, head_sha=SHA,
        pr=pr() if identity is None else identity,
        checks=checks() if rows is None else rows,
        requirements=requirements, inventory_complete=complete,
    )


def test_all_four_ensure_readiness_not_approval():
    result = decision()
    assert result.state == "READY_FOR_INDEPENDENT_REVIEW"
    assert result.evidence_ids == (1, 2, 3, 4)
    assert not result.can_execute and not result.can_publish_qa
    assert not result.can_publish_release and not result.can_merge and not result.can_deploy


def test_1940_ordering_does_not_turn_running_required_three_into_reject():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], status="in_progress", conclusion=None)
    prior = decision(rows=rows)
    assert prior.state == "WAIT_UPSTREAM"
    assert "WAIT_UPSTREAM:WOW required-three regression" in prior.reasons
    current = decision()
    plan = advise_reentry(previous=prior, current=current, role="qa", active_lease=False)
    assert plan.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert plan.dispatch_authorized is False
    assert plan.lease_key.endswith(":" + SHA + ":qa")


def test_missing_required_check_waits_without_false_rejection():
    assert decision(rows=checks()[1:]).state == "WAIT_UPSTREAM"


def test_failing_required_check_never_becomes_pending_or_green():
    rows = checks()
    rows[1] = check(REQUIRED[1][0], conclusion="failure", id=5)
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_FAILED:WOW governed probability backend:failure" in got.reasons


def test_older_failure_can_be_superseded_by_new_authentic_success():
    rows = checks() + [check(REQUIRED[0][0], conclusion="failure", id=5),
                       check(REQUIRED[0][0], conclusion="success", id=9)]
    assert decision(rows=rows).state == "READY_FOR_INDEPENDENT_REVIEW"
    assert 9 in decision(rows=rows).evidence_ids


def test_latest_failed_attempt_supersedes_stale_green():
    rows = checks() + [check(REQUIRED[0][0], conclusion="failure", id=20)]
    assert decision(rows=rows).state == "HOLD_CI_OR_EVIDENCE"


def test_spoofed_app_cannot_count_as_regression():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], app=5245832)
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_APP_MISMATCH:WOW required-three regression" in got.reasons


def test_stale_sha_check_not_a_current_pass():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], head=OTHER)
    assert decision(rows=rows).state == "WAIT_UPSTREAM"


def test_foreign_pr_head_rejected():
    identity = pr()
    identity["head"]["repo"]["full_name"] = "foreign/attacker"
    assert decision(identity=identity).state == "HOLD_IDENTITY"


def test_changed_head_rejected():
    identity = pr()
    identity["head"]["sha"] = OTHER
    assert decision(identity=identity).state == "HOLD_IDENTITY"


def test_draft_and_non_main_prs_rejected():
    identity = pr()
    identity["draft"] = True
    assert decision(identity=identity).state == "HOLD_IDENTITY"
    identity = pr()
    identity["base"]["ref"] = "develop"
    assert decision(identity=identity).state == "HOLD_IDENTITY"


def test_incomplete_inventory_cannot_be_ready():
    assert decision(complete=False).state == "HOLD_EVIDENCE"


def test_missing_or_duplicate_policy_requirements_hold():
    assert decision(requirements=()).state == "HOLD_EVIDENCE"
    assert decision(requirements=(REQUIRED[0], REQUIRED[0])).state == "HOLD_EVIDENCE"


def test_trust_root_governance_is_explicit_extra_requirement():
    special = REQUIRED + (("Trusted exact-head engineering governance", APP),)
    assert decision(requirements=special).state == "WAIT_UPSTREAM"
    rows = checks() + [check("Trusted exact-head engineering governance", id=20)]
    assert decision(rows=rows, requirements=special).state == "READY_FOR_INDEPENDENT_REVIEW"


def test_incomplete_check_provenance_holds():
    rows = checks()
    rows[0] = {**rows[0], "app": {}}
    assert decision(rows=rows).state == "HOLD_CI_OR_EVIDENCE"


def test_quota_does_not_mint_qa_pass_or_dispatch():
    prior = decision(rows=checks()[1:])
    got = advise_reentry(previous=prior, current=decision(), role="qa",
                         active_lease=False, provider_state="quota_exceeded")
    assert got.state == "WAIT"
    assert got.reason == "REVIEW_PROVIDER_UNAVAILABLE:quota_exceeded"
    assert not got.dispatch_authorized and not got.can_publish_check


def test_quota_recovery_produces_new_eligible_signal_without_check_publish():
    prior = decision(rows=checks()[1:])
    got = advise_reentry(previous=prior, current=decision(), role="qa",
                         active_lease=False, provider_state="available")
    assert got.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not got.can_publish_check


def test_active_lease_deduplicates_upstream_wakeup():
    result = advise_reentry(previous=None, current=decision(), role="qa", active_lease=True)
    assert result.state == "WAIT"


def test_no_repeated_dispatch_when_nothing_changed():
    ready = decision()
    got = advise_reentry(previous=ready, current=ready, role="qa", active_lease=False)
    assert got.state == "WAIT"
    assert got.reason == "NO_NEW_UPSTREAM_TRANSITION"


def test_release_requires_separately_verified_qa_receipt():
    got = advise_reentry(previous=None, current=decision(), role="release",
                         active_lease=False)
    assert got.state == "HOLD"
    assert got.reason == "QA_RECEIPT_NOT_INDEPENDENTLY_VERIFIED"
    got = advise_reentry(previous=None, current=decision(), role="release",
                         active_lease=False, qa_receipt_verified=True)
    assert got.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not got.dispatch_authorized


def test_stale_head_cannot_reuse_old_reentry():
    older = replace(decision(), head_sha=OTHER)
    got = advise_reentry(previous=older, current=decision(), role="qa", active_lease=False)
    assert got.state == "HOLD"
    assert got.reason == "HEAD_CHANGED_REVALIDATE"


def test_failed_evidence_or_unauthorized_provider_never_dispatches():
    got = advise_reentry(previous=None, current=decision(rows=[]), role="qa",
                         active_lease=False)
    assert got.state == "WAIT_OR_HOLD"
    got = advise_reentry(previous=None, current=decision(), role="qa",
                         active_lease=False, provider_state="unknown")
    assert got.state == "HOLD"


def test_no_role_impersonation_from_untrusted_caller():
    got = advise_reentry(previous=None, current=decision(), role="sirt", active_lease=False)
    assert got.state == "HOLD"


def test_terminal_role_success_stops_duplicate_dispatch():
    got = advise_reentry(previous=None, current=decision(), role="qa",
                         active_lease=False, already_completed=True)
    assert got.state == "NOOP"

def test_provider_quota_recovery_can_reenter_without_ci_state_change():
    ready = decision()
    first = advise_reentry(previous=ready, current=ready, role="qa",
                           active_lease=False, provider_state="quota_exceeded")
    assert first.state == "WAIT"
    later = advise_reentry(previous=ready, current=ready, role="qa",
                           active_lease=False, previous_provider_state="quota_exceeded",
                           provider_state="available")
    assert later.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not later.dispatch_authorized


def test_qa_artifact_arrival_can_reenter_release_after_ci_already_ready():
    ready = decision()
    before = advise_reentry(previous=ready, current=ready, role="release",
                            active_lease=False, qa_receipt_verified=False)
    assert before.state == "HOLD"
    after = advise_reentry(previous=ready, current=ready, role="release",
                           active_lease=False, qa_receipt_verified=True,
                           previous_qa_receipt_verified=False)
    assert after.state == "READY_FOR_EXTERNAL_DISPATCH"
    assert not after.can_publish_check

def test_caller_cannot_omit_mandatory_regression_or_change_impact():
    for omitted in range(len(REQUIRED)):
        selected = tuple(item for i, item in enumerate(REQUIRED) if i != omitted)
        result = decision(requirements=selected)
        assert result.state == "HOLD_EVIDENCE"
        assert result.reasons == ("MANDATORY_REQUIREMENTS_OMITTED",)


def test_caller_cannot_reassign_mandatory_check_to_wrong_app():
    selected = ((REQUIRED[0][0], 5245832),) + REQUIRED[1:]
    result = decision(requirements=selected)
    assert result.state == "HOLD_EVIDENCE"
    assert result.reasons == ("MANDATORY_REQUIREMENTS_OMITTED",)


def test_duplicate_replayed_check_row_with_identical_evidence_dedupes_safely():
    rows = checks()
    rows.append(check(REQUIRED[0][0], id=1))
    assert decision(rows=rows).state == "READY_FOR_INDEPENDENT_REVIEW"


def test_conflicting_duplicate_check_id_never_resolves_to_a_pass():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=1, conclusion="success")
    rows.append(check(REQUIRED[0][0], id=1, conclusion="failure"))
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:WOW required-three regression" in got.reasons


def test_conflicting_duplicate_check_id_holds_regardless_of_list_order():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=1, conclusion="failure")
    rows.append(check(REQUIRED[0][0], id=1, conclusion="success"))
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:WOW required-three regression" in got.reasons


def test_same_app_same_id_differing_status_also_collides():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=1, app=APP)
    rows.append(check(REQUIRED[0][0], id=1, app=APP, status="in_progress", conclusion=None))
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:WOW required-three regression" in got.reasons


def test_cross_app_same_id_collision_cannot_be_masked_by_app_filtering():
    # Same name/id/head_sha, but one row claims GitHub Actions (the mandatory
    # App) and the other claims a different App entirely. Check-run ids are
    # globally unique, so this can only be forged/corrupted evidence -- the
    # App-correct row must not be allowed to look authentic on its own just
    # because the wrong-App row would otherwise be filtered out first.
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=1, app=APP, conclusion="success")
    rows.append(check(REQUIRED[0][0], id=1, app=5245832, conclusion="success"))
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:WOW required-three regression" in got.reasons


def test_cross_app_same_id_collision_holds_regardless_of_row_order():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=1, app=5245832, conclusion="success")
    rows.append(check(REQUIRED[0][0], id=1, app=APP, conclusion="success"))
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:WOW required-three regression" in got.reasons


def test_cross_name_same_id_collision_holds_both_requirements():
    # GitHub check-run ids are unique across the whole repository, not scoped
    # to a single check name -- two different required checks claiming the
    # same id at this head is itself proof of corrupted/forged evidence.
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=99)
    rows[1] = check(REQUIRED[1][0], id=99)
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_ID_COLLISION:" + REQUIRED[0][0] in got.reasons
    assert "CHECK_ID_COLLISION:" + REQUIRED[1][0] in got.reasons


def test_distinct_ids_across_apps_remain_a_plain_mismatch_not_a_collision():
    rows = checks()
    rows[0] = check(REQUIRED[0][0], id=777, app=5245832)
    got = decision(rows=rows)
    assert got.state == "HOLD_CI_OR_EVIDENCE"
    assert "CHECK_APP_MISMATCH:WOW required-three regression" in got.reasons
    assert not any(reason.startswith("CHECK_ID_COLLISION") for reason in got.reasons)
