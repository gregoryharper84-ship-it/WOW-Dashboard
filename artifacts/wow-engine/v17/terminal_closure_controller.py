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

CAN_EXECUTE = False
TERMINAL_RECEIPT_HEADING = "### Terminal Verification Receipt"
BLOCKER_MARKER = "<!-- WOW_TERMINAL_CLOSURE_BLOCKER -->"
AUTONOMOUS_MARKER = "Terminal-Closure-Autonomous: true"

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
    issue_match = _ISSUE_RE.search(body) or _FALLBACK_ISSUE_RE.search(body)
    acceptance_match = _ACCEPTANCE_RE.search(body)
    return {
        "autonomous": AUTONOMOUS_MARKER in body,
        "issue_number": int(issue_match.group("number")) if issue_match else None,
        "acceptance_workflow": (
            acceptance_match.group("workflow").strip()
            if acceptance_match
            else "none"
        ),
    }


def _comment_bodies(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("body") or "") for item in items if isinstance(item, dict)]


def _has_terminal_receipt(comments: list[dict[str, Any]]) -> bool:
    for body in _comment_bodies(comments):
        if (
            TERMINAL_RECEIPT_HEADING in body
            and "Status:" in body
            and "FIXED_AND_VERIFIED" in body
        ):
            return True
    return False


def _release_payloads(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for body in _comment_bodies(comments):
        if "## Release / Production Verification Agent" not in body:
            continue
        for match in _JSON_BLOCK_RE.finditer(body):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append(payload)
    return out


def _release_verified(comments: list[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
    for payload in reversed(_release_payloads(comments)):
        if str(payload.get("status") or "") == "PRODUCTION_VERIFIED":
            return True, payload
    return False, None


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
    merged_at = _iso(pr.get("merged_at"))
    pr_comments = state.get("pr_comments") if isinstance(state.get("pr_comments"), list) else []
    issue_comments = state.get("issue_comments") if isinstance(state.get("issue_comments"), list) else []
    runs = state.get("runs") if isinstance(state.get("runs"), list) else []
    current_main_sha = str(state.get("current_main_sha") or "").strip()

    base = {
        "autonomous": meta["autonomous"],
        "issue_number": meta["issue_number"],
        "acceptance_workflow": meta["acceptance_workflow"],
        "merge_sha": merge_sha,
        "can_execute": False,
    }
    if not meta["autonomous"]:
        return {**base, "status": "SKIP", "reason": "TERMINAL_CLOSURE_NOT_OPTED_IN"}
    if not merge_sha or merged_at is None:
        return {**base, "status": "BLOCKED_WITH_EXACT_REASON", "blockers": ["MERGE_IDENTITY_INCOMPLETE"]}
    if _has_terminal_receipt(issue_comments) or _has_terminal_receipt(pr_comments):
        return {**base, "status": "FIXED_AND_VERIFIED", "reason": "TERMINAL_RECEIPT_ALREADY_PRESENT"}

    release_ok, release_payload = _release_verified(pr_comments)
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
