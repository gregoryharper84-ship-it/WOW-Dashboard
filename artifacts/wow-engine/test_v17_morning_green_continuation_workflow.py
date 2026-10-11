from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-morning-green-continuation.yml"


def _text() -> str:
    return WORKFLOW.read_text()


def test_continuation_wakes_on_both_protected_ci_workflows():
    text = _text()
    assert 'workflows: ["wow-verify", "wow-engine-verify"]' in text
    assert "types: [completed]" in text


def test_autonomous_marker_and_risk_are_required_before_merge():
    text = _text()
    assert "Morning-Green-Autonomous: true" in text
    assert "Morning-Green-Risk:" in text
    assert "R0|R1|R2-restorative" in text
    assert 'head_repo" != "$GITHUB_REPOSITORY"' in text
    assert 'base_ref" != "main"' in text


def test_exact_three_protected_checks_are_reverified():
    text = _text()
    assert "WOW governed probability backend" in text
    assert "WOW required-three regression" in text
    assert "WOW additional required regression" in text
    assert 'state="RELEASE_AUTHORIZATION_HOLD"' in text
    assert 'ready=true' in text


def test_merge_is_head_sha_pinned_and_nonterminal_state_is_deferred():
    text = _text()
    assert "DIRECT_MERGE_HOLD_INDEPENDENT_AUTHORIZATION_REQUIRED" in text
    assert "gh pr merge" not in text
    assert 'state="CI_REWORK"' in text
    assert 'state="CI_WAIT"' in text
    assert 'state="SCOPE_REWORK"' in text
    assert "Preserve nonterminal Morning-Green state without poisoning main deploy checks" in text
    assert "durable closure evidence has been persisted" in text
    assert 'echo "deferred=true"' in text
    assert "successful workflow termination is forbidden" not in text
    assert 'echo "approved=false"' in text
    assert 'echo "approved=true"' in text
    assert "steps.gates.outputs.ready == 'true' && steps.scope.outputs.approved == 'true'" in text


def test_scope_rejection_is_machine_readable_not_a_failed_workflow_step():
    text = _text()
    assert "Morning-Green autonomous merge denied by diff-scope guard; preserving SCOPE_REWORK without failing protected main." in text
    assert 'echo "guard_exit_code=$rc"' in text
    assert 'exit "$rc"' not in text
    assert "SCOPE_APPROVED: ${{ steps.scope.outputs.approved }}" in text


def test_morning_green_preflight_cannot_merge_or_deploy():
    text = _text()
    assert "Hold protected Morning-Green repair for independent release" in text
    assert "DIRECT_MERGE_HOLD_INDEPENDENT_AUTHORIZATION_REQUIRED" in text
    assert "steps.release_hold.outputs.release_hold" in text
    assert "gh pr merge" not in text
    assert "gh workflow run wow-engine-verify.yml" not in text
    assert "gh workflow run wow-verify.yml" not in text
    assert "gh workflow run wow-v17-render-production-deploy.yml" not in text
    assert "actions: write" not in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text


def test_acceptance_workflow_whitelist_is_still_read_only():
    text = _text()
    assert "Morning-Green-Acceptance-Workflow:" in text
    assert "wow-v17-nightly-multiscout.yml" in text
    assert "wow-v17-nightly-engineering-scan.yml" in text
    assert 'case "${acceptance:-none}" in' in text
    assert 'gh workflow run "$ACCEPTANCE_WORKFLOW"' not in text


def test_machine_readable_closure_state_is_persisted():
    text = _text()
    assert "Persist machine-readable closure state" in text
    assert "closure-state.json" in text
    assert "failed_checks" in text
    assert "pending_checks" in text
    assert "can_execute:false" in text


def test_governance_invariant_remains_research_only():
    text = _text()
    assert "can_execute: false" in text
    assert "contents: read" in text
    assert "pull-requests: read" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
