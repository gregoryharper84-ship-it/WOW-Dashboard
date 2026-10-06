from datetime import datetime, timezone

from v17.terminal_closure_controller import (
    BLOCKER_MARKER,
    TERMINAL_RECEIPT_HEADING,
    evaluate,
)


NOW = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
MERGE = "abc123merge"
TRUSTED_USER = {"login": "github-actions[bot]"}


def _release_comment(*, production_sha: str = MERGE, author: str = "github-actions[bot]"):
    return {
        "user": {"login": author},
        "body": (
            "## Release / Production Verification Agent\n\n"
            f"- governed_pr_head_sha: `head123`\n"
            f"- protected_main_merge_sha: `{MERGE}`\n\n"
            "~~~json\n"
            f'{{"status":"PRODUCTION_VERIFIED","production_sha":"{production_sha}",'
            f'"main_sha":"{MERGE}","acceptance":"PASS","reconciliation":"PASS",'
            '"blocker":"","next_action":""}'
            "\n~~~"
        ),
    }


def _base_state():
    return {
        "pr": {
            "number": 1285,
            "body": (
                "Terminal-Closure-Autonomous: true\n"
                "Terminal-Issue: #1250\n"
                "Morning-Green-Acceptance-Workflow: wow-v17-nightly-multiscout.yml\n"
            ),
            "merge_commit_sha": MERGE,
            "merged_at": "2026-10-04T01:30:00Z",
        },
        "pr_comments": [],
        "issue_comments": [],
        "runs": [],
        "current_main_sha": MERGE,
    }


def test_ready_for_receipt_requires_release_and_exact_merge_acceptance():
    state = _base_state()
    state["pr_comments"] = [_release_comment()]
    state["runs"] = [
        {
            "id": 42,
            "name": "wow-v17-nightly-multiscout",
            "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
            "head_sha": MERGE,
            "event": "workflow_run",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://example/acceptance",
        },
        {
            "id": 43,
            "name": "wow-v17-release-production-verification-agent",
            "path": ".github/workflows/wow-v17-release-production-verification-agent.yml",
            "head_sha": MERGE,
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://example/release",
            "updated_at": "2026-10-04T01:55:00Z",
        },
    ]

    result = evaluate(state, now=NOW)

    assert result["status"] == "READY_FOR_RECEIPT"
    assert TERMINAL_RECEIPT_HEADING in result["receipt_markdown"]
    assert MERGE in result["receipt_markdown"]
    assert "42" in result["receipt_markdown"]
    assert "FIXED_AND_VERIFIED" in result["receipt_markdown"]
    assert result["can_execute"] is False


def test_missing_receipts_dispatches_bounded_verification_while_merge_is_current_main():
    state = _base_state()

    result = evaluate(state, now=NOW)

    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
    assert result["dispatch_release_verification"] is True
    assert result["dispatch_acceptance"] is True
    assert set(result["blockers"]) == {
        "PRODUCTION_VERIFICATION_RECEIPT_MISSING",
        "EXACT_MERGE_ACCEPTANCE_RUN_MISSING",
    }
    assert result["can_execute"] is False


def test_two_hour_debt_escalates_and_does_not_fake_exact_acceptance_after_main_moves():
    state = _base_state()
    state["pr"]["merged_at"] = "2026-10-03T23:30:00Z"
    state["current_main_sha"] = "newer-main"

    result = evaluate(state, now=NOW)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["dispatch_acceptance"] is False
    assert BLOCKER_MARKER in result["blocker_markdown"]
    assert "EXACT_MERGE_ACCEPTANCE_RUN_MISSING" in result["blocker_markdown"]


