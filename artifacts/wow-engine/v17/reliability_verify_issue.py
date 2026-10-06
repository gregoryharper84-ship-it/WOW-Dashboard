"""CLI for generating machine-verifiable WOW reliability receipts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

from v17.receipt_schema import (
    CONTRACT_VERSION,
    VerificationReceipt,
    expected_sentinel_signature,
    response_digest,
    schema_hash,
    validate_terminal_receipt,
)


def _headers(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip().lower()] = value.strip()
    return out


def _environment_signature() -> str:
    values = {
        "python": sys.version,
        "platform": platform.platform(),
        "github_workflow": os.getenv("GITHUB_WORKFLOW", ""),
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "github_sha": os.getenv("GITHUB_SHA", ""),
        "runner_os": os.getenv("RUNNER_OS", ""),
    }
    raw = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--pr-head-sha", required=True)
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--deployed-sha", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--http-status", type=int, required=True)
    parser.add_argument("--headers", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--trace-out", type=Path, required=True)
    args = parser.parse_args()

    headers = _headers(args.headers)
    trace = {
        "contract_version": CONTRACT_VERSION,
        "issue_id": args.issue,
        "pr_number": args.pr,
        "exact_head_sha": args.pr_head_sha.lower(),
        "merge_sha": args.merge_sha.lower(),
        "deployed_render_sha": args.deployed_sha.lower(),
        "method": args.method.upper(),
        "route_tested": args.route,
        "workflow": args.workflow,
        "workflow_run_id": args.workflow_run_id,
        "runtime_environment_signature": _environment_signature(),
        "executed_utc": datetime.now(timezone.utc).isoformat(),
        "can_execute": False,
    }
    trace_bytes = (json.dumps(trace, sort_keys=True, separators=(",", ":")) + "\n").encode()
    args.trace_out.parent.mkdir(parents=True, exist_ok=True)
    args.trace_out.write_bytes(trace_bytes)
    trace_digest = hashlib.sha256(trace_bytes).hexdigest()

    provisional = {
        "contract_version": CONTRACT_VERSION,
        "issue_id": args.issue,
        "pr_number": args.pr,
        "exact_head_sha": args.pr_head_sha.lower(),
        "merge_sha": args.merge_sha.lower(),
        "deployed_render_sha": args.deployed_sha.lower(),
        "method": args.method.upper(),
        "route_tested": args.route,
        "schema_hash": schema_hash(),
        "http_status": args.http_status,
        "dry_run_header_present": headers.get("x-wow-dry-run-only", "").lower() == "true",
        "can_execute_header_false": headers.get("x-wow-can-execute", "").lower() == "false",
        "raw_response_digest": response_digest(args.response.read_bytes()),
        "execution_trace_digest": trace_digest,
        "timestamp_utc": datetime.now(timezone.utc),
        "sentinel_workflow": args.workflow,
        "sentinel_workflow_run_id": args.workflow_run_id,
        "sentinel_signature": "sha256:" + ("0" * 64),
        "audit_artifact_name": args.artifact_name,
    }
    unsigned = VerificationReceipt.model_validate(provisional)
    provisional["sentinel_signature"] = expected_sentinel_signature(unsigned)
    receipt = VerificationReceipt.model_validate(provisional)
    errors = validate_terminal_receipt(
        receipt,
        expected_issue_id=args.issue,
        expected_pr_number=args.pr,
        expected_pr_head_sha=args.pr_head_sha,
        expected_merge_sha=args.merge_sha,
    )
    if errors:
        raise SystemExit("INVALID_RECEIPT_SCHEMA:" + ",".join(errors))

    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(
        receipt.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "VERIFICATION_RECEIPT_VALID",
        "receipt": str(args.receipt_out),
        "trace": str(args.trace_out),
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
