"""Bounded exact-head QA/Release HOLD discovery for the protected-main worker.

Only reads GitHub. Never authenticates as QA or Release, publishes a check,
authors a PR, triggers a workflow, merges, or deploys.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

SHA = re.compile(r"^[a-f0-9]{40}$")
CODE = re.compile(r"`([A-Z][A-Z0-9_]*)`")
ROLES = {
    "WOW Independent QA exact-head": "qa",
    "WOW Release Authority exact-head": "release",
}


class HoldDiscoveryError(ValueError):
    pass


def select_hold(
    pulls: list[dict[str, Any]],
    checks_by_sha: dict[str, list[dict[str, Any]]],
    *,
    repository: str,
    qa_app_id: int,
    release_app_id: int,
    max_targets: int = 20,
) -> dict[str, Any]:
    if qa_app_id <= 0 or release_app_id <= 0 or qa_app_id == release_app_id:
        raise HoldDiscoveryError("ROLE_APP_IDS_UNBOUND_OR_NOT_DISTINCT")
    if not isinstance(pulls, list) or max_targets < 1:
        raise HoldDiscoveryError("PR_INVENTORY_INVALID")
    allowed = {ROLES["WOW Independent QA exact-head"]: qa_app_id,
               ROLES["WOW Release Authority exact-head"]: release_app_id}
    considered = 0
    for pr in pulls:
        if considered >= max_targets:
            break
        if not isinstance(pr, dict) or pr.get("state") != "open":
            continue
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        sha = head.get("sha")
        if (not isinstance(sha, str) or not SHA.fullmatch(sha)
                or (head.get("repo") or {}).get("full_name") != repository
                or base.get("ref") != "main"):
            continue
        considered += 1
        runs = checks_by_sha.get(sha)
        if not isinstance(runs, list):
            raise HoldDiscoveryError("CHECKS_UNREADABLE:" + sha)
        author = (pr.get("user") or {}).get("login")
        valid_failures = []
        for name, role in ROLES.items():
            source = [r for r in runs if isinstance(r, dict)
                      and r.get("name") == name
                      and r.get("head_sha") == sha
                      and (r.get("app") or {}).get("id") == allowed[role]]
            if not source:
                continue
            latest = max(source, key=lambda r: int(r.get("id") or 0))
            if latest.get("status") != "completed" or latest.get("conclusion") != "failure":
                continue
            summary = (latest.get("output") or {}).get("summary") or ""
            findings = CODE.findall(summary)
            if not findings:
                raise HoldDiscoveryError("ROLE_FAILURE_WITHOUT_TYPED_FINDINGS:" + name)
            valid_failures.append((int(latest.get("id") or 0), name, summary, findings))
        if not valid_failures:
            continue
        _, name, summary, findings = max(valid_failures, key=lambda x: x[0])
        # Never automatically repair owner-user or fork-authored PR code. Only
        # route an owner-authored protected-root PR to Engineering App re-proposal.
        if author != "wow-engineering[bot]":
            if author != repository.split("/")[0] or not any(
                f.endswith("TRUST_ROOT_OWNER_AUTHORED") for f in findings
            ):
                continue
        return {
            "eligible": True, "pr_number": pr["number"], "head_sha": sha,
            "author": author, "check_name": name, "summary": summary,
            "findings": findings, "scanned": considered,
            "requires_independent_review": True, "can_execute": False,
        }
    return {"eligible": False, "scanned": considered, "can_execute": False}


def fetch_json(repo: str, endpoint: str, token: str) -> Any:
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise HoldDiscoveryError("REPOSITORY_OR_READ_TOKEN_INVALID")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        raise HoldDiscoveryError("GITHUB_EVIDENCE_UNREADABLE") from exc


def discover(repo: str, token: str, qa_app_id: int, release_app_id: int) -> dict[str, Any]:
    pulls = fetch_json(repo, "pulls?state=open&base=main&per_page=100", token)
    if not isinstance(pulls, list):
        raise HoldDiscoveryError("PR_INVENTORY_INVALID")
    checks: dict[str, list[dict[str, Any]]] = {}
    for pr in pulls[:20]:
        sha = ((pr.get("head") or {}).get("sha") if isinstance(pr, dict) else None)
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            continue
        data = fetch_json(repo, f"commits/{sha}/check-runs?per_page=100", token)
        if not isinstance(data, dict) or not isinstance(data.get("check_runs"), list):
            raise HoldDiscoveryError("CHECKS_UNREADABLE:" + sha)
        if data.get("total_count", 0) > len(data["check_runs"]):
            raise HoldDiscoveryError("CHECKS_TRUNCATED:" + sha)
        checks[sha] = data["check_runs"]
    return select_hold(pulls, checks, repository=repo, qa_app_id=qa_app_id,
                       release_app_id=release_app_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--qa-app-id", required=True, type=int)
    parser.add_argument("--release-app-id", required=True, type=int)
    args = parser.parse_args()
    try:
        result = discover(args.repo, os.getenv("GH_TOKEN", ""),
                          args.qa_app_id, args.release_app_id)
    except HoldDiscoveryError as exc:
        print(json.dumps({"eligible": False, "error": str(exc), "can_execute": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