def test_existing_issue_receipt_is_terminal_and_repeat_safe():
    state = _base_state()
    state["issue_comments"] = [{
        "user": TRUSTED_USER,
        "body": (
            f"{TERMINAL_RECEIPT_HEADING}\n"
            "- **Commit SHA:** `abc123merge`\n"
            "- **Status:** **FIXED_AND_VERIFIED**"
        )
    }]

    result = evaluate(state, now=NOW)

    assert result["status"] == "FIXED_AND_VERIFIED"
    assert result["reason"] == "TERMINAL_RECEIPT_ALREADY_PRESENT"
    assert result["can_execute"] is False


def test_controller_ignores_pr_without_explicit_autonomous_opt_in():
    state = _base_state()
    state["pr"]["body"] = "Terminal-Issue: #1250\n"

    result = evaluate(state, now=NOW)

    assert result["status"] == "SKIP"
    assert result["reason"] == "TERMINAL_CLOSURE_NOT_OPTED_IN"


def test_worker_morning_green_marker_is_explicit_terminal_opt_in():
    state = _base_state()
    state["pr"]["body"] = (
        "Incident: `1250`\n"
        "Morning-Green-Autonomous: true\n"
        "Morning-Green-Risk: R1\n"
        "Morning-Green-Acceptance-Workflow: wow-v17-nightly-engineering-scan.yml\n"
    )

    result = evaluate(state, now=NOW)

    assert result["autonomous"] is True
    assert result["issue_number"] == 1250
    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
    assert result["can_execute"] is False


def test_worker_incident_marker_without_autonomous_marker_still_skips():
    state = _base_state()
    state["pr"]["body"] = "Incident: `1250`\n"

    result = evaluate(state, now=NOW)

    assert result["status"] == "SKIP"
    assert result["issue_number"] == 1250


def test_merge_identity_incomplete_is_exact_blocker():
    state = _base_state()
    state["pr"]["merge_commit_sha"] = ""
    state["pr"]["merged_at"] = "not-a-date"

    result = evaluate(state, now=NOW)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["blockers"] == ["MERGE_IDENTITY_INCOMPLETE"]
    assert result["can_execute"] is False


def test_fallback_issue_and_no_acceptance_workflow_close_from_release_only():
    state = _base_state()
    state["pr"]["body"] = (
        "Terminal-Closure-Autonomous: true\n"
        "Fixes #1250\n"
    )
    state["pr_comments"] = [{"body": "not a release comment"}, _release_comment()]

    result = evaluate(state, now=NOW)

    assert result["issue_number"] == 1250
    assert result["acceptance_workflow"] == "none"
    assert result["status"] == "READY_FOR_RECEIPT"
    assert MERGE in result["receipt_markdown"]
    assert "Canary / Acceptance Run ID:** `N/A`" in result["receipt_markdown"]


def test_inflight_release_and_acceptance_are_not_redispatched():
    state = _base_state()
    state["runs"] = [
        {
            "name": "wow-v17-release-production-verification-agent",
            "path": ".github/workflows/wow-v17-release-production-verification-agent.yml",
            "head_sha": MERGE,
            "status": "in_progress",
            "conclusion": None,
            "created_at": "2026-10-04T01:58:00Z",
        },
        {
            "workflow_name": "wow-v17-nightly-multiscout",
            "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
            "head_sha": MERGE,
            "status": "queued",
            "conclusion": None,
        },
    ]

    result = evaluate(state, now=NOW)

    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
    assert result["dispatch_release_verification"] is False
    assert result["dispatch_acceptance"] is False


def test_recent_failed_release_observes_cooldown_before_redispatch():
    state = _base_state()
    state["runs"] = [{
        "name": "wow-v17-release-production-verification-agent",
        "path": ".github/workflows/wow-v17-release-production-verification-agent.yml",
        "head_sha": MERGE,
        "status": "completed",
        "conclusion": "failure",
        "updated_at": "2026-10-04T01:58:00Z",
    }]

    result = evaluate(state, now=NOW)

    assert result["dispatch_release_verification"] is False
    assert result["dispatch_acceptance"] is True


