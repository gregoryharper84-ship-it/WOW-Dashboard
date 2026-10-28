"""Focused and adversarial Phase 1/2 support-only handoff regressions."""
from __future__ import annotations

import copy
import json
import shlex
import subprocess
from pathlib import Path

import pytest

from v17.engineering_agent_team import (
    NONFINAL_OWNED_HOLDS, TERMINAL_ISSUE_STATES, closure_wip,
    is_actionable, reliability_blocks_frontier, select_priority_incident,
)

from v17.engineering_phase12_delegation import (
    CAN_EXECUTE, DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
    TERMINAL_AUTHORITY, admission_receipt, validate_support_packet,
)


def _pr_packet():
    return {
        "action": "SUPPORT_PR", "incident_id": "1688", "pr_number": "2069",
        "head_sha": "a" * 40, "wait_state": "CI_PENDING", "typed_failure": "",
        "primary_subsystem": "", "check_state": "[]", "implementation_lease": False,
        "support_only": True, "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY,
        "source_repository": "gregoryharper84-ship-it/WOW-Dashboard",
        "expected_repository": "gregoryharper84-ship-it/WOW-Dashboard",
        "pr_head_repository": "gregoryharper84-ship-it/WOW-Dashboard",
        "base_branch": "main", "pr_state": "open",
        "source_kind": "GITHUB_PR_EXACT_HEAD",
    }


def test_exact_pr_support_only():
    packet = _pr_packet()
    assert validate_support_packet(packet) == []
    receipt = admission_receipt(packet)
    assert receipt["admitted"] is True
    assert receipt["implementation_lease"] is False
    assert receipt["may_approve"] is False
    assert receipt["may_merge"] is False
    assert receipt["may_deploy"] is False
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert CAN_EXECUTE is False and DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS is True


def test_lifecycle_reentry_without_pr_is_support_only():
    packet = _pr_packet()
    packet.update(action="SUPPORT_RELEASE", pr_number="", head_sha="",
                  check_state="NOT_APPLICABLE", source_kind="DURABLE_LEDGER_INCIDENT")
    assert validate_support_packet(packet) == []


@pytest.mark.parametrize("incident", [
    "1688", "PM-2026-09-14-001", "PM-2026-12-31-999",
])
def test_release_support_accepts_only_canonical_numeric_or_dated_ledger_ids(incident):
    packet = _pr_packet()
    packet.update(action="SUPPORT_RELEASE", incident_id=incident,
                  pr_number="", head_sha="", source_kind="DURABLE_LEDGER_INCIDENT",
                  check_state="NOT_APPLICABLE")
    assert validate_support_packet(packet) == []
    receipt = admission_receipt(packet)
    assert receipt["admitted"] is True
    assert receipt["may_merge"] is False
    assert receipt["may_approve"] is False
    assert receipt["may_deploy"] is False
    assert receipt["can_execute"] is False


@pytest.mark.parametrize("incident", [
    "PM-2026-09-14-001", "PR-123", "PM-2026-09-14-001; rm -rf /",
])
def test_pr_support_rejects_non_numeric_incident_even_if_ledger_format(incident):
    packet = _pr_packet()
    packet["incident_id"] = incident
    assert "SUPPORT_INCIDENT_ID_INVALID" in validate_support_packet(packet)


@pytest.mark.parametrize("incident", [
    "PM-2026-02-30-001", "PM-2026-13-14-001",
    "PM-2026-09-14-1", "PM-2026-09-14-001-extra",
    "PM-2026-09-14-001;echo hacked",
    "pm-2026-09-14-001", "PM-2026-09-14-001\nTAMPER", "0",
])
def test_release_support_rejects_malformed_injected_or_noncalendar_ledger_id(incident):
    packet = _pr_packet()
    packet.update(action="SUPPORT_RELEASE", incident_id=incident,
                  pr_number="", head_sha="", source_kind="DURABLE_LEDGER_INCIDENT",
                  check_state="NOT_APPLICABLE")
    assert "SUPPORT_INCIDENT_ID_INVALID" in validate_support_packet(packet)
    assert admission_receipt(packet)["admitted"] is False


def test_actual_paginated_support_checks_are_one_json_array_not_concatenated(monkeypatch):
    import shutil
    if shutil.which("jq") is None:
        pytest.skip("jq not installed on local runner")
    workflow = (Path(__file__).resolve().parents[2] /
                ".github/workflows/wow-v17-engineering-specialist-support.yml").read_text()
    assert "jq -s '[.[].check_runs[]]'" in workflow
    # GitHub --paginate yields separate object documents, not a single array.
    pages = "\n".join(json.dumps({"check_runs": [
        {"name": "check", "status": "completed", "conclusion": "success"}
        for _ in range(n)
    ]}) for n in (100, 23))
    flattened = subprocess.run(["jq", "-s", "[.[].check_runs[]]"], input=pages,
                               capture_output=True, text=True, check=True)
    checks = json.loads(flattened.stdout)
    assert len(checks) == 123
    packet = _pr_packet()
    packet["check_state"] = json.dumps(checks)
    assert validate_support_packet(packet) == []
    # Limit must remain in effect; no loss/truncation of overflow data.
    packet["check_state"] = json.dumps(checks * 5)
    assert "SUPPORT_CHECK_STATE_INVALID" in validate_support_packet(packet)


