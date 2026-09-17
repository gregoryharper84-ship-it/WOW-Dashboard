from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-morning-green-continuation.yml"


def _text() -> str:
    return WORKFLOW.read_text()


def test_continuation_wakes_on_both_protected_ci_workflows_and_rescans():
    text = _text()
    assert 'workflows: ["wow-verify", "wow-engine-verify"]' in text
    assert "types: [completed]" in text
    assert 'cron: "*/15 * * * *"' in text
    assert "github.event_name == 'schedule'" in text


def test_autonomous_marker_and_risk_are_required_before_merge():
    text = _text()
    assert "Morning-Green-Autonomous: true" in text
    assert "Morning-Green-Risk:" in text
    assert "R0|R1|R2-restorative" in text
    assert 'head_repo" != "$GITHUB_REPOSITORY"' in text
    assert 'base_ref" != "main"' in text


def test_scheduled_rescan_discovers_authorized_open_prs():
    text = _text()
    assert 'contains("Morning-Green-Autonomous: true")' in text
    assert "No eligible open same-repository Morning-Green PR found" in text
    assert "sort_by(.created_at)" in text


def test_exact_three_protected_checks_are_reverified():
    text = _text()
    assert "WOW governed probability backend" in text
    assert "WOW required-three regression" in text
    assert "WOW additional required regression" in text
    assert "All three protected checks are successful on exact head SHA" in text


def test_failed_ci_is_retried_once_then_marked_for_rework():
    text = _text()
    assert "Retry failed protected CI once" in text
    assert "Morning-Green-CI-Retry:" in text
    assert "/actions/jobs/${job_id}/rerun" in text
    assert "Morning-Green-Rework-Required:" in text
    assert "CI_FAILED_REWORK_REQUIRED" in text
    assert "Automatically retried" in text


def test_merge_is_head_sha_pinned_and_failure_does_not_merge():
    text = _text()
    assert '--match-head-commit "$HEAD_SHA"' in text
    assert "At least one protected check failed" in text
    assert "no merge attempted" in text
    assert "scheduled rescan will resume this repair" in text


def test_bot_merge_explicitly_resumes_main_required_checks():
    text = _text()
    assert "actions: write" in text
    assert "gh workflow run wow-engine-verify.yml" in text
    assert "gh workflow run wow-verify.yml" in text
    assert "GITHUB_TOKEN-authored merges do not recursively trigger normal push workflows" in text


def test_acceptance_workflow_is_explicit_and_whitelisted():
    text = _text()
    assert "Morning-Green-Acceptance-Workflow:" in text
    assert "wow-v17-nightly-multiscout.yml" in text
    assert "wow-v17-nightly-engineering-scan.yml" in text
    assert "requested non-whitelisted acceptance workflow" in text
    assert 'gh workflow run "$ACCEPTANCE_WORKFLOW"' in text


def test_governance_invariant_remains_research_only():
    text = _text()
    assert "can_execute: false" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
