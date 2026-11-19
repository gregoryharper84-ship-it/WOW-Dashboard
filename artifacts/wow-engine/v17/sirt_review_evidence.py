"""Read-only exact-head SIRT source-review admission; never model certification.

Identities and check runs must come from GitHub's authenticated REST API,
not a PR-supplied JSON blob. This evaluator only produces a review receipt.
Fitted-model replay and separate Release Authority acceptance are still required.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
REQUIRED = (
    "WOW additional required regression",
    "WOW governed probability backend",
    "WOW required-three regression",
)
ACTIONS_APP = 15368


def _int_id(value):
    return value if type(value) is int and value > 0 else None


def decision(code, *, sha, findings=(), reviewer=None, checks=()):
    return {
        "schema_version": 1,
        "review_status": "EVIDENCE_READY" if code == "SIRT_SOURCE_REVIEW_EVIDENCE_READY" else "HOLD",
        "code": code,
        "head_sha": sha,
        "reviewer": reviewer,
        "required_check_names": list(checks),
        "findings": sorted(set(findings)),
        "independent_scientific_acceptance_required": True,
        "protected_release_authority_required": True,
        "merge_or_release_authorized": False,
        "model_certified": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def evaluate(*, repository, expected_sha, pr, reviews, check_runs,
             authorized_reviewer_ids, owner_id, head_commit_author_id=None):
    """Assess authenticated GitHub API observations; never approve promotion."""
    bad = []
    if repository != REPO or not isinstance(expected_sha, str) or not SHA_RE.fullmatch(expected_sha):
        return decision("SIRT_TARGET_INVALID", sha=expected_sha, findings=["SIRT_TARGET_INVALID"])
    if not isinstance(pr, dict) or not isinstance(reviews, list) or not isinstance(check_runs, list):
        return decision("SIRT_EVIDENCE_INVALID", sha=expected_sha, findings=["SIRT_EVIDENCE_INVALID"])
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    author_id = _int_id(user.get("id"))
    auth_ids = {x for x in authorized_reviewer_ids if _int_id(x) is not None}
    if (head.get("sha") != expected_sha or pr.get("state") != "open"
        or pr.get("draft") is not False or pr.get("merged_at") is not None
        or base.get("ref") != "main"
        or head_repo.get("full_name") != repository
        or author_id is None):
        bad.append("SIRT_PR_IDENTITY_OR_HEAD_INVALID")
    if len(auth_ids) != 1 or _int_id(owner_id) is None or _int_id(head_commit_author_id) is None:
        bad.append("SIRT_REVIEWER_IDENTITY_NOT_BOUND")
    if author_id in auth_ids or owner_id in auth_ids or head_commit_author_id in auth_ids:
        bad.append("SIRT_REVIEWER_NOT_INDEPENDENT")
    approved = None
    for rid in sorted(auth_ids):
        candidate = [r for r in reviews if isinstance(r, dict)
                     and isinstance(r.get("user"), dict)
                     and _int_id(r["user"].get("id")) == rid]
        if not candidate:
            continue
        # Later GitHub review IDs supersede earlier approvals for that reviewer.
        candidate.sort(key=lambda r: _int_id(r.get("id")) or -1)
        last = candidate[-1]
        if (last.get("state") == "APPROVED" and last.get("commit_id") == expected_sha
            and (last.get("user") or {}).get("type") == "User"
            and _int_id(last.get("id")) is not None and last.get("submitted_at")):
            approved = {
                "id": last["id"], "login": last["user"].get("login"),
                "user_id": rid, "commit_id": expected_sha,
                "submitted_at": last["submitted_at"],
            }
            break
    if approved is None:
        bad.append("SIRT_INDEPENDENT_EXACT_HEAD_APPROVAL_MISSING")
    for name in REQUIRED:
        matches = [c for c in check_runs if isinstance(c, dict)
                   and c.get("name") == name and c.get("head_sha") == expected_sha
                   and isinstance(c.get("app"), dict)
                   and _int_id(c["app"].get("id")) == ACTIONS_APP]
        if not matches:
            bad.append("SIRT_REQUIRED_CHECK_MISSING:" + name)
            continue
        matches.sort(key=lambda c: _int_id(c.get("id")) or -1)
        if matches[-1].get("status") != "completed" or matches[-1].get("conclusion") != "success":
            bad.append("SIRT_REQUIRED_CHECK_NOT_GREEN:" + name)
    return decision("SIRT_SOURCE_REVIEW_EVIDENCE_READY" if not bad else "SIRT_EVIDENCE_HOLD",
                    sha=expected_sha, findings=bad, reviewer=approved if not bad else None,
                    checks=REQUIRED)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_json(path, token):
    url = "https://api.github.com/repos/" + REPO + ("/" + path if path else "")
    req = Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with build_opener(NoRedirect()).open(req, timeout=15) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("SIRT_GITHUB_RESPONSE_LIMIT")
    return json.loads(payload)


def collect_and_evaluate(pr_number, expected_sha, reviewer_id, token):
    """Read identities from GitHub directly, never from candidate supplied data."""
    if (type(pr_number) is not int or pr_number <= 0
        or not isinstance(expected_sha, str) or not SHA_RE.fullmatch(expected_sha)):
        return decision("SIRT_TARGET_INVALID", sha=expected_sha)
    try:
        pr = github_json(f"pulls/{pr_number}", token)
        repo = github_json("", token)
        commit = github_json(f"commits/{expected_sha}", token)
        reviews = github_json(f"pulls/{pr_number}/reviews?per_page=100", token)
        checks = github_json(f"commits/{expected_sha}/check-runs?per_page=100", token)
        # Re-read after all review/check collection to detect a concurrent
        # force-push, draft transition or altered PR identity. No stale PASS.
        final_pr = github_json(f"pulls/{pr_number}", token)
    except (HTTPError, URLError, ValueError, TypeError, TimeoutError, json.JSONDecodeError):
        return decision("SIRT_GITHUB_EVIDENCE_UNAVAILABLE", sha=expected_sha)
    def pr_identity(record):
        if not isinstance(record, dict):
            return None
        head = record.get("head")
        base = record.get("base")
        user = record.get("user")
        if (not isinstance(head, dict) or not isinstance(base, dict)
            or not isinstance(user, dict) or not isinstance(head.get("repo"), dict)):
            return None
        return (
            head.get("sha"), head.get("ref"), head["repo"].get("full_name"),
            base.get("ref"), user.get("id"), record.get("state"),
            record.get("draft"), record.get("merged_at"),
        )

    before = pr_identity(pr)
    after = pr_identity(final_pr)
    if before is None or after is None:
        return decision("SIRT_EVIDENCE_INVALID", sha=expected_sha)
    if before != after:
        return decision(
            "SIRT_PR_CHANGED_DURING_REVIEW", sha=expected_sha,
            findings=["SIRT_PR_CHANGED_DURING_REVIEW"],
        )
    if (not isinstance(repo, dict) or not isinstance(commit, dict)
        or not isinstance(checks, dict) or not isinstance(reviews, list)
        or not isinstance(checks.get("check_runs"), list)
        or not isinstance(repo.get("owner"), dict)
        or (commit.get("author") is not None and not isinstance(commit.get("author"), dict))
        or type(checks.get("total_count")) is not int or checks["total_count"] < 0):
        return decision("SIRT_EVIDENCE_INVALID", sha=expected_sha)
    if (checks["total_count"] > 100
        or checks["total_count"] != len(checks["check_runs"])
        or len(reviews) >= 100):
        return decision("SIRT_EVIDENCE_PAGINATION_INCOMPLETE", sha=expected_sha)
    head_author = commit.get("author")
    return evaluate(
        repository=REPO, expected_sha=expected_sha, pr=final_pr, reviews=reviews,
        check_runs=checks["check_runs"], authorized_reviewer_ids={reviewer_id},
        owner_id=repo["owner"].get("id"),
        head_commit_author_id=head_author.get("id") if head_author else None,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    # Reviewer ID must be configured in a protected environment, not a PR body.
    try:
        reviewer_id = int(os.environ.get("WOW_SIRT_REVIEWER_USER_ID", ""))
    except ValueError:
        reviewer_id = 0
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token or reviewer_id <= 0:
        receipt = decision("SIRT_REVIEWER_IDENTITY_NOT_BOUND", sha=args.head_sha)
    else:
        receipt = collect_and_evaluate(args.pr, args.head_sha, reviewer_id, token)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["review_status"] == "EVIDENCE_READY" else 1


if __name__ == "__main__":
    sys.exit(main())
