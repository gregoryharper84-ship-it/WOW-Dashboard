"""Deterministic terminal-closure decision logic for merged WOW engineering PRs.

This module does not deploy code or alter sporting probability behavior. It
consumes GitHub evidence collected by the workflow and decides whether a merged
PR is ready for a terminal receipt, needs another verification action, or must
remain blocked with an exact reason.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

try:
    from v17.receipt_schema import CONTRACT_VERSION, VerificationReceipt, validate_terminal_receipt
except ImportError:  # pragma: no cover - direct script execution
    from receipt_schema import CONTRACT_VERSION, VerificationReceipt, validate_terminal_receipt

CAN_EXECUTE = False
TERMINAL_RECEIPT_HEADING = "### Terminal Verification Receipt"
BLOCKER_MARKER = "<!-- WOW_TERMINAL_CLOSURE_BLOCKER -->"
AUTONOMOUS_MARKER = "Terminal-Closure-Autonomous: true"
LEGACY_AUTONOMOUS_MARKER = "Morning-Green-Autonomous: true"

_WORKER_INCIDENT_RE = re.compile(
    r"(?im)^Incident:\s*`?#?(?P<number>\d+)`?\s*$"
)

_ISSUE_RE = re.compile(r"(?im)^Terminal-Issue:\s*#(?P<number>\d+)\s*$")
_FALLBACK_ISSUE_RE = re.compile(
    r"(?im)\b(?:fixes|closes|resolves|continues)\s+#(?P<number>\d+)\b"
)
_ACCEPTANCE_RE = re.compile(
    r"(?im)^Morning-Green-Acceptance-Workflow:\s*(?P<workflow>[^\s]+)\s*$"
)
_JSON_BLOCK_RE = re.compile(r"~~~json\s*(\{.*?\})\s*~~~", re.DOTALL)

# Some acceptance workflows intentionally run lightweight contract-only jobs on
# push/PR events and reserve the live acceptance path for a narrower event set.
# A green contract-only run must never be promoted into a terminal receipt.
_LIVE_ACCEPTANCE_EVENTS: dict[str, frozenset[str]] = {
    "wow-v17-nightly-multiscout.yml": frozenset({"workflow_run", "workflow_dispatch", "schedule"}),
}


def _iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metadata(body: str) -> dict[str, Any]:
    issue_match = (
        _ISSUE_RE.search(body)
        or _WORKER_INCIDENT_RE.search(body)
        or _FALLBACK_ISSUE_RE.search(body)
    )
    acceptance_match = _ACCEPTANCE_RE.search(body)
    reliability_match = _RELIABILITY_RE.search(body)
    return {
        "autonomous": (
            AUTONOMOUS_MARKER in body
            or LEGACY_AUTONOMOUS_MARKER in body
        ),
        "issue_number": int(issue_match.group("number")) if issue_match else None,
        "acceptance_workflow": (
            acceptance_match.group("workflow").strip()
            if acceptance_match
            else "none"
        ),
        "reliability_receipt_version": (
            reliability_match.group("version").strip()
            if reliability_match
            else None
        ),
    }


TRUSTED_COMMENT_AUTHORS = frozenset({"github-actions[bot]"})


def _trusted_comment(item: dict[str, Any]) -> bool:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return str(user.get("login") or "").strip().lower() in TRUSTED_COMMENT_AUTHORS


def _has_terminal_receipt(
    comments: list[dict[str, Any]],
    merge_sha: str,
) -> bool:
    exact_commit = f"- **Commit SHA:** `{merge_sha}`"
    for item in comments:
        if not isinstance(item, dict) or not _trusted_comment(item):
            continue
        body = str(item.get("body") or "")
        if (
            TERMINAL_RECEIPT_HEADING in body
            and "Status:" in body
            and "FIXED_AND_VERIFIED" in body
            and exact_commit in body
        ):
            return True
    return False


def _release_payloads(
    comments: list[dict[str, Any]],
    merge_sha: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    exact_merge_marker = f"- protected_main_merge_sha: `{merge_sha}`"
    for item in comments:
        if not isinstance(item, dict) or not _trusted_comment(item):
            continue
        body = str(item.get("body") or "")
        if "## Release / Production Verification Agent" not in body:
            continue
        if exact_merge_marker not in body:
            continue
        for match in _JSON_BLOCK_RE.finditer(body):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append(payload)
    return out


def _release_verified(
    comments: list[dict[str, Any]],
    merge_sha: str,
) -> tuple[bool, dict[str, Any] | None]:
    for payload in reversed(_release_payloads(comments, merge_sha)):
        if str(payload.get("status") or "") != "PRODUCTION_VERIFIED":
            continue
        if str(payload.get("production_sha") or "").strip() != merge_sha:
            continue
        return True, payload
    return False, None


def _machine_receipt_status(
    state: dict[str, Any],
    *,
    issue_number: int | None,
    pr_number: int | None,
    pr_head_sha: str,
    merge_sha: str,
    reliability_version: str | None,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    if reliability_version is None:
        return True, [], None
    if reliability_version != CONTRACT_VERSION:
        return False, [f"UNSUPPORTED_RELIABILITY_RECEIPT_VERSION:{reliability_version}"], None
    if issue_number is None or pr_number is None or not pr_head_sha or not merge_sha:
        return False, ["RECEIPT_IDENTITY_INCOMPLETE"], None
    raw_receipts = state.get("verification_receipts")
    if not isinstance(raw_receipts, list) or not raw_receipts:
        return False, ["RECEIPT_MISSING"], None
    observed_errors: list[str] = []
    for raw in reversed(raw_receipts):
        if not isinstance(raw, dict):
            observed_errors.append("RECEIPT_NOT_OBJECT")
            continue
        try:
            receipt = VerificationReceipt.model_validate(raw)
        except Exception as exc:
            observed_errors.append(f"SCHEMA_PARSE_FAILED:{type(exc).__name__}")
            continue
        errors = validate_terminal_receipt(
            receipt,
            expected_issue_id=issue_number,
            expected_pr_number=pr_number,
            expected_pr_head_sha=pr_head_sha,
            expected_merge_sha=merge_sha,
        )
        if not errors:
            return True, [], receipt.model_dump(mode="json")
        observed_errors.extend(errors)
    return False, observed_errors or ["RECEIPT_INVALID"], None


def _run_name(run: dict[str, Any]) -> str:
    return str(run.get("name") or run.get("workflow_name") or "")


def _exact_acceptance_run(
    runs: list[dict[str, Any]],
    workflow: str,
    merge_sha: str,
) -> dict[str, Any] | None:
    if workflow == "none":
        return {
            "id": None,
            "html_url": None,
            "name": "none",
            "head_sha": merge_sha,
            "status": "completed",
            "conclusion": "success",
        }
    target_name = workflow[:-4] if workflow.endswith(".yml") else workflow
    for run in runs:
        path = str(run.get("path") or "")
        name = _run_name(run)
        path_match = path.endswith("/" + workflow) or path == workflow
        name_match = name == target_name or name == workflow
        if not (path_match or name_match):
            continue
        if str(run.get("head_sha") or "") != merge_sha:
            continue
        allowed_events = _LIVE_ACCEPTANCE_EVENTS.get(workflow)
        if allowed_events is not None and str(run.get("event") or "") not in allowed_events:
            continue
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            return run
    return None


def _has_inflight_run(runs: list[dict[str, Any]], workflow_name: str) -> bool:
    for run in runs:
        if workflow_name and workflow_name not in {_run_name(run), str(run.get("path") or "").split("/")[-1]}:
            continue
        if str(run.get("status") or "") in {"queued", "in_progress", "waiting", "pending"}:
            return True
    return False


def _latest_release_age_minutes(
    runs: list[dict[str, Any]],
    now: datetime,
) -> float | None:
    times: list[datetime] = []
    for run in runs:
        if _run_name(run) != "wow-v17-release-production-verification-agent":
            continue
        stamp = _iso(run.get("updated_at") or run.get("created_at"))
        if stamp:
            times.append(stamp)
    if not times:
        return None
    return max(0.0, (now - max(times)).total_seconds() / 60.0)


def _receipt(
    *,
    issue_number: int | None,
    merge_sha: str,
    acceptance_workflow: str,
    acceptance_run: dict[str, Any],
    release_payload: dict[str, Any],
    release_run: dict[str, Any] | None,
) -> str:
    acceptance_id = acceptance_run.get("id") or "N/A"
    acceptance_url = acceptance_run.get("html_url") or "N/A"
    release_url = (release_run or {}).get("html_url") or "N/A"
    production_sha = release_payload.get("production_sha") or "VERIFIED_BY_RELEASE_AGENT"
    lines = [
        TERMINAL_RECEIPT_HEADING,
        f"- **Issue:** #{issue_number}" if issue_number else "- **Issue:** not linked",
        f"- **Commit SHA:** `{merge_sha}`",
        f"- **Production SHA:** `{production_sha}`",
        f"- **Acceptance Workflow:** `{acceptance_workflow}`",
        f"- **Canary / Acceptance Run ID:** `{acceptance_id}`",
        f"- **Acceptance Evidence:** {acceptance_url}",
        f"- **Observability / Release Evidence:** {release_url}",
        "- **Saturation / Error Delta:** N/A for generic closure; domain metrics remain in acceptance/release artifacts",
        "- **Terminal Authority:** `V17_TERMINAL_REDUCER`",
        "- **can_execute:** `false`",
        "- **Status:** **FIXED_AND_VERIFIED**",
    ]
    return "\n".join(lines)


def evaluate(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pr = state.get("pr") if isinstance(state.get("pr"), dict) else {}
    body = str(pr.get("body") or "")
    meta = _metadata(body)
    merge_sha = str(pr.get("merge_commit_sha") or "").strip()
    pr_number = int(pr.get("number")) if str(pr.get("number") or "").isdigit() else None
    pr_head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    pr_head_sha = str(pr_head.get("sha") or pr.get("head_sha") or "").strip().lower()
    merged_at = _iso(pr.get("merged_at"))
    pr_comments = state.get("pr_comments") if isinstance(state.get("pr_comments"), list) else []
    issue_comments = state.get("issue_comments") if isinstance(state.get("issue_comments"), list) else []
    runs = state.get("runs") if isinstance(state.get("runs"), list) else []
    current_main_sha = str(state.get("current_main_sha") or "").strip()

    base = {
        "autonomous": meta["autonomous"],
        "issue_number": meta["issue_number"],
        "acceptance_workflow": meta["acceptance_workflow"],
        "reliability_receipt_version": meta["reliability_receipt_version"],
        "pr_number": pr_number,
        "pr_head_sha": pr_head_sha,
        "merge_sha": merge_sha,
        "can_execute": False,
    }
    if not meta["autonomous"]:
        return {**base, "status": "SKIP", "reason": "TERMINAL_CLOSURE_NOT_OPTED_IN"}
    if not merge_sha or merged_at is None:
        return {**base, "status": "BLOCKED_WITH_EXACT_REASON", "blockers": ["MERGE_IDENTITY_INCOMPLETE"]}
    machine_receipt_ok, machine_receipt_errors, machine_receipt = _machine_receipt_status(
        state,
        issue_number=meta["issue_number"],
        pr_number=pr_number,
        pr_head_sha=pr_head_sha,
        merge_sha=merge_sha,
        reliability_version=meta["reliability_receipt_version"],
    )
    existing_terminal_receipt = (
        _has_terminal_receipt(issue_comments, merge_sha)
        or _has_terminal_receipt(pr_comments, merge_sha)
    )
    if existing_terminal_receipt and machine_receipt_ok:
        return {
            **base,
            "status": "FIXED_AND_VERIFIED",
            "reason": "TERMINAL_RECEIPT_ALREADY_PRESENT",
            "machine_receipt": machine_receipt,
        }

    release_ok, release_payload = _release_verified(pr_comments, merge_sha)
    acceptance = _exact_acceptance_run(runs, meta["acceptance_workflow"], merge_sha)

    release_run = None
    release_candidates = [
        run for run in runs
        if _run_name(run) == "wow-v17-release-production-verification-agent"
    ]
    if release_candidates:
        release_run = release_candidates[-1]

    if release_ok and acceptance is not None and release_payload is not None:
        return {
            **base,
            "status": "READY_FOR_RECEIPT",
            "receipt_markdown": _receipt(
                issue_number=meta["issue_number"],
                merge_sha=merge_sha,
                acceptance_workflow=meta["acceptance_workflow"],
                acceptance_run=acceptance,
                release_payload=release_payload,
                release_run=release_run,
            ),
        }

    blockers: list[str] = []
    if not machine_receipt_ok:
        blockers.append("INVALID_RECEIPT_SCHEMA")
    if not release_ok:
        blockers.append("PRODUCTION_VERIFICATION_RECEIPT_MISSING")
    if acceptance is None:
        blockers.append("EXACT_MERGE_ACCEPTANCE_RUN_MISSING")

    release_age = _latest_release_age_minutes(runs, now)
    dispatch_release = (
        not release_ok
        and not _has_inflight_run(runs, "wow-v17-release-production-verification-agent")
        and (release_age is None or release_age >= 5.0)
    )
    dispatch_acceptance = (
        acceptance is None
        and meta["acceptance_workflow"] != "none"
        and current_main_sha == merge_sha
        and not _has_inflight_run(runs, meta["acceptance_workflow"])
    )

    age_minutes = max(0.0, (now - merged_at).total_seconds() / 60.0)
    status = "WAITING_FOR_TERMINAL_RECEIPT"
    if age_minutes >= 120.0:
        status = "BLOCKED_WITH_EXACT_REASON"

    blocker_lines = [
        BLOCKER_MARKER,
        "### Terminal closure blocker",
        f"- **Merged SHA:** `{merge_sha}`",
        *[f"- **Blocker:** `{item}`" for item in blockers],
        f"- **Age since merge:** {age_minutes:.0f} minutes",
        "- **Status:** **BLOCKED_WITH_EXACT_REASON**",
        "- **can_execute:** `false`",
    ]

    return {
        **base,
        "status": status,
        "blockers": blockers,
        "receipt_errors": machine_receipt_errors,
        "age_minutes": age_minutes,
        "dispatch_release_verification": dispatch_release,
        "dispatch_acceptance": dispatch_acceptance,
        "blocker_markdown": "\n".join(blocker_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    result = evaluate(state)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("status", "issue_number", "acceptance_workflow", "can_execute")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
