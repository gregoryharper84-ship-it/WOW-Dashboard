from datetime import datetime, timezone

from v17.terminal_closure_controller import (
    BLOCKER_MARKER,
    TERMINAL_RECEIPT_HEADING,
    evaluate,
)


NOW = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
MERGE = "abc123merge"


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
    state["pr_comments"] = [{
        "body": (
            "## Release / Production Verification Agent\n\n"
            "~~~json\n"
            '{"status":"PRODUCTION_VERIFIED","production_sha":"prod456","main_sha":"main456",'
            '"acceptance":"PASS","reconciliation":"PASS","blocker":"","next_action":""}'
            "\n~~~"
        )
    }]
    state["runs"] = [
        {
            "id": 42,
            "name": "wow-v17-nightly-multiscout",
            "path": ".github/workflows/wow-v17-nightly-multiscout.yml",
            "head_sha": MERGE,
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
    assert "prod456" in result["receipt_markdown"]
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
