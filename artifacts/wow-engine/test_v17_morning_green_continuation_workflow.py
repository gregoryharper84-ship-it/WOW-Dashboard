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
    assert 'state="MERGING"' in text
    assert 'ready=true' in text


def test_merge_is_head_sha_pinned_and_nonterminal_state_stays_fail_closed_without_poisoning_default_branch():
    text = _text()
    assert '--match-head-commit "$HEAD_SHA"' in text
    assert 'state="CI_REWORK"' in text
    assert 'state="CI_WAIT"' in text
    assert 'state="SCOPE_REWORK"' in text
    assert "continue-on-error: true" in text
    assert "Record nonterminal incident state" in text
    assert "autonomous merge remains blocked" in text
    assert "successful workflow termination is forbidden" not in text


def test_bot_merge_explicitly_resumes_main_required_checks():
    text = _text()
    assert "actions: write" in text
    assert "gh workflow run wow-engine-verify.yml" in text
    assert "gh workflow run wow-verify.yml" in text
    assert "gh workflow run wow-v17-render-production-deploy.yml" in text
    assert "steps.merge.outcome == 'success'" in text


def test_acceptance_workflow_is_explicit_and_whitelisted():
    text = _text()
    assert "Morning-Green-Acceptance-Workflow:" in text
    assert "wow-v17-nightly-multiscout.yml" in text
    assert "wow-v17-nightly-engineering-scan.yml" in text
    assert 'case "${acceptance:-none}" in' in text
    assert 'gh workflow run "$ACCEPTANCE_WORKFLOW"' in text


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
    assert "contents: write" in text
    assert "pull-requests: write" in text