def test_negative_untrusted_handoffs():
    cases = [
        ("action", "MERGE", "SUPPORT_ACTION_INVALID"),
        ("incident_id", "../../bad", "SUPPORT_INCIDENT_ID_INVALID"),
        ("incident_id", "", "SUPPORT_INCIDENT_ID_INVALID"),
        ("implementation_lease", True, "SUPPORT_IMPLEMENTATION_LEASE_INVALID"),
        ("support_only", False, "SUPPORT_SUPPORT_ONLY_INVALID"),
        ("can_execute", True, "SUPPORT_CAN_EXECUTE_INVALID"),
        ("terminal_authority", "another", "SUPPORT_TERMINAL_AUTHORITY_INVALID"),
        ("wait_state", "OWNER_CAN_IGNORE_REVIEW", "SUPPORT_WAIT_STATE_INVALID"),
        ("pr_number", "2; rm -rf /", "SUPPORT_PR_NUMBER_INVALID"),
        ("head_sha", "a" * 39, "SUPPORT_PR_HEAD_SHA_INVALID"),
        ("source_repository", "attacker/fork", "SUPPORT_CROSS_REPOSITORY_PR"),
        ("pr_head_repository", "attacker/fork", "SUPPORT_CROSS_REPOSITORY_PR"),
        ("base_branch", "unprotected", "SUPPORT_PR_NOT_OPEN_AGAINST_MAIN"),
        ("pr_state", "closed", "SUPPORT_PR_NOT_OPEN_AGAINST_MAIN"),
        ("source_kind", "LLM_GUESS", "SUPPORT_SOURCE_EVIDENCE_INVALID"),
        ("check_state", '{"fake":"success"}', "SUPPORT_CHECK_STATE_INVALID"),
        ("typed_failure", "MODEL_UNAVAILABLE; rm", "SUPPORT_TYPED_FAILURE_INVALID"),
    ]
    for field, bad, expected in cases:
        candidate = copy.deepcopy(_pr_packet())
        candidate[field] = bad
        assert expected in validate_support_packet(candidate), (field, expected)


def test_unknown_fields_and_missing_packet_fail_closed():
    candidate = _pr_packet()
    candidate["grant_release"] = True
    assert "SUPPORT_PACKET_UNKNOWN_FIELDS" in validate_support_packet(candidate)
    assert validate_support_packet(None) == ["SUPPORT_PACKET_NOT_OBJECT"]
    assert validate_support_packet([]) == ["SUPPORT_PACKET_NOT_OBJECT"]


def test_pr_checks_are_not_cleared_by_empty_or_pending_ci():
    candidate = _pr_packet()
    candidate["check_state"] = json.dumps([{"name": "QA", "status": "in_progress", "conclusion": None}])
    receipt = admission_receipt(candidate)
    assert receipt["admitted"] is True
    assert receipt["may_approve"] is False
    assert receipt["may_merge"] is False
    assert receipt["may_deploy"] is False


def test_malformed_checks_fail_closed():
    packet = _pr_packet()
    for data in ["none", "null", "{}", '[["status"]]', '[{"status":"completed"}]']:
        packet["check_state"] = data
        assert "SUPPORT_CHECK_STATE_INVALID" in validate_support_packet(packet)


def test_blocked_incident_is_retained_not_retried_or_falsely_closed():
    incident = {
        "incident_id": "1688", "severity": "P0", "state": "BLOCKED_WITH_EXACT_REASON",
        "current_owner": "ENGINEERING_AGENT", "next_action": "Reproduce with fresh evidence",
        "reentry_ready": True,  # A flag alone is not a validated writer lease.
    }
    assert incident["state"] in NONFINAL_OWNED_HOLDS
    assert incident["state"] not in TERMINAL_ISSUE_STATES
    assert is_actionable(incident) is False
    assert reliability_blocks_frontier([incident]) is True
    decision = select_priority_incident([incident])
    assert decision.incident_id is None
    assert decision.frontier_allowed is False
    counts = closure_wip([incident])
    assert counts["nonfinal_held_owned"] == 1
    assert counts["new_product_work_allowed"] is True


def test_missing_owner_in_nonfinal_hold_is_visible():
    incident = {
        "incident_id": "1021", "severity": "P1", "state": "DEFERRED_WITH_JUSTIFICATION",
        "current_owner": "", "next_action": "",
    }
    assert closure_wip([incident])["nonfinal_held_missing_handoff"] == 1
    assert reliability_blocks_frontier([incident]) is True


def test_only_actionable_work_reenters_and_lower_held_does_not_starve_improvement():
    held = {"incident_id": "100", "severity": "P2", "state": "DEFERRED_WITH_JUSTIFICATION"}
    assert is_actionable(held) is False
    assert reliability_blocks_frontier([held]) is False
    actionable = {"incident_id": "101", "severity": "P0", "state": "OPEN"}
    assert select_priority_incident([held, actionable]).incident_id == "101"
    closed = {"incident_id": "102", "severity": "P0", "state": "FIXED_AND_VERIFIED"}
    assert select_priority_incident([closed]).frontier_allowed is True


