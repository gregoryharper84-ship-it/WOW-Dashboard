from v17 import engineering_recovery_planner as rp


def test_non_wow_engineering_pr_is_always_ignored():
    result = rp.plan_recovery(
        pr_number=1, findings=["SOME_FAILURE"], attempts=0, is_wow_engineering_pr=False
    )
    assert result["action"] == "IGNORE"
    assert result["can_execute"] is False


def test_no_findings_is_a_noop():
    result = rp.plan_recovery(pr_number=1, findings=[], attempts=0, is_wow_engineering_pr=True)
    assert result["action"] == "NONE"


def test_repairable_finding_dispatches_repair_and_increments_attempts():
    result = rp.plan_recovery(
        pr_number=1, findings=["QA_REGRESSION_FAILED"], attempts=0, is_wow_engineering_pr=True
    )
    assert result["action"] == "DISPATCH_REPAIR"
    assert result["attempts"] == 1
    assert result["can_execute"] is False


def test_repair_dispatch_at_second_attempt():
    result = rp.plan_recovery(
        pr_number=1, findings=["QA_REGRESSION_FAILED"], attempts=2, is_wow_engineering_pr=True
    )
    assert result["action"] == "DISPATCH_REPAIR"
    assert result["attempts"] == 3


def test_retry_cap_stops_further_repair_dispatch():
    result = rp.plan_recovery(
        pr_number=1, findings=["QA_REGRESSION_FAILED"], attempts=3, is_wow_engineering_pr=True
    )
    assert result["action"] == "NEEDS_OWNER_RETRY_CAP"
    assert result["label"] == "needs-owner"


def test_retry_cap_does_not_decrement_or_reset_attempts():
    result = rp.plan_recovery(
        pr_number=1, findings=["QA_REGRESSION_FAILED"], attempts=5, is_wow_engineering_pr=True
    )
    assert result["action"] == "NEEDS_OWNER_RETRY_CAP"
    assert result["attempts"] == 5


def test_qa_trust_root_finding_is_never_dispatched_even_at_zero_attempts():
    result = rp.plan_recovery(
        pr_number=1,
        findings=["QA_TRUST_ROOT_OWNER_AUTHORED"],
        attempts=0,
        is_wow_engineering_pr=True,
    )
    assert result["action"] == "WAITING_ON_OWNER"
    assert result["non_repairable_findings"] == ["QA_TRUST_ROOT_OWNER_AUTHORED"]


def test_owner_approval_missing_suffix_is_never_dispatched():
    result = rp.plan_recovery(
        pr_number=1,
        findings=["RELEASE_TRUST_ROOT_OWNER_APPROVAL_MISSING"],
        attempts=0,
        is_wow_engineering_pr=True,
    )
    assert result["action"] == "WAITING_ON_OWNER"


def test_mixed_repairable_and_non_repairable_is_waiting_on_owner_not_dispatched():
    result = rp.plan_recovery(
        pr_number=1,
        findings=["QA_REGRESSION_FAILED", "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"],
        attempts=0,
        is_wow_engineering_pr=True,
    )
    assert result["action"] == "WAITING_ON_OWNER"
    assert result["non_repairable_findings"] == ["QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"]


def test_waiting_on_owner_never_increments_attempts_or_touches_retry_cap():
    result = rp.plan_recovery(
        pr_number=1,
        findings=["QA_TRUST_ROOT_OWNER_AUTHORED"],
        attempts=2,
        is_wow_engineering_pr=True,
    )
    assert result["attempts"] == 2
    assert "label" not in result


def test_is_repairable_finding_helper():
    assert rp.is_repairable_finding("QA_REGRESSION_FAILED") is True
    assert rp.is_repairable_finding("QA_TRUST_ROOT_OWNER_AUTHORED") is False
    assert rp.is_repairable_finding("RELEASE_TRUST_ROOT_OWNER_APPROVAL_MISSING") is False


def test_parse_findings_from_summary_extracts_backtick_codes():
    summary = (
        "Typed findings:\n- `QA_REGRESSION_FAILED`\n- `QA_TRUST_ROOT_OWNER_APPROVAL_MISSING`"
        "\n\nFail-closed. can_execute=false."
    )
    assert rp.parse_findings_from_summary(summary) == [
        "QA_REGRESSION_FAILED",
        "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING",
    ]


def test_parse_findings_from_summary_pass_has_no_findings():
    summary = "All exact-head conditions satisfied. Evidence only: not merge, deployment or probability authority. can_execute=false."
    assert rp.parse_findings_from_summary(summary) == []


def test_cli_plan_from_summary_matches_findings_json(capsys):
    rc = rp.main(
        [
            "--pr-number",
            "9",
            "--summary",
            "Typed findings:\n- `QA_REGRESSION_FAILED`",
            "--attempts",
            "0",
            "--is-wow-engineering-pr",
        ]
    )
    import json

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["action"] == "DISPATCH_REPAIR"
    assert payload["findings"] == ["QA_REGRESSION_FAILED"]


