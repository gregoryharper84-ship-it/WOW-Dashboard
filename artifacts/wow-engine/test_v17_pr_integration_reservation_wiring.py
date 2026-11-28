"""Tests for the dispatcher-facing evidence, receipt and CLI layer.

The selector only becomes real if GitHub evidence normalises predictably and the
receipt cannot grant more than a reservation. These tests pin both.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from v17.pr_integration_reservation import (
    github_pull_record,
    main,
    render_integration_receipt,
    summarize_status_rollup,
)

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def gh_pull(**overrides):
    record = {
        "number": 2666,
        "headRefOid": SHA,
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": "APPROVED",
        "mergeStateStatus": "BEHIND",
        "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
        "createdAt": "2026-11-26T12:00:00Z",
        "behind_by": 12,
        "author": {"login": "wow-engineering[bot]"},
    }
    record.update(overrides)
    return record


def test_rollup_reduces_to_one_attested_status():
    assert summarize_status_rollup([]) == "MISSING"
    assert summarize_status_rollup(None) == "MISSING"
    assert summarize_status_rollup([{"status": "COMPLETED", "conclusion": "SUCCESS"}]) == "SUCCESS"
    assert summarize_status_rollup([{"status": "COMPLETED", "conclusion": "NEUTRAL"}]) == "SUCCESS"
    assert summarize_status_rollup([{"status": "COMPLETED", "conclusion": "FAILURE"}]) == "FAILURE"
    assert summarize_status_rollup([{"status": "COMPLETED", "conclusion": "TIMED_OUT"}]) == "FAILURE"
    assert summarize_status_rollup([{"status": "IN_PROGRESS", "conclusion": None}]) == "PENDING"
    assert summarize_status_rollup([{"state": "ERROR"}]) == "FAILURE"
    assert summarize_status_rollup([{"state": "PENDING"}]) == "PENDING"
    assert summarize_status_rollup([{}]) == "UNATTESTED"
    assert summarize_status_rollup(["SUCCESS"]) == "UNATTESTED"


def test_github_row_normalises_identity_review_age_and_checks():
    record = github_pull_record(gh_pull(), now=NOW, behind_by=12)
    assert record["number"] == 2666
    assert record["head_sha"] == SHA
    assert record["state"] == "open"
    assert record["draft"] is False
    assert record["review_state"] == "APPROVED"
    assert record["checks"] == {"status": "SUCCESS", "sha": SHA}
    assert record["behind_by"] == 12
    assert record["age_hours"] == 48.0
    # Acceptance is never inferred from a green rollup.
    assert record["accepted_head_sha"] is None


def test_review_required_and_draft_flags_are_preserved():
    record = github_pull_record(
        gh_pull(reviewDecision="REVIEW_REQUIRED", isDraft=True), now=NOW, behind_by=0
    )
    assert record["review_state"] == "PENDING"
    assert record["draft"] is True


def test_unknown_base_comparison_stays_unattested():
    record = github_pull_record(gh_pull(), now=NOW, behind_by=None)
    assert record["behind_by"] is None
    assert record["age_hours"] == 48.0


def test_receipt_in_report_mode_never_dispatches_a_repair():
    plan = {
        "decision": "RESERVATION_GRANTED",
        "reservation": {"pull_number": 2666, "head_sha": SHA},
        "selected_pull_number": 2666,
        "requires_code_repair": True,
        "requires_refresh": False,
        "requires_fresh_acceptance": True,
        "starvation_risk": [2859],
        "waiting": [2891],
        "blockers": [],
    }
    receipt = render_integration_receipt(plan, mode="report", generated_at=NOW, provider="openai")
    assert receipt["status"] == "RESERVED"
    assert receipt["dispatch_repair"] is False
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert receipt["starvation_risk"] == [2859]


def test_dispatch_mode_targets_the_reserved_head_only_for_writing_providers():
    plan = {
        "decision": "RESERVATION_GRANTED",
        "reservation": {"pull_number": 2666, "head_sha": SHA},
        "selected_pull_number": 2666,
        "requires_code_repair": True,
        "requires_refresh": False,
        "requires_fresh_acceptance": True,
    }
    assert render_integration_receipt(
        plan, mode="dispatch", generated_at=NOW, provider="openai"
    )["dispatch_repair"] is True
    assert render_integration_receipt(
        plan, mode="dispatch", generated_at=NOW, provider="anthropic"
    )["dispatch_repair"] is True
    for provider in ("gemini", "cursor", "deterministic", "none"):
        assert render_integration_receipt(
            plan, mode="dispatch", generated_at=NOW, provider=provider
        )["dispatch_repair"] is False


def test_dispatch_mode_does_not_spend_on_work_that_needs_nothing():
    plan = {
        "decision": "RESERVATION_GRANTED",
        "reservation": {"pull_number": 2900, "head_sha": SHA},
        "selected_pull_number": 2900,
        "requires_code_repair": False,
        "requires_refresh": False,
        "requires_fresh_acceptance": False,
    }
    receipt = render_integration_receipt(plan, mode="dispatch", generated_at=NOW, provider="openai")
    assert receipt["dispatch_repair"] is False


def test_dispatch_mode_never_pays_a_worker_for_a_mechanical_refresh():
    """wow-v17-engineering-pr-keep-current already updates behind-main PRs."""
    plan = {
        "decision": "RESERVATION_GRANTED",
        "reservation": {"pull_number": 2859, "head_sha": SHA},
        "selected_pull_number": 2859,
        "requires_code_repair": False,
        "requires_refresh": True,
        "requires_fresh_acceptance": True,
        "refresh_owner": "WOW_V17_ENGINEERING_PR_KEEP_CURRENT",
        "refresh_owner_reason": "KEEP_CURRENT_SELECTS_THIS_PULL",
    }
    receipt = render_integration_receipt(plan, mode="dispatch", generated_at=NOW, provider="openai")
    assert receipt["dispatch_repair"] is False
    assert receipt["refresh_owner"] == "WOW_V17_ENGINEERING_PR_KEEP_CURRENT"
    assert receipt["requires_refresh"] is True


def test_receipt_passes_through_the_plans_owner_decision_verbatim():
    """The receipt must not invent an owner the plan refused to claim."""
    plan = {
        "decision": "RESERVATION_GRANTED",
        "reservation": {"pull_number": 2666, "head_sha": SHA},
        "selected_pull_number": 2666,
        "requires_code_repair": False,
        "requires_refresh": True,
        "requires_fresh_acceptance": True,
        "refresh_owner": None,
        "refresh_owner_reason": "NO_AUTOMATIC_REFRESH_OWNER_FOR_AUTHOR",
    }
    receipt = render_integration_receipt(plan, mode="report", generated_at=NOW, provider="openai")
    assert receipt["refresh_owner"] is None
    assert receipt["refresh_owner_reason"] == "NO_AUTOMATIC_REFRESH_OWNER_FOR_AUTHOR"


def test_blocked_and_empty_plans_keep_their_exact_reason():
    blocked = render_integration_receipt(
        {"decision": "BLOCKED_EVIDENCE", "blockers": ["PULL_0_BEHIND_BASE_UNATTESTED"]},
        mode="report",
        generated_at=NOW,
    )
    assert blocked["status"] == "BLOCKED_EVIDENCE"
    assert blocked["blockers"] == ["PULL_0_BEHIND_BASE_UNATTESTED"]
    empty = render_integration_receipt(
        {"decision": "NO_ELIGIBLE_PULL", "blockers": []}, mode="report", generated_at=NOW
    )
    assert empty["status"] == "NO_ELIGIBLE_PULL"
    assert empty["selected_pull_number"] is None


def test_cli_off_writes_a_disabled_receipt(tmp_path):
    output = tmp_path / "reservation.json"
    assert main(["select", "--mode", "off", "--output", str(output)]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "DISABLED"
    assert receipt["can_execute"] is False


def test_github_row_carries_the_merge_state_used_to_separate_repair_from_refresh():
    assert github_pull_record(gh_pull(), now=NOW, behind_by=12)["merge_state"] == "behind"
    dirty = github_pull_record(
        gh_pull(mergeStateStatus="DIRTY"), now=NOW, behind_by=4
    )
    assert dirty["merge_state"] == "dirty"


def test_github_row_carries_author_evidence_for_owner_attribution():
    assert github_pull_record(gh_pull(), now=NOW, behind_by=12)["author_login"] == (
        "wow-engineering[bot]"
    )
    ordinary = github_pull_record(
        gh_pull(author={"login": "some-human"}), now=NOW, behind_by=12
    )
    assert ordinary["author_login"] == "some-human"
    missing = github_pull_record(gh_pull(author=None), now=NOW, behind_by=12)
    assert missing["author_login"] is None


def test_cli_report_reserves_the_oldest_behind_main_pull(tmp_path):
    prs = tmp_path / "prs.json"
    old = gh_pull(number=2666, createdAt="2026-11-20T12:00:00Z", behind_by=37)
    new = gh_pull(number=2891, createdAt="2026-11-28T08:00:00Z", behind_by=2)
    prs.write_text(json.dumps([new, old]))
    output = tmp_path / "reservation.json"
    assert main([
        "select", "--mode", "report", "--prs-file", str(prs),
        "--provider", "openai", "--now", "2026-11-28T12:00:00Z", "--output", str(output),
    ]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "RESERVED"
    assert receipt["selected_pull_number"] == 2666
    assert receipt["reserved_head_sha"] == SHA
    assert receipt["requires_refresh"] is True
    assert receipt["requires_code_repair"] is False
    assert receipt["requires_fresh_acceptance"] is True
    assert receipt["refresh_owner"] == "WOW_V17_ENGINEERING_PR_KEEP_CURRENT"
    assert receipt["refresh_owner_reason"] == "KEEP_CURRENT_SELECTS_THIS_PULL"
    assert receipt["dispatch_repair"] is False


def test_cli_dispatch_mode_marks_the_repair_but_never_merges(tmp_path):
    prs = tmp_path / "prs.json"
    conflict = gh_pull(mergeStateStatus="DIRTY", behind_by=0)
    prs.write_text(json.dumps([conflict]))
    output = tmp_path / "reservation.json"
    assert main([
        "select", "--mode", "dispatch", "--prs-file", str(prs),
        "--provider", "openai", "--now", "2026-11-28T12:00:00Z", "--output", str(output),
    ]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["requires_code_repair"] is True
    assert receipt["dispatch_repair"] is True
    assert receipt["can_execute"] is False
    assert "merge" not in json.dumps(receipt).lower()


def test_cli_records_no_refresh_owner_for_an_ordinary_author(tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([
        gh_pull(
            number=2666,
            mergeStateStatus="BEHIND",
            behind_by=19,
            author={"login": "some-human"},
            createdAt="2026-11-19T12:00:00Z",
        )
    ]))
    output = tmp_path / "reservation.json"
    assert main([
        "select", "--mode", "dispatch", "--prs-file", str(prs),
        "--provider", "openai", "--now", "2026-11-28T12:00:00Z", "--output", str(output),
    ]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "RESERVED"
    assert receipt["refresh_owner"] is None
    assert receipt["refresh_owner_reason"] == "NO_AUTOMATIC_REFRESH_OWNER_FOR_AUTHOR"
    assert receipt["dispatch_repair"] is False


def test_cli_credits_keep_current_only_for_the_bot_author(tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([gh_pull(mergeStateStatus="BEHIND", behind_by=19)]))
    output = tmp_path / "reservation.json"
    assert main([
        "select", "--mode", "report", "--prs-file", str(prs),
        "--now", "2026-11-28T12:00:00Z", "--output", str(output),
    ]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["refresh_owner"] == "WOW_V17_ENGINEERING_PR_KEEP_CURRENT"
    assert receipt["refresh_owner_reason"] == "KEEP_CURRENT_SELECTS_THIS_PULL"


def test_cli_dispatch_mode_refuses_a_behind_only_pull(tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps([gh_pull(mergeStateStatus="BEHIND", behind_by=12)]))
    output = tmp_path / "reservation.json"
    assert main([
        "select", "--mode", "dispatch", "--prs-file", str(prs),
        "--provider", "openai", "--now", "2026-11-28T12:00:00Z", "--output", str(output),
    ]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "RESERVED"
    assert receipt["dispatch_repair"] is False
    assert receipt["refresh_owner"] == "WOW_V17_ENGINEERING_PR_KEEP_CURRENT"


def test_cli_unreadable_evidence_returns_blocked_without_raising(tmp_path):
    output = tmp_path / "reservation.json"
    assert main([
        "select", "--mode", "report", "--prs-file", str(tmp_path / "missing.json"),
        "--now", "2026-11-28T12:00:00Z", "--output", str(output),
    ]) == 0
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "BLOCKED_EVIDENCE"
    assert receipt["dispatch_repair"] is False
