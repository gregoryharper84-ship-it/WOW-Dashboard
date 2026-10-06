#!/usr/bin/env python3
"""Audit and safely liquidate open PR WIP for WOW V17.

Non-destructive by default. This tool classifies open PRs targeting main and
emits proof-gated recommended actions. It does NOT close, merge, rebase, or
enable auto-merge.

Safety rules:
- age alone never proves supersession;
- pending checks are not passing;
- failing CI does not justify closure if the PR is still relevant;
- only explicit supersession evidence may justify a close recommendation;
- merge-ready means eligible for the global mutation/promotion queue, not an
  instruction to bypass branch protection or the single-writer lease.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

DEFAULT_REPO = "gregoryharper84-ship-it/WOW-Dashboard"
DEFAULT_BASE = "main"
DEFAULT_WIP_LIMIT = 12
TERMINAL_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}
FAILURE_CHECK_CONCLUSIONS = {
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "startup_failure",
}
TRUSTED_GOVERNANCE_NAMES = {
    "Existing PR trusted governance certification",
    "Trusted exact-head engineering governance",
}
SUPERCESSION_MARKERS = (
    "superseded-by:",
    "superseded by:",
    "duplicate-of:",
    "duplicate of:",
)


class GitHubAuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class PRState:
    number: int
    title: str
    author: str
    head_ref: str
    head_sha: str
    base_ref: str
    draft: bool
    updated_at: str
    days_since_update: int
    mergeable: bool | None
    mergeable_state: str
    check_total: int
    check_success: int
    check_pending: int
    check_failed: tuple[str, ...]
    governance_failed: tuple[str, ...]
    statuses_pending: bool
    statuses_failed: bool
    explicit_supersession_refs: tuple[int, ...]
    explicit_supersession_proven: bool

    @property
    def deterministic_failed(self) -> tuple[str, ...]:
        return tuple(name for name in self.check_failed if name not in TRUSTED_GOVERNANCE_NAMES)


class GitHubClient:
    def __init__(self, repo: str, token: str | None) -> None:
        self.repo = repo
        self.token = token

    def get(self, endpoint: str) -> Any:
        url = f"https://api.github.com{endpoint}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "WOW-PR-Liquidation-Audit/2.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubAuditError(f"GitHub GET {endpoint} failed: HTTP {exc.code}: {body[:1000]}") from exc

    def paginate(self, endpoint: str) -> list[Any]:
        rows: list[Any] = []
        page = 1
        sep = "&" if "?" in endpoint else "?"
        while True:
            data = self.get(f"{endpoint}{sep}per_page=100&page={page}")
            if not isinstance(data, list):
                raise GitHubAuditError(f"Expected list response for {endpoint}, got {type(data).__name__}")
            rows.extend(data)
            if len(data) < 100:
                break
            page += 1
        return rows

    def fetch_open_prs(self, base: str) -> list[dict[str, Any]]:
        base_q = urllib.parse.quote(base, safe="")
        return self.paginate(f"/repos/{self.repo}/pulls?state=open&base={base_q}")

    def fetch_pr(self, number: int) -> dict[str, Any]:
        data = self.get(f"/repos/{self.repo}/pulls/{number}")
        if not isinstance(data, dict):
            raise GitHubAuditError(f"Unexpected PR payload for #{number}")
        return data

    def fetch_check_runs(self, sha: str) -> list[dict[str, Any]]:
        payload = self.get(f"/repos/{self.repo}/commits/{sha}/check-runs?per_page=100")
        rows = payload.get("check_runs", []) if isinstance(payload, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def fetch_combined_status(self, sha: str) -> str:
        payload = self.get(f"/repos/{self.repo}/commits/{sha}/status")
        return str(payload.get("state") or "unknown").lower() if isinstance(payload, dict) else "unknown"

    def issue_or_pr_state(self, number: int) -> str | None:
        payload = self.get(f"/repos/{self.repo}/issues/{number}")
        if not isinstance(payload, dict):
            return None
        return str(payload.get("state") or "").lower() or None


def _age_days(updated_at: str, now: datetime) -> int:
    stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    return max(0, (now - stamp).days)


def _supersession_refs(title: str, body: str) -> tuple[int, ...]:
    text = f"{title}\n{body or ''}".lower()
    refs: list[int] = []
    for marker in SUPERCESSION_MARKERS:
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx < 0:
                break
            frag = text[idx : idx + 120]
            refs.extend(int(m.group(1)) for m in re.finditer(r"#(\d+)", frag))
            start = idx + len(marker)
    return tuple(dict.fromkeys(refs))


def _check_summary(check_runs: Iterable[dict[str, Any]]) -> tuple[int, int, int, tuple[str, ...], tuple[str, ...]]:
    total = success = pending = 0
    failed: list[str] = []
    governance_failed: list[str] = []
    for run in check_runs:
        total += 1
        name = str(run.get("name") or "unnamed")
        status = str(run.get("status") or "").lower()
        conclusion = run.get("conclusion")
        conclusion_s = str(conclusion).lower() if conclusion is not None else ""
        if status != "completed" or not conclusion_s:
            pending += 1
        elif conclusion_s in TERMINAL_CHECK_CONCLUSIONS:
            success += 1
        else:
            failed.append(name)
            if name in TRUSTED_GOVERNANCE_NAMES:
                governance_failed.append(name)
    return total, success, pending, tuple(failed), tuple(governance_failed)


def inspect_pr(client: GitHubClient, pr: dict[str, Any], now: datetime) -> PRState:
    number = int(pr["number"])
    detail = client.fetch_pr(number)
    head_sha = str(detail["head"]["sha"])
    checks = client.fetch_check_runs(head_sha)
    total, success, pending, failed, governance_failed = _check_summary(checks)
    combined_status = client.fetch_combined_status(head_sha)

    refs = _supersession_refs(str(detail.get("title") or ""), str(detail.get("body") or ""))
    proven = False
    for ref in refs:
        try:
            state = client.issue_or_pr_state(ref)
        except GitHubAuditError:
            state = None
        if state == "closed":
            proven = True
            break

    return PRState(
        number=number,
        title=str(detail.get("title") or ""),
        author=str((detail.get("user") or {}).get("login") or ""),
        head_ref=str((detail.get("head") or {}).get("ref") or ""),
        head_sha=head_sha,
        base_ref=str((detail.get("base") or {}).get("ref") or ""),
        draft=bool(detail.get("draft")),
        updated_at=str(detail.get("updated_at") or ""),
        days_since_update=_age_days(str(detail.get("updated_at") or ""), now),
        mergeable=detail.get("mergeable"),
        mergeable_state=str(detail.get("mergeable_state") or "unknown").lower(),
        check_total=total,
        check_success=success,
        check_pending=pending,
        check_failed=failed,
        governance_failed=governance_failed,
        statuses_pending=combined_status == "pending",
        statuses_failed=combined_status in {"failure", "error"},
        explicit_supersession_refs=refs,
        explicit_supersession_proven=proven,
    )


def classify(state: PRState) -> tuple[str, str]:
    if state.explicit_supersession_proven:
        refs = ",".join(f"#{n}" for n in state.explicit_supersession_refs)
        return "PROVEN_SUPERSEDED", f"Explicit supersession marker points to closed canonical target(s): {refs}"

    if state.draft:
        return "WIP_PAUSED", "Draft PR; preserve commits and keep out of promotion queue."

    if state.mergeable is False or state.mergeable_state == "dirty":
        return "REBASE_REQUIRED", "Merge conflict/dirty head; refresh only if the work is still relevant."

    if state.deterministic_failed or state.statuses_failed:
        failures = ", ".join(state.deterministic_failed) or "combined commit status"
        return "CI_REPAIR_REQUIRED", f"Deterministic CI/status failure requires repair and is not closure proof: {failures}"

    if state.governance_failed and not state.deterministic_failed and state.check_pending == 0:
        return "GOVERNANCE_RECERTIFY", "Code checks are otherwise terminal; exact-head trusted governance must be refreshed."

    if state.check_pending > 0 or state.statuses_pending:
        return "CI_IN_PROGRESS", "One or more checks/statuses are still pending; not merge-ready."

    if state.check_total == 0:
        return "CI_NOT_TRIGGERED", "No check runs exist for exact head; trigger/repair CI before promotion."

    if state.mergeable_state == "behind":
        return "REBASE_REQUIRED", "Head is behind current base; restack before protected promotion."

    if state.mergeable in {True, None} and state.mergeable_state in {"clean", "unstable", "has_hooks", "blocked", "unknown"}:
        if state.mergeable_state in {"blocked", "unstable", "has_hooks"}:
            return "PROMOTION_GATED", "Deterministic checks are terminal but branch protection/governance still gates promotion."
        return "MERGE_CANDIDATE", "Eligible for global mutation/promotion owner review; do not auto-merge."

    return "MANUAL_REVIEW", f"Unrecognized merge state: {state.mergeable_state}"


def build_receipt(states: list[PRState], wip_limit: int, now: datetime) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for state in states:
        bucket, reason = classify(state)
        buckets.setdefault(bucket, []).append(
            {
                "pr_number": state.number,
                "title": state.title,
                "author": state.author,
                "head_ref": state.head_ref,
                "head_sha": state.head_sha,
                "mergeable": state.mergeable,
                "mergeable_state": state.mergeable_state,
                "days_since_update": state.days_since_update,
                "check_total": state.check_total,
                "check_success": state.check_success,
                "check_pending": state.check_pending,
                "check_failed": list(state.check_failed),
                "explicit_supersession_refs": list(state.explicit_supersession_refs),
                "reason": reason,
            }
        )

    total = len(states)
    return {
        "timestamp_utc": now.isoformat(),
        "total_open_prs_targeting_main": total,
        "wip_limit": wip_limit,
        "wip_exceeded": total > wip_limit,
        "excess_pr_reduction_target": max(0, total - wip_limit),
        "new_pr_creation_allowed_for_support_lanes": total < wip_limit,
        "buckets": buckets,
        "safe_action_policy": {
            "PROVEN_SUPERSEDED": "May be closed only with durable supersession comment citing exact target.",
            "REBASE_REQUIRED": "Do not close automatically; prove relevance, then restack or supersede.",
            "CI_REPAIR_REQUIRED": "Repair failing CI if relevant; failure alone is not closure proof.",
            "GOVERNANCE_RECERTIFY": "Refresh trusted exact-head governance; do not reopen root-cause work.",
            "CI_IN_PROGRESS": "Wait/observe exact-head checks; pending is not passing.",
            "CI_NOT_TRIGGERED": "Trigger or repair CI for exact head.",
            "MERGE_CANDIDATE": "Queue behind the single global promotion owner; no auto-merge.",
            "PROMOTION_GATED": "Resolve branch-protection/governance gate through normal protected flow.",
            "WIP_PAUSED": "Keep draft/paused; no new PR while WIP limit is exceeded.",
        },
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--wip-limit", type=int, default=DEFAULT_WIP_LIMIT)
    parser.add_argument("--output")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN or GH_TOKEN is required.", file=sys.stderr)
        return 2
    if args.wip_limit < 1:
        print("ERROR: --wip-limit must be >= 1", file=sys.stderr)
        return 2

    client = GitHubClient(args.repo, token)
    now = datetime.now(timezone.utc)
    prs = client.fetch_open_prs(args.base)
    states = [inspect_pr(client, pr, now) for pr in prs]
    receipt = build_receipt(states, args.wip_limit, now)
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