def test_cli_plan_outputs_json(capsys):
    rc = rp.main(
        [
            "--pr-number",
            "9",
            "--findings-json",
            '["QA_REGRESSION_FAILED"]',
            "--attempts",
            "0",
            "--is-wow-engineering-pr",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    import json

    payload = json.loads(out)
    assert payload["action"] == "DISPATCH_REPAIR"


def test_owner_authored_trust_root_reports_independent_source_reentry():
    result = rp.plan_recovery(pr_number=2125,
                              findings=["QA_TRUST_ROOT_OWNER_AUTHORED"],
                              attempts=2, is_wow_engineering_pr=True)
    assert result["action"] == "WAITING_ON_OWNER"
    assert result["required_actor"] == "INDEPENDENT_ENGINEERING_AUTHOR"
    assert result["next_action"] == "INDEPENDENT_SOURCE_ADOPTION_WITH_VERIFIED_WRITER_LEASE"
    assert result["attempts"] == 2 and result["can_execute"] is False


def test_missing_owner_approval_does_not_spawn_fake_independent_implementation():
    result = rp.plan_recovery(pr_number=2125,
                              findings=["QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"],
                              attempts=0, is_wow_engineering_pr=True)
    assert result["required_actor"] == "ACTUAL_CODEOWNER"
    assert result["next_action"] == "FRESH_EXACT_HEAD_OWNER_APPROVAL"
    assert result["can_execute"] is False


def test_owner_authored_non_bot_trust_root_is_retained_for_independent_reentry():
    result = rp.plan_recovery(
        pr_number=2125, findings=["QA_TRUST_ROOT_OWNER_AUTHORED"],
        attempts=0, is_wow_engineering_pr=False,
    )
    assert result["action"] == "WAITING_ON_OWNER"
    assert result["required_actor"] == "INDEPENDENT_ENGINEERING_AUTHOR"
    assert result["next_action"] == "INDEPENDENT_SOURCE_ADOPTION_WITH_VERIFIED_WRITER_LEASE"
    assert result["attempts"] == 0
    assert result["can_execute"] is False


def test_non_bot_unrelated_qa_finding_remains_ignored():
    result = rp.plan_recovery(
        pr_number=2125, findings=["QA_REGRESSION_FAILED"],
        attempts=0, is_wow_engineering_pr=False,
    )
    assert result["action"] == "IGNORE"
    assert result["can_execute"] is False



def test_owner_authored_review_routed_without_owner_or_fake_approval():
    for is_bot in (True, False):
        result = rp.plan_recovery(
            pr_number=2127,
            findings=["QA_TRUST_ROOT_OWNER_AUTHORED"],
            attempts=2,
            is_wow_engineering_pr=is_bot,
        )
        assert result["action"] == "WAITING_ON_OWNER"  # legacy worker contract
        assert result["progression_state"] == "AUTOMATED_INDEPENDENT_SOURCE_HANDOFF"
        assert result["reentry_trigger"] == "EXISTING_HOURLY_ADOPTION_AND_QA_HOLD_WORKER"
        assert result["required_actor"] == "INDEPENDENT_ENGINEERING_AUTHOR"
        assert result["owner_intervention_required"] is False
        assert result["unrelated_work_may_progress"] is True
        assert result["release_admission"] == "DENIED_UNTIL_INDEPENDENT_EVIDENCE"
        assert result["attempts"] == 2 and result["can_execute"] is False


def test_codeowner_approval_is_external_but_unrelated_work_is_not_stopped():
    result = rp.plan_recovery(
        pr_number=2127,
        findings=["QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"],
        attempts=1,
        is_wow_engineering_pr=True,
    )
    assert result["progression_state"] == "ACTUAL_CODEOWNER_AUTHORIZATION_REQUIRED"
    assert result["reentry_trigger"] == "AUTHENTIC_GITHUB_CODEOWNER_REVIEW_AT_EXACT_SHA"
    assert result["owner_intervention_required"] is True
    assert result["unrelated_work_may_progress"] is True
    assert result["release_admission"] == "DENIED_UNTIL_INDEPENDENT_EVIDENCE"
    assert result["attempts"] == 1 and result["can_execute"] is False



def test_protected_recovery_workflow_routes_author_vs_codeowner_without_fake_approval():
    from pathlib import Path
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github/workflows/wow-v17-engineering-auto-repair.yml"
    ).read_text()
    assert "AUTOMATED_INDEPENDENT_SOURCE_HANDOFF|INDEPENDENT_ENGINEERING_AUTHOR|false" in workflow
    assert "ACTUAL_CODEOWNER_AUTHORIZATION_REQUIRED|ACTUAL_CODEOWNER|true" in workflow
    assert "RECOVERY_NONREPAIRABLE_ROUTE_INVALID" in workflow
    assert "unrelated Engineering work continues" in workflow
    assert "gh pr review --approve" not in workflow
    assert "gh pr merge" not in workflow
