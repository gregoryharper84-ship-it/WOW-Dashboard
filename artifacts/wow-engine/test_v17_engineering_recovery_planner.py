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
    assert result["action"] == "BLOCKER_TEAM_REENTRY"
    assert result["reason"] == "REPAIR_RETRY_CAP_EXHAUSTED"
    assert result["next_owner"] == "ROOT_CAUSE_INVESTIGATOR"
    assert result["attempts"] == 3
    assert "label" not in result
    assert result["can_execute"] is False


def test_retry_cap_does_not_decrement_or_reset_attempts():
    result = rp.plan_recovery(
        pr_number=1, findings=["QA_REGRESSION_FAILED"], attempts=5, is_wow_engineering_pr=True
    )
    assert result["action"] == "BLOCKER_TEAM_REENTRY"
    assert result["attempts"] == 5
    assert result["revisit_trigger"] == "FRESH_ROOT_CAUSE_OR_CHANGED_HEAD_EVIDENCE"


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

def test_cap_does_not_override_missing_owner_approval():
    result = rp.plan_recovery(
        pr_number=11, findings=["QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"],
        attempts=4, is_wow_engineering_pr=True,
    )
    assert result["action"] == "WAITING_ON_OWNER"
    assert result["attempts"] == 4


def test_cap_does_not_override_pending_ci_evidence():
    result = rp.plan_recovery(
        pr_number=11, findings=["RELEASE_QA_MISSING"],
        attempts=4, is_wow_engineering_pr=True,
    )
    assert result["action"] == "WAITING_ON_EVIDENCE"
    assert result["attempts"] == 4


def test_triple_d_reentry_workflow_has_exact_head_and_single_writer_guards():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[2] /
                ".github/workflows/wow-v17-engineering-auto-repair.yml").read_text()
    assert "steps.plan.outputs.action == 'BLOCKER_TEAM_REENTRY'" in workflow
    assert "WOW_TRIPLE_D_REENTRY:PR=" in workflow
    assert "REENTRY_PR_HEAD_STALE_OR_UNTRUSTED" in workflow
    assert "REENTRY_INCIDENT_NOT_IN_APPROVED_MANIFEST" in workflow
    assert "REENTRY_INCIDENT_SEVERITY_MISMATCH" in workflow
    assert "REENTRY_EXISTING_WRITER_QUEUE" in workflow
    assert "steps.plan.outputs.action == 'NEEDS_OWNER_RETRY_CAP'" not in workflow
