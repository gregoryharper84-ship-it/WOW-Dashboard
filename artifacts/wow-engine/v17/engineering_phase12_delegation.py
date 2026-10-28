"""Fail-closed Phase 1/2 evidence gate for existing support-only delegation.

This is a pure validator, not another scheduler, lease holder, producer, or
release actor. It only admits read-only specialist support for an already-owned
incident and a verified same-repository PR when applicable.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

CUSTOM_GPT_IDENTITY = "WOW_BETTING_ENGINE"
RUNTIME_GENERATION = "V17_ACTIVE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True

ACTIONS = frozenset({"SUPPORT_PR", "SUPPORT_RELEASE"})
WAIT_STATES = frozenset({
    "CI_PENDING", "REVIEW_PENDING", "MERGE_PENDING", "DEPLOYMENT_PENDING",
    "PROVIDER_WAIT", "EXTERNAL_WAIT", "APPROVAL_PENDING",
})
PACKET_FIELDS = frozenset({
    "action", "incident_id", "pr_number", "head_sha", "wait_state",
    "typed_failure", "primary_subsystem", "check_state",
    "implementation_lease", "support_only", "can_execute", "terminal_authority",
    "source_repository", "expected_repository", "base_branch", "pr_state",
    "pr_head_repository", "source_kind",
})
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
INCIDENT = re.compile(r"(?:[1-9][0-9]*|PR-[1-9][0-9]*)\Z")
NUMERIC_PR = re.compile(r"[1-9][0-9]*\Z")
TOKEN = re.compile(r"[A-Z][A-Z0-9_]{0,95}\Z")


def validate_support_packet(packet: Any) -> list[str]:
    """Reject unproven handoffs. A validated packet never grants a writer lease.

    The protected workflow separately fetches the GitHub PR and places its
    authoritative repository/base/head/state into this packet. This validator
    cannot fetch evidence or imply that a PR is approved or CI is green.
    """
    if not isinstance(packet, dict):
        return ["SUPPORT_PACKET_NOT_OBJECT"]
    errors: list[str] = []
    unknown = sorted(set(packet) - PACKET_FIELDS)
    if unknown:
        errors.append("SUPPORT_PACKET_UNKNOWN_FIELDS")
    if packet.get("action") not in ACTIONS:
        errors.append("SUPPORT_ACTION_INVALID")
    incident = packet.get("incident_id")
    if not isinstance(incident, str) or not INCIDENT.fullmatch(incident):
        errors.append("SUPPORT_INCIDENT_ID_INVALID")
    for key, required in (("can_execute", False), ("implementation_lease", False),
                          ("support_only", True)):
        if packet.get(key) is not required:
            errors.append(f"SUPPORT_{key.upper()}_INVALID")
    if packet.get("terminal_authority") != TERMINAL_AUTHORITY:
        errors.append("SUPPORT_TERMINAL_AUTHORITY_INVALID")
    if packet.get("wait_state") not in WAIT_STATES:
        errors.append("SUPPORT_WAIT_STATE_INVALID")
    for key in ("typed_failure", "primary_subsystem"):
        value = packet.get(key)
        if not isinstance(value, str) or (value and not TOKEN.fullmatch(value)):
            errors.append(f"SUPPORT_{key.upper()}_INVALID")
    if packet.get("action") == "SUPPORT_PR":
        pr = packet.get("pr_number")
        if not isinstance(pr, str) or not NUMERIC_PR.fullmatch(pr):
            errors.append("SUPPORT_PR_NUMBER_INVALID")
        head = packet.get("head_sha")
        if not isinstance(head, str) or not SHA40.fullmatch(head):
            errors.append("SUPPORT_PR_HEAD_SHA_INVALID")
        expected = packet.get("expected_repository")
        if not isinstance(expected, str) or not REPO.fullmatch(expected):
            errors.append("SUPPORT_EXPECTED_REPOSITORY_INVALID")
        if packet.get("source_repository") != expected or packet.get("pr_head_repository") != expected:
            errors.append("SUPPORT_CROSS_REPOSITORY_PR")
        if packet.get("base_branch") != "main" or packet.get("pr_state") != "open":
            errors.append("SUPPORT_PR_NOT_OPEN_AGAINST_MAIN")
        if packet.get("source_kind") != "GITHUB_PR_EXACT_HEAD":
            errors.append("SUPPORT_SOURCE_EVIDENCE_INVALID")
        checks = packet.get("check_state")
        if not isinstance(checks, str):
            errors.append("SUPPORT_CHECK_STATE_INVALID")
        else:
            try:
                rows = json.loads(checks)
                if (not isinstance(rows, list) or len(rows) > 500 or
                        any(not isinstance(x, dict) or
                            not isinstance(x.get("name"), str) or
                            not isinstance(x.get("status"), str) or
                            (x.get("conclusion") is not None and not isinstance(x.get("conclusion"), str))
                            for x in rows)):
                    raise ValueError("bad checks")
            except (ValueError, TypeError):
                errors.append("SUPPORT_CHECK_STATE_INVALID")
    elif packet.get("action") == "SUPPORT_RELEASE":
        if packet.get("source_kind") != "DURABLE_LEDGER_INCIDENT":
            errors.append("SUPPORT_SOURCE_EVIDENCE_INVALID")
        if packet.get("pr_number") or packet.get("head_sha"):
            errors.append("SUPPORT_RELEASE_PR_AMBIGUOUS")
    return errors


def admission_receipt(packet: Any) -> dict[str, Any]:
    errors = validate_support_packet(packet)
    return {
        "admitted": not errors,
        "reason": "READ_ONLY_SPECIALIST_SUPPORT_ONLY" if not errors else "TYPED_SUPPORT_HOLD",
        "errors": errors,
        "incident_id": packet.get("incident_id") if isinstance(packet, dict) else None,
        "implementation_lease": False,
        "support_only": True,
        "may_approve": False,
        "may_merge": False,
        "may_deploy": False,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate-support-packet"])
    parser.add_argument("--packet-file", required=True)
    args = parser.parse_args(argv)
    try:
        # The caller must provide a fixed workflow-created path, not external PR code.
        packet = json.loads(Path(args.packet_file).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        packet = None
    receipt = admission_receipt(packet)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