def test_stale_release_failure_can_be_redispatched():
    state = _base_state()
    state["runs"] = [{
        "name": "wow-v17-release-production-verification-agent",
        "path": ".github/workflows/wow-v17-release-production-verification-agent.yml",
        "head_sha": MERGE,
        "status": "completed",
        "conclusion": "failure",
        "created_at": "2026-10-04T01:40:00",
    }]

    result = evaluate(state, now=NOW)

    assert result["dispatch_release_verification"] is True


def test_acceptance_must_match_exact_merge_sha_and_success():
    state = _base_state()
    state["runs"] = [
        {
            "name": "wow-v17-nightly-multiscout",
            "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
            "head_sha": "different-sha",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "name": "wow-v17-nightly-multiscout",
            "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
            "head_sha": MERGE,
            "status": "completed",
            "conclusion": "failure",
        },
    ]

    result = evaluate(state, now=NOW)

    assert "EXACT_MERGE_ACCEPTANCE_RUN_MISSING" in result["blockers"]
    assert result["dispatch_acceptance"] is True


def test_pr_receipt_is_also_repeat_safe_and_non_dict_comments_are_ignored():
    state = _base_state()
    state["pr_comments"] = [
        "not-a-comment-object",
        {
            "user": TRUSTED_USER,
            "body": (
                f"{TERMINAL_RECEIPT_HEADING}\n"
                f"- **Commit SHA:** `{MERGE}`\n"
                "- **Status:** **FIXED_AND_VERIFIED**"
            ),
        },
    ]

    result = evaluate(state, now=NOW)

    assert result["status"] == "FIXED_AND_VERIFIED"


def test_multiscout_push_contract_run_cannot_satisfy_terminal_acceptance():
    state = _base_state()
    state["pr_comments"] = [_release_comment()]
    state["runs"] = [{
        "id": 587,
        "name": "wow-v17-nightly-multiscout",
        "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
        "head_sha": MERGE,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://example/contract-only",
    }]

    result = evaluate(state, now=NOW)

    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
    assert "EXACT_MERGE_ACCEPTANCE_RUN_MISSING" in result["blockers"]
    assert result["can_execute"] is False


def test_multiscout_workflow_run_can_satisfy_terminal_acceptance():
    state = _base_state()
    state["pr_comments"] = [_release_comment()]
    state["runs"] = [{
        "id": 588,
        "name": "wow-v17-nightly-multiscout",
        "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
        "head_sha": MERGE,
        "event": "workflow_run",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://example/live-acceptance",
    }]

    result = evaluate(state, now=NOW)

    assert result["status"] == "READY_FOR_RECEIPT"
    assert "588" in result["receipt_markdown"]


def test_release_verification_for_different_production_sha_is_rejected():
    state = _base_state()
    state["pr"]["body"] = "Terminal-Closure-Autonomous: true\nFixes #1250\n"
    state["pr_comments"] = [_release_comment(production_sha="stale-merge")]

    result = evaluate(state, now=NOW)

    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
    assert "PRODUCTION_VERIFICATION_RECEIPT_MISSING" in result["blockers"]


def test_release_verification_from_untrusted_comment_author_is_rejected():
    state = _base_state()
    state["pr"]["body"] = "Terminal-Closure-Autonomous: true\nFixes #1250\n"
    state["pr_comments"] = [_release_comment(author="untrusted-user")]

    result = evaluate(state, now=NOW)

    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
    assert "PRODUCTION_VERIFICATION_RECEIPT_MISSING" in result["blockers"]


def test_untrusted_terminal_receipt_cannot_short_circuit_closure():
    state = _base_state()
    state["issue_comments"] = [{
        "user": {"login": "untrusted-user"},
        "body": (
            f"{TERMINAL_RECEIPT_HEADING}\n"
            f"- **Commit SHA:** `{MERGE}`\n"
            "- **Status:** **FIXED_AND_VERIFIED**"
        ),
    }]

    result = evaluate(state, now=NOW)

    assert result["status"] == "WAITING_FOR_TERMINAL_RECEIPT"
