"""Adversarial read-only dispatch reconciliation tests; no GitHub/network writes."""
from dataclasses import replace
from v17.engineering_continuity_reconcile import (
    PendingIntent, RunReceipt, DispatchArtifact, evaluate_reconciliation,
)

SHA = "a" * 40
HEAD = "b" * 40
UUID = "12345678-1234-4234-8234-123456789abc"
REPO = "example/WOW-Dashboard"
INTENT = PendingIntent(823, UUID, SHA, "DISPATCH_NEW_REPAIR", None, None, 1001)
RUN = RunReceipt(
    1001, REPO, ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    "workflow_dispatch", "main", SHA, "completed", "success"
)
ART = DispatchArtifact(1001, UUID, 823, SHA, "DISPATCH_NEW_REPAIR", None, None)


class Evidence:
    def __init__(self, run=RUN, artifact=ART, fail=None):
        self.run = run
        self.artifact = artifact
        self.fail = fail
        self.calls = []

    def fetch_verified_run(self, run_id):
        self.calls.append(("run", run_id))
        if self.fail == "run":
            raise RuntimeError("untrusted token appears in exception")
        return self.run

    def fetch_verified_dispatch_artifact(self, run_id, intent_id):
        self.calls.append(("artifact", run_id, intent_id))
        if self.fail == "artifact":
            raise RuntimeError("untrusted token appears in exception")
        return self.artifact


def check(intent=INTENT, source=None, repository=REPO):
    src = source or Evidence()
    return evaluate_reconciliation(intent, src, repository=repository), src


def test_successful_exact_run_is_never_auto_released_or_dispatched():
    r, src = check()
    assert r.code == "RECONCILE_COMPLETED_NEEDS_INDEPENDENT_CLOSURE"
    assert r.candidate is True and not r.terminal and not r.may_redispatch
    assert src.calls == [("run", 1001), ("artifact", 1001, UUID)]


def test_completed_failed_run_needs_independent_review_not_auto_retry():
    for conclusion in ("failure", "cancelled", "timed_out"):
        r, _ = check(source=Evidence(run=replace(RUN, conclusion=conclusion)))
        assert r.code == "RECONCILE_FAILED_NEEDS_INDEPENDENT_REPAIR_REVIEW"
        assert r.candidate and not r.may_redispatch


def test_github_204_or_timeout_without_run_never_unlocks_row():
    for id_ in (None, 0, -9, True):
        r, src = check(intent=replace(INTENT, workflow_run_id=id_))
        assert r.code == "RECONCILE_RUN_NOT_CORRELATED"
        assert not src.calls and not r.may_redispatch


def test_correlated_run_but_no_artifact_cannot_unlock_row():
    r, src = check(source=Evidence(fail="artifact"))
    assert r.code == "RECONCILE_ARTIFACT_UNAVAILABLE"
    assert src.calls[-1][0] == "artifact"
    assert not r.candidate


def test_provider_errors_are_typed_and_never_leak_secret():
    r, src = check(source=Evidence(fail="run"))
    assert r.code == "RECONCILE_RUN_READBACK_UNAVAILABLE"
    assert "token" not in str(r)


def test_untrusted_run_provenance_holds():
    invalid = [
        replace(RUN, run_id=1002), replace(RUN, repository="other/repo"),
        replace(RUN, workflow_path=".github/workflows/attacker.yml"),
        replace(RUN, event="pull_request_target"),
        replace(RUN, head_branch="attacker"), replace(RUN, head_sha=HEAD),
    ]
    for item in invalid:
        r, src = check(source=Evidence(run=item))
        assert r.code == "RECONCILE_RUN_PROVENANCE_MISMATCH"
        assert all(call[0] != "artifact" for call in src.calls)


def test_pending_run_not_terminal_even_with_artifact_present():
    for status in ("in_progress", "queued", "pending"):
        r, src = check(source=Evidence(run=replace(RUN, status=status, conclusion=None)))
        assert r.code == "RECONCILE_RUN_NOT_TERMINAL"
        assert len(src.calls) == 1


def test_unknown_run_conclusion_fails_closed():
    for conclusion in (None, "skipped", "neutral", "action_required"):
        r, _ = check(source=Evidence(run=replace(RUN, conclusion=conclusion)))
        assert r.code == "RECONCILE_RUN_CONCLUSION_UNKNOWN"


def test_mismatched_or_forged_artifacts_hold_even_for_green_run():
    invalid = [
        replace(ART, workflow_run_id=1003), replace(ART, intent_id="aaaaaaaa-1234-4234-8234-123456789abc"),
        replace(ART, incident=824), replace(ART, main_sha=HEAD),
        replace(ART, action="REPAIR_SAME_BRANCH"), replace(ART, pr_number=99),
        replace(ART, pr_head_sha=HEAD),
    ]
    for item in invalid:
        r, _ = check(source=Evidence(artifact=item))
        assert r.code == "RECONCILE_ARTIFACT_IDENTITY_MISMATCH"


def test_valid_existing_pr_requires_same_exact_head_and_pr_number():
    p = replace(INTENT, action="REPAIR_SAME_BRANCH", pr_number=76, pr_head_sha=HEAD)
    artifact = replace(ART, action=p.action, pr_number=p.pr_number, pr_head_sha=HEAD)
    r, _ = check(intent=p, source=Evidence(artifact=artifact))
    assert r.candidate and not r.may_redispatch
    r, _ = check(intent=p, source=Evidence(artifact=replace(artifact, pr_head_sha=SHA)))
    assert r.code == "RECONCILE_ARTIFACT_IDENTITY_MISMATCH"


def test_missing_pr_head_or_invalid_intent_never_reads_provider():
    invalid = [
        replace(INTENT, incident=0), replace(INTENT, intent_id="abc"),
        replace(INTENT, main_sha="abc"), replace(INTENT, action="MERGE"),
        replace(INTENT, pr_number=9), replace(INTENT, action="REPAIR_SAME_BRANCH"),
        replace(INTENT, action="REPAIR_SAME_BRANCH", pr_number=9),
        replace(INTENT, action="REPAIR_SAME_BRANCH", pr_number=True, pr_head_sha=HEAD),
    ]
    for item in invalid:
        result, source = check(intent=item)
        assert not result.candidate and not source.calls


def test_bad_repository_never_reads_provider():
    result, source = check(repository="")
    assert result.code == "RECONCILE_REPOSITORY_INVALID"
    assert not source.calls


def test_nonmatching_run_object_type_not_accepted():
    source = Evidence(run={"status": "completed", "conclusion": "success"})
    r, _ = check(source=source)
    assert r.code == "RECONCILE_RUN_READBACK_INVALID"


def test_nonmatching_artifact_object_type_not_accepted():
    source = Evidence(artifact={"incident": 823, "intent_id": UUID})
    r, _ = check(source=source)
    assert r.code == "RECONCILE_ARTIFACT_INVALID"
