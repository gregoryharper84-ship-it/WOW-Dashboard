"""Agent identity and protection policy for WOW V17 (incident #1550, parent #1540).

Single machine-readable source of truth for the three independent GitHub App
principals (Engineering, Independent QA, Release Authority) and the protection
that makes their separation enforceable. ``evaluate`` checks a read-only
snapshot of live repository settings against the policy and returns PASS or a
typed HOLD. It never changes settings, never approves, and never grants merge,
release or probability authority. ``can_execute`` is always false.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROLES = ("engineering", "qa", "release")

# Exact GitHub App permission sets. Anything not listed for a role is excess.
# Separation of the QA and Release required checks is enforced by pinning each
# required check to its App's integration_id, not by withholding checks:write.
APP_PERMISSIONS: dict[str, dict[str, str]] = {
    "engineering": {
        "metadata": "read",
        "contents": "write",        # push implementation branches
        "pull_requests": "write",   # open/update governed PRs (never merge: ruleset)
        "issues": "write",          # heartbeat #1138 and incident receipts
        "workflows": "write",       # trust-root edits; gated by CODEOWNERS owner review
        "actions": "read",
        "checks": "read",
    },
    "qa": {
        "metadata": "read",
        "contents": "read",
        "pull_requests": "read",
        "issues": "read",
        "actions": "read",
        "checks": "write",          # its own source-pinned exact-SHA check only
    },
    "release": {
        "metadata": "read",
        "contents": "write",        # merge commit on main via governed merge
        "pull_requests": "write",   # merge after QA + Release checks pass
        "issues": "write",          # release receipts
        "actions": "read",
        "checks": "write",          # its own source-pinned exact-SHA check only
        "deployments": "write",     # deployment records for exact SHA
    },
}

# Never granted to any agent principal.
FORBIDDEN_PERMISSIONS = frozenset({
    "administration", "secrets", "actions_variables", "environments",
    "repository_hooks", "members", "organization_administration",
    "repository_projects", "security_events", "pages",
})

REQUIRED_REGRESSION_CHECKS = (
    "WOW additional required regression",
    "WOW governed probability backend",
    "WOW required-three regression",
)
QA_CHECK = "WOW Independent QA exact-head"
RELEASE_CHECK = "WOW Release Authority exact-head"
ROLE_CHECKS = {"qa": QA_CHECK, "release": RELEASE_CHECK}

TRUST_ROOT_SOURCE = ".github/workflows/wow-v17-existing-pr-governance-review.yml"
# Autonomy model: ordinary PRs need no human review; they merge only when the
# source-pinned QA and Release App checks pass. Trust-root paths additionally need
# the sole owner's code-owner approval. No bypass actor may exist: while AI sessions
# act as the owner user, an admin bypass would also be an AI bypass.


def trust_root_paths(repo_root: Path) -> list[str]:
    """Trust-root paths exactly as the protected existing-PR review enforces them."""
    text = (repo_root / TRUST_ROOT_SOURCE).read_text()
    match = re.search(r'case "\$changed_path" in\s*\n\s*([^\n]+)\)\n', text)
    if not match:
        raise ValueError("TRUST_ROOT_LIST_UNPARSEABLE")
    return [p.strip() for p in match.group(1).split("|") if p.strip()]


def _codeowners_regex(pattern: str) -> re.Pattern[str]:
    """Translate a CODEOWNERS (gitignore-style) pattern into an anchored regex."""
    anchored = pattern.startswith("/") or "/" in pattern.strip("/")
    body = pattern.strip("/")
    out = ""
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            out += ".*"
            i += 2
        elif body[i] == "*":
            out += "[^/]*"
            i += 1
        elif body[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(body[i])
            i += 1
    suffix = "/.*" if pattern.endswith("/") else "(?:/.*)?"
    return re.compile(("" if anchored else "(?:.*/)?") + out + suffix + r"\Z")


def _codeowners_owners(codeowners: str, path: str) -> list[str]:
    """Owners of ``path``; the last matching rule wins (GitHub semantics).

    A trust-root entry ending in ``/*`` is checked as a representative file inside it.
    """
    probe = path[:-2] + "/x" if path.endswith("/*") else path
    owners: list[str] = []
    for raw in codeowners.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *rule_owners = line.split()
        if _codeowners_regex(pattern).match(probe):
            owners = rule_owners
    return owners


def _targets_main(ruleset: dict) -> bool:
    include = (((ruleset.get("conditions") or {}).get("ref_name") or {}).get("include")) or []
    return any(ref in ("refs/heads/main", "~DEFAULT_BRANCH", "~ALL") for ref in include)


def evaluate(snapshot: dict[str, Any], *, trust_roots: list[str]) -> dict[str, Any]:
    # An unreadable input is its own finding, never silently read as "absent".
    findings: list[str] = [f"IDENTITY_SNAPSHOT_INCOMPLETE:{err}" for err in snapshot.get("collection_errors") or []]
    bindings = snapshot.get("role_bindings") or {}
    installs = {str(i.get("app_id")): i for i in snapshot.get("installations") or []}

    # --- A/B: distinct, least-privilege App principals -------------------------
    bound_ids = {role: str(bindings.get(role) or "") for role in ROLES}
    for role, app_id in bound_ids.items():
        if not app_id:
            findings.append(f"IDENTITY_APP_UNBOUND:{role}")
        elif app_id not in installs:
            findings.append(f"IDENTITY_APP_NOT_INSTALLED:{role}")
    present = [v for v in bound_ids.values() if v]
    if len(present) != len(set(present)):
        findings.append("IDENTITY_APPS_NOT_DISTINCT")
    for role, app_id in bound_ids.items():
        install = installs.get(app_id)
        if not install:
            continue
        granted = {k: v for k, v in (install.get("permissions") or {}).items() if v}
        allowed = APP_PERMISSIONS[role]
        for perm, level in sorted(granted.items()):
            if perm in FORBIDDEN_PERMISSIONS:
                findings.append(f"IDENTITY_PERMISSION_FORBIDDEN:{role}:{perm}:{level}")
            elif perm not in allowed or (level == "write" and allowed[perm] == "read") or level == "admin":
                findings.append(f"IDENTITY_PERMISSION_EXCESS:{role}:{perm}:{level}")
        for perm, level in sorted(allowed.items()):
            got = granted.get(perm)
            if got is None or (level == "write" and got == "read"):
                findings.append(f"IDENTITY_PERMISSION_MISSING:{role}:{perm}:{level}")
        if install.get("repository_selection") != "selected":
            findings.append(f"IDENTITY_INSTALLATION_NOT_REPO_SCOPED:{role}")

    ai = snapshot.get("ai_credential") or {}
    if ai.get("can_merge") is not False:
        findings.append("IDENTITY_AI_CREDENTIAL_CAN_MERGE" if ai.get("can_merge") else "IDENTITY_AI_CREDENTIAL_UNMEASURED")
    if ai.get("acts_as_owner_user"):
        findings.append("IDENTITY_AI_CREDENTIAL_IS_OWNER_USER")

    # --- D: CODEOWNERS over every trust root ------------------------------------
    owner = str(snapshot.get("owner_login") or "")
    codeowners = snapshot.get("codeowners")
    if not codeowners:
        findings.append("PROTECTION_CODEOWNERS_MISSING")
    else:
        # Named trust roots plus probes for NEW files: a newly added privileged workflow
        # or action is a trust root even though no list names it yet.
        probes = [".github/CODEOWNERS", ".github/workflows/__new_workflow__.yml",
                  ".github/actions/__new_action__/action.yml", ".github/scripts/__new_script__.py"]
        for path in trust_roots + probes:
            if f"@{owner}" not in _codeowners_owners(codeowners, path):
                findings.append(f"PROTECTION_CODEOWNERS_UNCOVERED:{path}")

    # --- D: active ruleset on main ----------------------------------------------
    rulesets = [r for r in snapshot.get("rulesets") or []
                if r.get("enforcement") == "active" and r.get("target", "branch") == "branch" and _targets_main(r)]
    if not rulesets:
        findings.append("PROTECTION_RULESET_MISSING")
    rules = [rule for r in rulesets for rule in r.get("rules") or []]
    types = {rule.get("type") for rule in rules}
    pr_rules = [rule.get("parameters") or {} for rule in rules if rule.get("type") == "pull_request"]
    if rulesets:
        if not pr_rules:
            findings.append("PROTECTION_PULL_REQUEST_RULE_MISSING")
        for key, code in (
            ("require_code_owner_review", "PROTECTION_CODE_OWNER_REVIEW_NOT_REQUIRED"),
            ("require_last_push_approval", "PROTECTION_LAST_PUSH_APPROVAL_NOT_REQUIRED"),
            ("dismiss_stale_reviews_on_push", "PROTECTION_STALE_REVIEWS_NOT_DISMISSED"),
        ):
            if pr_rules and not any(p.get(key) for p in pr_rules):
                findings.append(code)
        if "non_fast_forward" not in types:
            findings.append("PROTECTION_FORCE_PUSH_ALLOWED")
        if "deletion" not in types:
            findings.append("PROTECTION_DELETION_ALLOWED")
        checks = [c for rule in rules if rule.get("type") == "required_status_checks"
                  for c in (rule.get("parameters") or {}).get("required_status_checks") or []]
        strict = any((rule.get("parameters") or {}).get("strict_required_status_checks_policy")
                     for rule in rules if rule.get("type") == "required_status_checks")
        if checks and not strict:
            findings.append("PROTECTION_CHECKS_NOT_STRICT")
        contexts = {c.get("context"): c for c in checks}
        for ctx in REQUIRED_REGRESSION_CHECKS:
            if ctx not in contexts:
                findings.append(f"PROTECTION_CHECK_MISSING:{ctx}")
        for role, ctx in ROLE_CHECKS.items():
            check = contexts.get(ctx)
            if not check:
                findings.append(f"PROTECTION_CHECK_MISSING:{ctx}")
            elif str(check.get("integration_id") or "") != bound_ids[role] or not bound_ids[role]:
                findings.append(f"PROTECTION_CHECK_NOT_SOURCE_PINNED:{ctx}")
        for actor in (a for r in rulesets for a in r.get("bypass_actors") or []):
            findings.append(f"PROTECTION_BYPASS_PRESENT:{actor.get('actor_type')}:{actor.get('actor_id')}")

    findings = sorted(set(findings))
    return {
        "status": "PASS" if not findings else "HOLD",
        "disposition": "FIXED_AND_VERIFIED_PENDING_INDEPENDENT_QA" if not findings else "BLOCKED_WITH_EXACT_REASON",
        "findings": findings,
        "merge_or_release_authorized": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def _gh(path: str) -> tuple[int, Any]:
    proc = subprocess.run(["gh", "api", "-i", path], capture_output=True, text=True)
    head, _, body = proc.stdout.partition("\r\n\r\n") if "\r\n\r\n" in proc.stdout else proc.stdout.partition("\n\n")
    try:
        status = int(head.split()[1])
    except (IndexError, ValueError):
        status = 0
    try:
        return status, json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        return status, None


def collect(repo: str, *, merge_probe_pr: int | None) -> dict[str, Any]:
    """Read-only collector. Run with the credential under test; prints no secrets.

    ``merge_probe_pr`` performs a merge attempt with an all-zero SHA, which GitHub
    always refuses (409/405/422) without merging; 403/404 means no merge capability.
    """
    snapshot: dict[str, Any] = {"repository": repo, "collection_errors": []}
    status, repo_json = _gh(f"repos/{repo}")
    snapshot["owner_login"] = (repo_json or {}).get("owner", {}).get("login", "")
    status, user = _gh("user")
    snapshot["ai_credential"] = {
        "login": (user or {}).get("login"),
        "acts_as_owner_user": bool(user) and (user or {}).get("login") == snapshot["owner_login"],
    }
    status, rulesets = _gh(f"repos/{repo}/rulesets")
    full = []
    for item in rulesets or []:
        _, detail = _gh(f"repos/{repo}/rulesets/{item.get('id')}")
        full.append(detail or item)
    snapshot["rulesets"] = full
    status, co = _gh(f"repos/{repo}/contents/.github/CODEOWNERS")
    if status == 200 and co and co.get("content"):
        import base64
        snapshot["codeowners"] = base64.b64decode(co["content"]).decode()
    else:
        snapshot["codeowners"] = None
    status, variables = _gh(f"repos/{repo}/actions/variables")
    values = {v.get("name"): v.get("value") for v in (variables or {}).get("variables", [])}
    snapshot["role_bindings"] = {role: values.get(f"WOW_{role.upper()}_APP_ID") for role in ROLES}
    if status != 200:
        snapshot["collection_errors"].append(f"VARIABLES_UNREADABLE:{status}")
    status, inst = _gh("user/installations")
    snapshot["installations"] = (inst or {}).get("installations", []) if status == 200 else []
    if status != 200:
        snapshot["collection_errors"].append(f"INSTALLATIONS_UNREADABLE:{status}")
    if merge_probe_pr:
        proc = subprocess.run(
            ["gh", "api", "-i", "-X", "PUT", f"repos/{repo}/pulls/{merge_probe_pr}/merge",
             "-f", "sha=0000000000000000000000000000000000000000"],
            capture_output=True, text=True,
        )
        try:
            code = int(proc.stdout.split()[1])
        except (IndexError, ValueError):
            code = 0
        snapshot["ai_credential"]["merge_probe_status"] = code
        snapshot["ai_credential"]["can_merge"] = code in (405, 409, 422) if code else None
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--repo", required=True)
    c.add_argument("--merge-probe-pr", type=int)
    e = sub.add_parser("evaluate")
    e.add_argument("--snapshot", required=True)
    e.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[3]))
    sub.add_parser("spec")
    args = parser.parse_args(argv)
    if args.cmd == "collect":
        print(json.dumps(collect(args.repo, merge_probe_pr=args.merge_probe_pr), indent=2))
        return 0
    if args.cmd == "spec":
        print(json.dumps({"app_permissions": APP_PERMISSIONS, "forbidden": sorted(FORBIDDEN_PERMISSIONS),
                          "role_checks": ROLE_CHECKS, "regression_checks": REQUIRED_REGRESSION_CHECKS}, indent=2))
        return 0
    snapshot = json.loads(Path(args.snapshot).read_text())
    result = evaluate(snapshot, trust_roots=trust_root_paths(Path(args.repo_root)))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
