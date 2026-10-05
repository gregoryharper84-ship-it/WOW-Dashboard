"""Bounded terminal acceptance for the durable Scout specialist handoff.

Polls one already-enqueued Scout source run until its durable ledger is terminal,
then verifies row accounting and evidence-only queue governance. This module
never produces sporting probability and never grants execution authority.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc

CAN_EXECUTE = False
ACTION_ORIGIN = os.environ.get(
    "WOW_ACTION_ORIGIN",
    "https://wow-governed-probability-engine.onrender.com",
).rstrip("/")
FORBIDDEN_PROBABILITY_FIELDS = frozenset({
    "model_probability",
    "specialist_probability",
    "calibrated_probability",
    "calibrated_lower_bound",
    "calibrated_upper_bound",
    "probability_publishable",
})


def _timeout_seconds() -> int:
    raw = os.getenv("WOW_SCOUT_TERMINAL_ACCEPTANCE_SECONDS", "900")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 900
    return max(30, min(value, 1800))


def _get_status(
    origin: str,
    source_run_id: str,
    token: str,
    *,
    include_receipts: bool = False,
) -> dict[str, Any]:
    suffix = "?include_receipts=true" if include_receipts else ""
    path = f"/v17/scout-handoff-runs/{quote(source_run_id, safe='')}{suffix}"
    request = Request(
        f"{origin.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {}
        return {
            "ok": False,
            "http_status": exc.code,
            "body": detail,
            "can_execute": False,
        }
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "code": "SCOUT_TERMINAL_ACCEPTANCE_TRANSPORT_FAILED",
            "error_type": type(exc).__name__,
            "can_execute": False,
        }
    return {"ok": True, "http_status": 200, "body": body, "can_execute": False}


def _queue_governance_blockers(summary: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    jobs = summary.get("jobs")
    if not isinstance(jobs, list):
        return ["SCOUT_TERMINAL_ACCEPTANCE_JOB_RECEIPTS_MISSING"]

    for job in jobs:
        if not isinstance(job, dict):
            blockers.append("SCOUT_TERMINAL_ACCEPTANCE_JOB_RECEIPT_INVALID")
            continue
        if job.get("can_execute") is not False:
            blockers.append("SCOUT_EXECUTION_GOVERNANCE_VIOLATION")
        payload = job.get("request_payload")
        if not isinstance(payload, dict):
            blockers.append("SCOUT_HANDOFF_PAYLOAD_INVALID")
            continue
        forbidden = sorted(FORBIDDEN_PROBABILITY_FIELDS.intersection(payload))
        if forbidden:
            blockers.append(
                "SCOUT_PROBABILITY_AUTHORITY_VIOLATION:" + ",".join(forbidden)
            )

    events = summary.get("state_events")
    if not isinstance(events, list):
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_STATE_EVENTS_MISSING")
    else:
        for event in events:
            if isinstance(event, dict) and event.get("can_execute") is True:
                blockers.append("SCOUT_EXECUTION_GOVERNANCE_VIOLATION")

    return list(dict.fromkeys(blockers))


def validate_terminal_summary(summary: dict[str, Any]) -> dict[str, Any]:
    rows_in = int(summary.get("rows_in") or 0)
    rows_completed = int(summary.get("rows_completed") or 0)
    rows_held = int(summary.get("rows_held") or 0)
    rows_rejected = int(summary.get("rows_rejected") or 0)
    candidate_jobs = int(summary.get("candidate_jobs") or 0)
    processing_seen = int(summary.get("specialist_processing_seen") or 0)
    blockers = _queue_governance_blockers(summary)

    if summary.get("can_execute") is not False:
        blockers.append("SCOUT_EXECUTION_GOVERNANCE_VIOLATION")
    if str(summary.get("status") or "") != "COMPLETE":
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_RUN_NOT_COMPLETE")
    if candidate_jobs <= 0 or rows_in <= 0:
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_NO_CANDIDATES")
    if candidate_jobs != rows_in:
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_CANDIDATE_COUNT_MISMATCH")
    if rows_in != rows_completed + rows_held + rows_rejected:
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_ROW_ACCOUNTING_MISMATCH")
    if summary.get("row_accounting_pass") is not True:
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_ROW_ACCOUNTING_NOT_PROVEN")
    if summary.get("reconciliation_pass") is not True or rows_held != 0:
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_RECONCILIATION_NOT_PROVEN")
    if processing_seen <= 0:
        blockers.append("SCOUT_TERMINAL_ACCEPTANCE_WORKER_PATH_NOT_PROVEN")

    blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": "wow.v17.scout-handoff-terminal-acceptance.v1",
        "status": "PASS" if not blockers else "BLOCKED_WITH_EXACT_REASON",
        "source_run_id": summary.get("source_run_id"),
        "research_run_id": summary.get("research_run_id"),
        "rows_in": rows_in,
        "rows_completed": rows_completed,
        "rows_held": rows_held,
        "rows_rejected": rows_rejected,
        "specialist_processing_seen": processing_seen,
        "row_accounting_pass": summary.get("row_accounting_pass") is True,
        "reconciliation_pass": summary.get("reconciliation_pass") is True,
        "terminal_code_counts": summary.get("terminal_code_counts") or {},
        "blockers": blockers,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def wait_for_terminal(
    *,
    origin: str,
    source_run_id: str,
    token: str,
    timeout_seconds: int,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
    ledger_output: Path | None = None,
) -> dict[str, Any]:
    deadline = monotonic_fn() + timeout_seconds
    latest: dict[str, Any] | None = None
    current_token = token

    while monotonic_fn() < deadline:
        receipt = _get_status(origin, source_run_id, current_token, include_receipts=False)
        if receipt.get("http_status") == 401:
            try:
                current_token = mint_github_actions_oidc(force=True)
            except GitHubOIDCMintError:
                return {
                    "schema_version": "wow.v17.scout-handoff-terminal-acceptance.v1",
                    "status": "BLOCKED_WITH_EXACT_REASON",
                    "source_run_id": source_run_id,
                    "blockers": ["GITHUB_OIDC_MINT_FAILED"],
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                }
            sleep_fn(1)
            continue

        body = receipt.get("body") if isinstance(receipt, dict) else None
        if isinstance(body, dict) and body.get("can_execute") is False:
            latest = body
            if body.get("status") == "COMPLETE":
                detailed = _get_status(
                    origin,
                    source_run_id,
                    current_token,
                    include_receipts=True,
                )
                detailed_body = detailed.get("body") if isinstance(detailed, dict) else None
                if not isinstance(detailed_body, dict) or detailed_body.get("can_execute") is not False:
                    return {
                        "schema_version": "wow.v17.scout-handoff-terminal-acceptance.v1",
                        "status": "BLOCKED_WITH_EXACT_REASON",
                        "source_run_id": source_run_id,
                        "blockers": ["SCOUT_TERMINAL_ACCEPTANCE_DETAILED_RECEIPT_FAILED"],
                        "terminal_authority": "V17_TERMINAL_REDUCER",
                        "can_execute": False,
                    }
                if ledger_output is not None:
                    ledger_output.parent.mkdir(parents=True, exist_ok=True)
                    ledger_output.write_text(
                        json.dumps(detailed_body, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                return validate_terminal_summary(detailed_body)
        sleep_fn(2)

    result = validate_terminal_summary(latest or {
        "source_run_id": source_run_id,
        "status": "IN_PROGRESS",
        "can_execute": False,
    })
    result["status"] = "BLOCKED_WITH_EXACT_REASON"
    if "SCOUT_TERMINAL_ACCEPTANCE_TIMEOUT" not in result["blockers"]:
        result["blockers"].append("SCOUT_TERMINAL_ACCEPTANCE_TIMEOUT")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--ledger-output",
        help="Optional path for the final detailed durable handoff ledger used by governed reporting.",
    )
    parser.add_argument("--origin", default=ACTION_ORIGIN)
    args = parser.parse_args()

    source = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    source_run_id = str(source.get("source_run_id") or "").strip()
    if not source_run_id:
        result = {
            "schema_version": "wow.v17.scout-handoff-terminal-acceptance.v1",
            "status": "BLOCKED_WITH_EXACT_REASON",
            "source_run_id": None,
            "blockers": ["SCOUT_TERMINAL_ACCEPTANCE_SOURCE_RUN_ID_MISSING"],
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }
    else:
        try:
            token = mint_github_actions_oidc()
        except GitHubOIDCMintError:
            result = {
                "schema_version": "wow.v17.scout-handoff-terminal-acceptance.v1",
                "status": "BLOCKED_WITH_EXACT_REASON",
                "source_run_id": source_run_id,
                "blockers": ["GITHUB_OIDC_MINT_FAILED"],
                "terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
            }
        else:
            result = wait_for_terminal(
                origin=args.origin,
                source_run_id=source_run_id,
                token=token,
                timeout_seconds=_timeout_seconds(),
                ledger_output=Path(args.ledger_output) if args.ledger_output else None,
            )

    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": result.get("status"),
        "source_run_id": result.get("source_run_id"),
        "rows_in": result.get("rows_in"),
        "rows_completed": result.get("rows_completed"),
        "rows_rejected": result.get("rows_rejected"),
        "blockers": result.get("blockers"),
        "can_execute": False,
    }))
    return 0 if result.get("status") == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
