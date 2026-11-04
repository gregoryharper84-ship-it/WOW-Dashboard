#!/usr/bin/env python3
"""Read-only WOW V17 release truth. Live GitHub evidence outranks old policy text.

A report is NOT a check run, approval, merge authorization, model receipt, or deploy.
Unknown policy/API/PR identity is UNVERIFIED, never evidence of an absent gate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")


def applies_to_main(rs: dict[str, Any]) -> bool | None:
    """Return None for patterns we cannot evaluate safely."""
    if not isinstance(rs, dict) or "enforcement" not in rs or "target" not in rs:
        return None
    if rs.get("enforcement") != "active" or rs.get("target") != "branch":
        return False
    conditions = rs.get("conditions")
    refs = conditions.get("ref_name") if isinstance(conditions, dict) else None
    if not isinstance(refs, dict):
        return None
    includes, excludes = refs.get("include"), refs.get("exclude")
    if not isinstance(includes, list) or not isinstance(excludes, list):
        return None
    recognized = {"~DEFAULT_BRANCH", "~ALL", "main", "refs/heads/main"}
    if any(not isinstance(s, str) for s in includes + excludes):
        return None
    if any(x in recognized for x in excludes):
        return False
    if excludes:
        return None  # Could exclude main through a pattern.
    if any(x in recognized for x in includes):
        return True
    return False if all(not any(c in x for c in "*?[]") for x in includes) else None


def classify(
    pr: dict[str, Any],
    rulesets: list[dict[str, Any]] | None,
    checks: list[dict[str, Any]] | None,
    *,
    repository: str,
) -> dict[str, Any]:
    if not isinstance(pr, dict):
        pr = {}
    head_record = pr.get("head")
    head = head_record.get("sha") if isinstance(head_record, dict) else None
    findings: list[dict[str, str]] = []
    known = lambda code, source: findings.append(
        {"finding": code, "state": "BLOCKED", "source": source}
    )
    unknown = lambda code, source: findings.append(
        {"finding": code, "state": "UNVERIFIED", "source": source}
    )
    pending = lambda code, source: findings.append(
        {"finding": code, "state": "PENDING", "source": source}
    )

    if not isinstance(head, str) or not SHA.fullmatch(head):
        unknown("PR_HEAD_UNVERIFIED", "live_pr")
    if pr.get("state") not in ("open", "closed"):
        unknown("PR_STATE_UNVERIFIED", "live_pr")
    elif pr["state"] != "open":
        known("PR_NOT_OPEN", "live_pr")
    if pr.get("draft") is True:
        known("PR_DRAFT", "live_pr")
    base_record = pr.get("base")
    base = base_record.get("ref") if isinstance(base_record, dict) else None
    if base != "main":
        unknown("PR_TARGET_MAIN_UNVERIFIED", "live_pr")
    merge_state = pr.get("mergeable_state")
    if merge_state == "dirty":
        known("PR_MERGE_CONFLICT", "live_pr")
    elif merge_state in (None, "unknown", "blocked", "unstable"):
        unknown("PR_MERGEABILITY_UNVERIFIED", "live_pr")

    applicable: list[dict[str, Any]] = []
    policy_unknown = not isinstance(rulesets, list)
    for rs in rulesets if isinstance(rulesets, list) else []:
        state = applies_to_main(rs)
        if state is None:
            policy_unknown = True
        elif state:
            applicable.append(rs)

    required_checks: dict[tuple[str, int], bool] = {}
    review_counts: list[int] = []
    strict_base_checks = False
    if policy_unknown:
        unknown("RULESET_REQUIREMENTS_UNVERIFIED", "github_rulesets")
    else:
        for rs in applicable:
            rules = rs.get("rules")
            if not isinstance(rules, list):
                policy_unknown = True
                break
            for rule in rules:
                if not isinstance(rule, dict):
                    policy_unknown = True
                    continue
                kind, params = rule.get("type"), rule.get("parameters")
                if kind not in ("pull_request", "required_status_checks"):
                    continue
                if not isinstance(params, dict):
                    policy_unknown = True
                    continue
                if kind == "pull_request":
                    count = params.get("required_approving_review_count")
                    if type(count) is not int or count < 0:
                        policy_unknown = True
                    else:
                        review_counts.append(count)
                    # Zero in the approval-count field is insufficient proof if
                    # another review control is active or its type is unknown.
                    for flag in (
                        "require_code_owner_review",
                        "require_last_push_approval",
                        "require_extra_approval_for_unattributed_changes",
                        "required_review_thread_resolution",
                    ):
                        value = params.get(flag, False)
                        if type(value) is not bool or value:
                            policy_unknown = True
                    reviewers = params.get("required_reviewers", [])
                    if not isinstance(reviewers, list) or reviewers:
                        policy_unknown = True
                if kind == "required_status_checks":
                    strict = params.get("strict_required_status_checks_policy")
                    if type(strict) is not bool:
                        policy_unknown = True
                    elif strict:
                        strict_base_checks = True
                    arr = params.get("required_status_checks")
                    if not isinstance(arr, list):
                        policy_unknown = True
                        continue
                    for entry in arr:
                        if not isinstance(entry, dict):
                            policy_unknown = True
                            continue
                        name, app = entry.get("context"), entry.get("integration_id")
                        if not isinstance(name, str) or not name or type(app) is not int:
                            policy_unknown = True
                        else:
                            required_checks[(name, app)] = True
        if policy_unknown:
            unknown("RULESET_REQUIREMENTS_UNVERIFIED", "github_rulesets")

    if merge_state == "behind":
        if policy_unknown:
            unknown("PR_BASE_SYNC_POLICY_UNVERIFIED", "github_rulesets")
        elif strict_base_checks:
            known("PR_HEAD_BEHIND_STRICT_BASE", "github_rulesets")
        else:
            unknown("PR_BEHIND_BASE_REQUIREMENT_UNVERIFIED", "github_rulesets")

    # Only statements about the observed rulesets are authorized. Ordinary
    # branch-protection and separate model/product acceptance are not inferred.
    human_review = "UNVERIFIED" if policy_unknown else (
        "REQUIRED" if any(review_counts) else "NOT_REQUIRED_BY_RULESET"
    )
    if human_review == "NOT_REQUIRED_BY_RULESET":
        rejected = ["HUMAN_REVIEW_REQUIRED_BY_ACTIVE_RULESET"]
    else:
        rejected = []

    if not isinstance(checks, list) or any(
        not isinstance(c, dict)
        or not isinstance(c.get("app"), dict)
        or type(c.get("id")) is not int
        for c in checks
    ):
        unknown("CHECK_INVENTORY_UNVERIFIED", "github_check_runs")
    elif not policy_unknown and isinstance(head, str) and SHA.fullmatch(head):
        latest: dict[tuple[str, int], dict[str, Any]] = {}
        for check in checks:
            if check.get("head_sha") != head:
                continue
            app = (check.get("app") or {}).get("id")
            key = (check.get("name"), app)
            if key not in required_checks:
                continue
            if key not in latest or check.get("id", -1) > latest[key].get("id", -1):
                latest[key] = check
        for name, app in sorted(required_checks):
            item = latest.get((name, app))
            if item is None:
                pending(f"REQUIRED_CHECK_MISSING:{name}:{app}", "github_check_runs")
            elif item.get("status") != "completed":
                pending(f"REQUIRED_CHECK_PENDING:{name}:{app}", "github_check_runs")
            elif item.get("conclusion") != "success":
                known(f"REQUIRED_CHECK_NOT_SUCCESS:{name}:{app}", "github_check_runs")

    return {
        "pr": pr.get("number"), "head_sha": head, "repository": repository,
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "ruleset_count_applicable": len(applicable),
        "ruleset_human_review": human_review,
        "required_check_identities": [
            {"context": n, "integration_id": a} for n, a in sorted(required_checks)
        ],
        "rejected_obsolete_claims": rejected,
        "findings": findings,
        "merge_evidence": "BLOCKED" if any(x["state"] == "BLOCKED" for x in findings)
            else "UNVERIFIED" if any(x["state"] == "UNVERIFIED" for x in findings)
            else "PENDING" if any(x["state"] == "PENDING" for x in findings)
            else "NO_IDENTIFIED_RULESET_OR_PR_BLOCKER",
        "branch_protection_separate": "NOT_ASSESSED",
        "semantic_review": "NOT_ASSESSED",
        "deployment": "NOT_ASSESSED",
        "model_acceptance": "NOT_ASSESSED",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "not_release_authorization": True,
    }


def gh_json(route: str) -> Any:
    try:
        proc = subprocess.run(["gh", "api", route], capture_output=True, text=True, timeout=25)
    except OSError as exc:
        # Reporting-only: unavailable GitHub CLI yields UNVERIFIED evidence,
        # never a traceback or a fabricated release blocker.
        raise RuntimeError(f"GITHUB_CLI_UNAVAILABLE:{type(exc).__name__}") from exc
    if proc.returncode:
        raise RuntimeError(f"GITHUB_READ_FAILED: {route.split('?')[0]}")
    return json.loads(proc.stdout)


def live_report(repo: str, number: int) -> dict[str, Any]:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("INVALID_REPOSITORY")
    pr = gh_json(f"/repos/{repo}/pulls/{number}")
    index = None
    rulesets: list[dict[str, Any]] | None = None
    try:
        index = gh_json(f"/repos/{repo}/rulesets?includes_parents=true&per_page=100")
        if (
            not isinstance(index, list) or len(index) >= 100
            or any(not isinstance(r, dict) or type(r.get("id")) is not int for r in index)
        ):
            raise ValueError("RULESET_INVENTORY_UNVERIFIED")
        rulesets = [
            gh_json(f"/repos/{repo}/rulesets/{r['id']}")
            for r in index if r.get("enforcement") == "active" and r.get("target") == "branch"
        ]
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        rulesets = None

    checks: list[dict[str, Any]] | None = None
    try:
        head = pr["head"]["sha"]
        pages: list[dict[str, Any]] = []
        for page in range(1, 21):
            result = gh_json(
                f"/repos/{repo}/commits/{head}/check-runs?per_page=100&page={page}"
            )
            batch = result["check_runs"]
            if not isinstance(batch, list):
                raise ValueError("CHECK_PAGE_INVALID")
            pages.extend(batch)
            if len(pages) >= result["total_count"] and len(batch) < 100:
                checks = pages
                break
            if len(batch) < 100:
                if len(pages) != result["total_count"]:
                    raise ValueError("CHECK_INVENTORY_TRUNCATED")
                checks = pages
                break
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        checks = None
    return classify(pr, rulesets, checks, repository=repo)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    args = parser.parse_args()
    try:
        data = live_report(args.repo, args.pr_number)
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        data = {
            "pr": args.pr_number, "repository": args.repo,
            "findings": [{"finding": "LIVE_PR_EVIDENCE_UNVERIFIED",
                          "state": "UNVERIFIED", "source": "github_api"}],
            "read_error": str(exc), "can_execute": False,
            "not_release_authorization": True,
        }
    print(json.dumps(data, sort_keys=True))
    return 0  # Reporting only. Never becomes a required admission check.


if __name__ == "__main__":
    raise SystemExit(main())
