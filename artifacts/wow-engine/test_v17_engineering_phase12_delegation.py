"""Focused and adversarial Phase 1/2 support-only handoff regressions."""
from __future__ import annotations

import copy
import json

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