def test_protected_workflow_performs_attributed_support_handoff():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[2] /
                ".github/workflows/wow-v17-engineering-specialist-support.yml").read_text()
    assert "SUPPORT_PARENT_INCIDENT_MISMATCH" in workflow
    assert "SUPPORT_PARENT_INCIDENT_UNPROVEN" in workflow
    assert "Validate bounded specialist handoff" in workflow
    assert "engineering_phase12_delegation.py validate-support-packet" in workflow
    assert workflow.index("Validate bounded specialist handoff") < workflow.index("Specialist support subagent")
    assert "terminal_authority:\"V17_TERMINAL_REDUCER\"" in workflow


def test_phase12_pr_lifecycle_declaration_remains_explicit_and_fail_closed():
    from v17.engineering_agent_team import validate_lifecycle_pr_body
    flags = (
        "changes_model_registration", "changes_routing_or_hydration",
        "changes_reconciliation_behavior", "changes_prediction_persistence_contract",
        "changes_sporting_probability_math", "changes_fitted_artifacts_or_coefficients",
        "changes_calibration_or_lower_bounds", "changes_qualification_thresholds",
        "changes_failure_path_weighting",
    )
    paths = ("artifacts/wow-engine/v17/engineering_agent_team.py",)
    body = "\n".join((
        "Incident: `1021`",
        "Lifecycle-Control-Plane: true",
        "Change-Class: B",
        *(f"- {flag}: false" for flag in flags),
    ))
    assert validate_lifecycle_pr_body(body, changed_paths=paths) == []
    assert any("scope flags" in error for error in
               validate_lifecycle_pr_body("Change-Class: B", changed_paths=paths))
    unsafe = body.replace("- changes_calibration_or_lower_bounds: false",
                          "- changes_calibration_or_lower_bounds: true")
    assert any("CLASSIFICATION_ESCALATION_REQUIRED" in error for error in
               validate_lifecycle_pr_body(unsafe, changed_paths=paths))


# Exercise the actual workflow gate instead of duplicating incident parsing.
_ROOT = Path(__file__).resolve().parents[2]


def _real_specialist_incident_gate(body: str, requested: str = ""):
    workflow = (
        _ROOT / ".github/workflows/wow-v17-engineering-specialist-support.yml"
    ).read_text()
    start = workflow.index("            # Reuse strict incident identity across the repo;")
    # Find the actual check-run command, regardless of explanatory comments.
    # The extracted snippet still runs the original incident-identity shell gate.
    end = workflow.index("\n            checks=$(gh api", start)
    script = (
        "set -euo pipefail\n"
        + "pr=" + shlex.quote(json.dumps({"body": body})) + "\n"
        + "incident=" + shlex.quote(requested) + "\n"
        + workflow[start:end] + "\n"
        + 'printf "IDENTITY:%s\\n" "$incident"\n'
    )
    return subprocess.run(["bash", "-c", script], cwd=_ROOT,
                          capture_output=True, text=True, check=False, timeout=10)


@pytest.mark.parametrize("body,requested", [
    ("Incident: #2136", ""),
    ("Incident: #2136", "2136"),
    ("Incident: 2136", "2136"),
    ("Incident: `2136`", "2136"),
    ("Incident: #2136\nParent: #1688", ""),
])
def test_actual_specialist_workflow_accepts_canonical_incident(body, requested):
    result = _real_specialist_incident_gate(body, requested)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("IDENTITY:2136\n")


@pytest.mark.parametrize("body,requested,error", [
    ("Incident: #2136", "1688", "SUPPORT_PARENT_INCIDENT_MISMATCH"),
    ("", "2136", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Related: #2136", "", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Incident: #2136\nIncident: #1688", "", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Incident: #0", "", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Incident: #2136 extra", "", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Incident: #2136\nIncident: `2136`", "2136", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Incident: #2136; rm -rf /", "", "SUPPORT_PARENT_INCIDENT_UNPROVEN"),
    ("Incident: #2136", "PR-2189", "SUPPORT_PARENT_INCIDENT_MISMATCH"),
])
def test_actual_specialist_workflow_rejects_unproved_incident(body, requested, error):
    result = _real_specialist_incident_gate(body, requested)
    assert result.returncode != 0
    assert f"::error::{error}" in result.stdout + result.stderr
    assert "IDENTITY:" not in result.stdout


def test_incident_gate_reuses_shared_parser_and_rejects_legacy_fallback():
    workflow = (
        _ROOT / ".github/workflows/wow-v17-engineering-specialist-support.yml"
    ).read_text()
    assert "engineering_incident_identity.py" in workflow
    assert 'incident="PR-' not in workflow
    assert "Validate bounded specialist handoff" in workflow
    assert 'terminal_authority:"V17_TERMINAL_REDUCER"' in workflow
