"""#1908 PR A: read-only policy evidence, NOT a production admission controller.

Nothing imports this module from a writer. Even valid evidence cannot authorize
merge, journal mutation, credential access, or a Render POST. The existing
two-App controller remains authoritative until a separately approved cutover.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any, Callable

from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS, gh_fetch
from v17.agent_identity_policy import governance_trust_roots, is_trust_root
from v17.github_release_journal import (
    AUTHENTIC_QA_APP_ID,
    AUTHENTIC_RELEASE_APP_ID,
    CONTROLLER_FILENAME,
    QA_CHECK_NAME,
    RELEASE_CHECK_NAME,
    role_admission,
)


REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
RULESET_ID = 24907422
RULESET_NAME = "wow-v17-main-agent-separation"
LEGACY = "legacy_two_app"
SINGLE_QA = "single_qa_plus_protected_environment"
DEPLOYMENT_CHECK = "WOW Release Authority deployment"
_SHA = re.compile(r"[0-9a-f]{40}")
Fetch = Callable[[str], tuple[int, Any]]


class EvidenceHold(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise EvidenceHold(reason)


def _get(fetch: Fetch, path: str, kind: type = dict) -> Any:
    status, body = fetch(path)
    _require(status == 200 and isinstance(body, kind), "GITHUB_EVIDENCE_UNREADABLE:" + path)
    return body


def _pages(fetch: Fetch, path: str, key: str | None = None) -> list[dict]:
    items = []
    for page in range(1, 11):
        query = f"per_page=100&page={page}"
        if key == "check_runs":
            query += "&filter=all"
        body = _get(fetch, f"{path}?{query}", dict if key else list)
        rows = body.get(key) if key else body
        _require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows),
                 "GITHUB_EVIDENCE_SCHEMA_INVALID:" + path)
        items.extend(rows)
        if len(rows) < 100:
            if key:
                _require(body.get("total_count") == len(items), "GITHUB_EVIDENCE_TRUNCATED:" + path)
            return items
    raise EvidenceHold("GITHUB_EVIDENCE_TRUNCATED:" + path)


def _policy(ruleset: dict, effective: list[dict], default_branch: str) -> tuple[str, list[str]]:
    selector = ruleset.get("conditions", {}).get("ref_name")
    targets_main = selector == {"include": ["refs/heads/main"], "exclude": []} or (
        selector == {"include": ["~DEFAULT_BRANCH"], "exclude": []} and default_branch == "main"
    )
    _require(
        ruleset.get("id") == RULESET_ID
        and ruleset.get("name") == RULESET_NAME
        and ruleset.get("source") == REPOSITORY
        and ruleset.get("source_type") == "Repository"
        and ruleset.get("target") == "branch"
        and ruleset.get("enforcement") == "active"
        and ruleset.get("bypass_actors") == []
        and targets_main,
        "ACTIVE_RULESET_MISMATCH",
    )
    rules = ruleset.get("rules", [])
    _require(isinstance(rules, list), "ACTIVE_RULESET_MISMATCH")
    pr_rules = [r for r in rules if r.get("type") == "pull_request"]
    _require(len(pr_rules) == 1, "RULESET_PULL_REQUEST_REQUIREMENT_MISSING_OR_AMBIGUOUS")
    _require({"deletion", "non_fast_forward"} <= {r.get("type") for r in rules},
             "RULESET_HISTORY_PROTECTION_MISSING")
    status_rules = [r for r in rules if r.get("type") == "required_status_checks"]
    _require(len(status_rules) == 1, "RULESET_CHECKS_AMBIGUOUS")
    params = status_rules[0].get("parameters", {})
    checks = params.get("required_status_checks", [])
    required = {(name, 15368) for name in REQUIRED_REGRESSION_CHECKS}
    required.add((QA_CHECK_NAME, AUTHENTIC_QA_APP_ID))
    observed = {(c.get("context"), c.get("integration_id")) for c in checks}
    _require(len(checks) == len(observed), "RULESET_CHECKS_AMBIGUOUS")
    if observed == required | {(RELEASE_CHECK_NAME, AUTHENTIC_RELEASE_APP_ID)}:
        policy = LEGACY
    else:
        _require(observed == required, "RULESET_REQUIRED_CONTEXT_MISMATCH")
        policy = SINGLE_QA
    active = [r for r in effective if r.get("type") == "required_status_checks"]
    _require(len(active) == 1 and active[0].get("ruleset_id") == RULESET_ID
             and active[0].get("parameters") == params, "EFFECTIVE_MAIN_RULESET_MISMATCH")
    requirements = []
    if policy == SINGLE_QA:
        for flag in ("require_code_owner_review", "dismiss_stale_reviews_on_push", "require_last_push_approval"):
            if pr_rules[0].get("parameters", {}).get(flag) is not True:
                requirements.append("RULESET_OWNER_PROTECTION_MISSING:" + flag)
        if params.get("strict_required_status_checks_policy") is not True:
            requirements.append("RULESET_CHECKS_NOT_STRICT")
    return policy, requirements


def _check(checks: list[dict], name: str, sha: str, app: int) -> dict:
    matching = [c for c in checks if c.get("name") == name and c.get("head_sha") == sha
                and (c.get("app") or {}).get("id") == app]
    _require(bool(matching), "CHECK_MISSING_STALE_OR_FOREIGN:" + name)
    _require(all(type(c.get("id")) is int and c["id"] > 0 for c in matching),
             "CHECK_ID_INVALID:" + name)
    latest = max(matching, key=lambda c: c["id"])
    _require(latest.get("status") == "completed" and latest.get("conclusion") == "success",
             "CHECK_NOT_SUCCESS:" + name)
    return latest


def _authorization(fetch: Fetch, root: str, run: dict, target_sha: str, pr_author_id: int) -> str:
    env_path = root + "/environments/wow-release"
    env = _get(fetch, env_path)
    _require(env.get("name") == "wow-release" and type(env.get("id")) is int,
             "ENVIRONMENT_IDENTITY_INVALID")
    # Not exposed by the published REST schema: missing remains UNKNOWN/HOLD.
    _require(env.get("can_admins_bypass") is False, "ENVIRONMENT_BYPASS_UNPROVEN")
    _require(env.get("deployment_branch_policy") == {
        "protected_branches": False, "custom_branch_policies": True,
    }, "ENVIRONMENT_MAIN_ONLY_UNPROVEN")
    branches = _pages(fetch, env_path + "/deployment-branch-policies", "branch_policies")
    _require(len(branches) == 1 and branches[0].get("name") == "main"
             and branches[0].get("type") == "branch", "ENVIRONMENT_MAIN_ONLY_UNPROVEN")
    rules = env.get("protection_rules")
    _require(isinstance(rules, list), "ENVIRONMENT_PROTECTIONS_UNREADABLE")
    reviewers = [r for r in rules if r.get("type") == "required_reviewers"]
    custom = _get(fetch, env_path + "/deployment_protection_rules")
    apps = custom.get("custom_deployment_protection_rules")
    _require(isinstance(apps, list) and custom.get("total_count") == len(apps),
             "ENVIRONMENT_APP_RULES_UNREADABLE")
    _require(bool(reviewers) != bool(apps), "RELEASE_AUTHORIZATION_ROUTE_AMBIGUOUS_OR_MISSING")

    def readback(route: str) -> str:
        _require(_get(fetch, env_path) == env, "ENVIRONMENT_CHANGED_DURING_READ")
        _require(_pages(fetch, env_path + "/deployment-branch-policies", "branch_policies") == branches,
                 "ENVIRONMENT_BRANCH_POLICY_CHANGED_DURING_READ")
        _require(_get(fetch, env_path + "/deployment_protection_rules") == custom,
                 "ENVIRONMENT_APP_RULE_CHANGED_DURING_READ")
        return route

    actor, trigger = run.get("actor", {}), run.get("triggering_actor", {})
    _require(type(actor.get("id")) is int and actor["id"] > 0
             and type(trigger.get("id")) is int and trigger["id"] > 0,
             "DISPATCH_ACTOR_UNREADABLE")
    if reviewers:
        _require(len(reviewers) == 1 and reviewers[0].get("prevent_self_review") is True,
                 "ENVIRONMENT_SELF_REVIEW_NOT_PREVENTED")
        # The review-history schema has no attempt identity. Never reuse a prior
        # attempt's approval as authorization for a rerun.
        _require(run.get("run_attempt") == 1, "ENVIRONMENT_APPROVAL_ATTEMPT_UNPROVEN")
        eligible = reviewers[0].get("reviewers", [])
        ids = {r.get("reviewer", {}).get("id") for r in eligible
               if r.get("type") == "User" and r.get("reviewer", {}).get("type") == "User"}
        _require(bool(ids) and all(type(i) is int and i > 0 for i in ids),
                 "INDEPENDENT_ENVIRONMENT_REVIEWER_UNPROVEN")
        _require(bool(ids - {actor["id"], trigger["id"], pr_author_id}),
                 "INDEPENDENT_ENVIRONMENT_REVIEWER_UNPROVEN")
        history = _get(fetch, root + f"/actions/runs/{run['id']}/approvals", list)
        relevant = [h for h in history if any(e.get("id") == env["id"] and e.get("name") == "wow-release"
                                             for e in h.get("environments", []))]
        _require(bool(relevant), "ENVIRONMENT_APPROVAL_MISSING")
        # History carries neither ordering timestamps nor attempt IDs. Conflicting
        # or duplicate decisions require independent investigation, not guessing.
        _require(len(relevant) == 1, "ENVIRONMENT_APPROVAL_AMBIGUOUS")
        approval = relevant[0]
        user = approval.get("user", {})
        _require(approval.get("state") == "approved" and user.get("type") == "User"
                 and type(user.get("id")) is int and user["id"] > 0
                 and user["id"] in ids, "ENVIRONMENT_REVIEWER_UNAUTHORIZED")
        _require(user.get("id") not in {actor["id"], trigger["id"]}, "ENVIRONMENT_SELF_REVIEW")
        _require(user.get("id") != pr_author_id, "ENVIRONMENT_PR_AUTHOR_SELF_REVIEW")
        permission = _get(fetch, root + f"/collaborators/{user['login']}/permission")
        _require(permission.get("user", {}).get("id") == user["id"]
                 and permission.get("permission") in {"admin", "write", "read", "maintain"},
                 "ENVIRONMENT_REVIEWER_ACCESS_UNPROVEN")
        return readback("independent_environment_reviewer")
    _require(len(apps) == 1 and apps[0].get("enabled") is True
             and apps[0].get("app", {}).get("id") == AUTHENTIC_RELEASE_APP_ID,
             "EXISTING_RELEASE_APP_RULE_UNPROVEN")
    checks = _pages(fetch, root + f"/commits/{target_sha}/check-runs", "check_runs")
    release = _check(checks, DEPLOYMENT_CHECK, target_sha, AUTHENTIC_RELEASE_APP_ID)
    _require(release.get("external_id") == f"wow-release:{run['id']}:{run['run_attempt']}:{target_sha}"
             and release.get("details_url") == run["html_url"], "RELEASE_APP_RUN_BINDING_INVALID")
    started = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(release["completed_at"].replace("Z", "+00:00"))
    _require(completed >= started, "RELEASE_APP_DECISION_STALE")
    return readback("existing_release_app_deployment_decision")


def _trust_root_review(fetch: Fetch, root: str, pr: dict, owner: dict) -> None:
    files = _pages(fetch, root + f"/pulls/{pr['number']}/files")
    _require(bool(files) and len(files) == pr.get("changed_files"), "PR_FILE_INVENTORY_UNPROVEN")
    paths = []
    for entry in files:
        _require(isinstance(entry.get("filename"), str) and bool(entry["filename"]),
                 "PR_FILE_IDENTITY_UNPROVEN")
        paths.append(entry["filename"])
        if entry.get("status") == "renamed" or "previous_filename" in entry:
            _require(isinstance(entry.get("previous_filename"), str) and bool(entry["previous_filename"]),
                     "PR_RENAME_ORIGIN_UNPROVEN")
            paths.append(entry["previous_filename"])
    governed = frozenset(governance_trust_roots(Path(__file__).resolve().parents[3])) | {
        "artifacts/wow-engine/v17/simple_release_policy_candidate.py",
    }
    if not any(is_trust_root(path, governed) for path in paths):
        return
    _require(type(owner.get("id")) is int and owner["id"] > 0, "OWNER_IDENTITY_UNPROVEN")
    _require(pr["user"]["id"] != owner["id"], "TRUST_ROOT_OWNER_AUTHORED")
    reviews = _pages(fetch, root + f"/pulls/{pr['number']}/reviews")
    decisions = [r for r in reviews if (r.get("user") or {}).get("id") == owner["id"]
                 and r.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}]
    _require(bool(decisions) and all(type(r.get("id")) is int for r in decisions),
             "TRUST_ROOT_OWNER_APPROVAL_UNPROVEN")
    latest = max(decisions, key=lambda r: r["id"])
    _require(latest.get("state") == "APPROVED" and latest.get("commit_id") == pr["head"]["sha"],
             "TRUST_ROOT_OWNER_APPROVAL_NOT_CURRENT")


def inspect_release_policy(
    *, target_sha: str, run_id: int, fetch: Fetch = gh_fetch,
) -> dict[str, Any]:
    """Collect authenticated GET evidence; return a non-authorizing observation.

    ``fetch`` is the trusted GET transport, injectable only for deterministic
    tests. Neither a caller-supplied policy flag nor a saved JSON artifact selects
    the policy. Production integration and security acceptance are out of scope.
    """
    result: dict[str, Any] = {
        "observed_policy": None, "authorization_route": None, "policy_evidence_valid": False,
        "status": "BLOCKED_WITH_EXACT_REASON", "blockers": [], "migration_requirements": [],
        "deployment_blockers": [
            "PR_A_NOT_PRODUCTION_AUTHORIZED",
            "ENVIRONMENT_ADMIN_BYPASS_AUTHORITATIVE_EVIDENCE_UNSUPPORTED",
            "ENVIRONMENT_APPROVAL_TIMESTAMP_AUTHORITATIVE_EVIDENCE_UNSUPPORTED",
            "RENDER_API_CREDENTIAL_SCOPE_UNATTESTED:#1875",
            "TRUE_MAIN_PUSH_CI_VERIFICATION_REQUIRED",
            "EXCLUSIVE_PRODUCTION_WRITER_AND_JOURNAL_CAS_LEASE_REQUIRED",
            "HEALTH_ACCEPTED_SCORING_PERSISTENCE_RECONCILIATION_REQUIRED",
            "DURABLE_PRODUCTION_RECEIPT_REQUIRED",
        ],
        "post_allowed": False, "deploy_authorized": False, "merge_authorized": False,
        "can_execute": False, "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True,
        "runtime_generation": "V17_ACTIVE", "terminal_authority": "V17_TERMINAL_REDUCER",
        "custom_gpt_identity": "WOW_BETTING_ENGINE",
    }
    root = "repos/" + REPOSITORY
    try:
        _require(bool(_SHA.fullmatch(str(target_sha))) and type(run_id) is int and run_id > 0,
                 "RELEASE_IDENTITY_INVALID")
        user = _get(fetch, "user")
        _require(type(user.get("id")) is int and bool(user.get("login")), "AUTHENTICATED_READ_UNPROVEN")
        repository = _get(fetch, root)
        _require(repository.get("full_name") == REPOSITORY
                 and isinstance(repository.get("default_branch"), str), "REPOSITORY_IDENTITY_UNPROVEN")
        ruleset = _get(fetch, root + f"/rulesets/{RULESET_ID}")
        effective = _pages(fetch, root + "/rules/branches/main")
        policy, requirements = _policy(ruleset, effective, repository["default_branch"])
        result["observed_policy"] = policy
        result["migration_requirements"] = requirements
        main_path = root + "/git/ref/heads/main"
        _require(_get(fetch, main_path).get("object", {}).get("sha") == target_sha, "CURRENT_MAIN_DRIFT")
        prs = _pages(fetch, root + f"/commits/{target_sha}/pulls")
        merged = [p for p in prs if p.get("merged_at") and p.get("merge_commit_sha") == target_sha]
        _require(len(merged) == 1, "MERGED_PR_PROVENANCE_NOT_UNIQUE")
        pr = _get(fetch, root + f"/pulls/{int(merged[0]['number'])}")
        _require(pr.get("merged") is True and pr.get("merge_commit_sha") == target_sha
                 and pr.get("base", {}).get("ref") == "main"
                 and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY
                 and pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY,
                 "MERGED_PR_PROVENANCE_INVALID")
        head = pr.get("head", {}).get("sha", "")
        _require(bool(_SHA.fullmatch(head)), "PR_HEAD_INVALID")
        _require(type(pr.get("user", {}).get("id")) is int and pr["user"]["id"] > 0,
                 "PR_AUTHOR_IDENTITY_UNPROVEN")
        checks = _pages(fetch, root + f"/commits/{head}/check-runs", "check_runs")
        for name in REQUIRED_REGRESSION_CHECKS:
            _check(checks, name, head, 15368)
        _check(checks, QA_CHECK_NAME, head, AUTHENTIC_QA_APP_ID)
        if policy == LEGACY:
            admission = role_admission(checks, qa_app_id=AUTHENTIC_QA_APP_ID,
                                       release_app_id=AUTHENTIC_RELEASE_APP_ID, source_head=head)
            _require(admission == "PASS", admission)
        elif not requirements:
            _trust_root_review(fetch, root, pr, repository.get("owner", {}))
            run_path = root + f"/actions/runs/{run_id}"
            run = _get(fetch, run_path)
            _require(run.get("id") == run_id and run.get("event") == "workflow_dispatch"
                     and run.get("head_sha") == target_sha and run.get("head_branch") == "main"
                     and run.get("repository", {}).get("full_name") == REPOSITORY
                     and run.get("path") == ".github/workflows/" + CONTROLLER_FILENAME
                     and type(run.get("run_attempt")) is int and run["run_attempt"] > 0
                     and run.get("html_url") == f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
                     "MANUAL_PROTECTED_RUN_IDENTITY_INVALID")
            result["authorization_route"] = _authorization(fetch, root, run, target_sha, pr["user"]["id"])
            _require(_get(fetch, run_path) == run, "WORKFLOW_RUN_CHANGED_DURING_READ")
        _require(_get(fetch, main_path).get("object", {}).get("sha") == target_sha, "CURRENT_MAIN_DRIFT")
        _require(_get(fetch, root + f"/rulesets/{RULESET_ID}") == ruleset,
                 "RULESET_CHANGED_DURING_READ")
        _require(_pages(fetch, root + "/rules/branches/main") == effective,
                 "EFFECTIVE_RULESET_CHANGED_DURING_READ")
        _require(_get(fetch, root) == repository, "REPOSITORY_CHANGED_DURING_READ")
        result["blockers"].extend(requirements)
        result["policy_evidence_valid"] = not requirements
    except EvidenceHold as exc:
        result["blockers"].append(str(exc))
    except Exception:
        # Do not leak transport errors, credentials, or untrusted response bodies.
        result["blockers"].append("GITHUB_EVIDENCE_UNREADABLE_OR_SCHEMA_INVALID")
    return result
