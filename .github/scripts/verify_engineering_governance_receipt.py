#!/usr/bin/env python3
"""Verify a trusted WOW engineering governance receipt against an exact PR head."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
WORKFLOW_NAME = "wow-v17-chatgpt-engineering-worker"
ALLOWED_ARCHITECT_DECISIONS = {"PASS", "NOT_APPLICABLE"}


class GovernanceReceiptError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GovernanceReceiptError(message)


def verify_receipt(
    receipt: dict[str, Any],
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_workflow_run_id: str | None = None,
) -> None:
    _require(receipt.get("receipt_schema_version") == 1, "receipt schema version is not supported")
    _require(receipt.get("workflow_name") == WORKFLOW_NAME, "receipt workflow identity mismatch")
    _require(receipt.get("repository") == expected_repository, "receipt repository mismatch")
    if expected_workflow_run_id is not None:
        _require(
            str(receipt.get("workflow_run_id", "")) == str(expected_workflow_run_id),
            "receipt workflow run id mismatch",
        )

    implementation = receipt.get("implementation") or {}
    _require(implementation.get("changed") is True, "receipt does not represent an implementation change")
    _require(implementation.get("head_sha") == expected_head_sha, "receipt head SHA mismatch")
    _require(implementation.get("branch") == expected_head_ref, "receipt branch mismatch")
    incident_id = str(implementation.get("incident_id") or "")
    _require(bool(incident_id), "receipt incident id is missing")

    handoff = receipt.get("handoff") or {}
    _require((handoff.get("lead_dispatch") or {}).get("decision") == "REPAIR", "lead dispatch did not authorize repair")

    triage = handoff.get("triage") or {}
    _require(triage.get("repairable") is True, "triage did not mark the incident repairable")
    _require(str(triage.get("incident_id") or "") == incident_id, "triage incident id mismatch")

    engineering = handoff.get("engineering") or {}
    _require(engineering.get("changed") is True, "engineering receipt does not contain a change")
    _require(str(engineering.get("incident_id") or "") == incident_id, "engineering incident id mismatch")

    review = handoff.get("independent_review") or {}
    _require(review.get("decision") == "PASS", "independent review did not pass")

    architect = handoff.get("system_architect") or {}
    _require(
        architect.get("decision") in ALLOWED_ARCHITECT_DECISIONS,
        "system architect decision is neither PASS nor NOT_APPLICABLE",
    )

    regression = handoff.get("deterministic_regression") or {}
    _require(str(regression.get("status")) == "0", "deterministic regression did not pass")

    qa = handoff.get("qa") or {}
    _require(qa.get("decision") == "PASS", "QA verification did not pass")

    _require(receipt.get("governance_complete") is True, "receipt is not governance-complete")
    _require(receipt.get("terminal_authority") == TERMINAL_AUTHORITY, "terminal authority mismatch")
    _require(receipt.get("can_execute") is False, "can_execute invariant violated")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--workflow-run-id")
    args = parser.parse_args()

    receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    try:
        verify_receipt(
            receipt,
            expected_repository=args.repository,
            expected_head_sha=args.head_sha,
            expected_head_ref=args.head_ref,
            expected_workflow_run_id=args.workflow_run_id,
        )
    except GovernanceReceiptError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 1

    print(
        json.dumps(
            {
                "status": "PASS",
                "head_sha": args.head_sha,
                "head_ref": args.head_ref,
                "can_execute": False,
                "terminal_authority": TERMINAL_AUTHORITY,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
