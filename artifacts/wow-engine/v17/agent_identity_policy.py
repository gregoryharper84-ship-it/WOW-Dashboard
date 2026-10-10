"""Agent identity and protection policy for WOW V17 (incident #1550, parent #1540).

Single machine-readable source of truth for the three independent GitHub App
principals (Engineering, Independent QA, Release Authority) and the protection
that makes their separation enforceable.

Three independently collected kinds of evidence are joined by ``evaluate``:

* **Owner inventory** (``collect-owner``, run with the owner's credential):
  repository identity, rulesets, CODEOWNERS, role bindings, App installations
  and the exact repositories each installation covers. It says nothing about AI.
* **AI principal evidence** (``collect-principal``, run *inside* each AI runtime):
  who that runtime acts as, and a bounded merge-capability probe. Bound to the
  inventory's nonce and an expiry. Self-reported, so never sufficient alone.
* **Live acceptance records**: owner-observed allow/deny outcomes of real probe
  PRs against the enforced ruleset. Configuration that only *looks* right never
  passes without them.

``evaluate`` returns PASS or a typed HOLD. PASS is a configuration-evidence
verdict only: it is never a work-item closure, merge, release, deployment or
probability authority. Nothing here changes settings. ``can_execute`` is false.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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

# AI runtimes that must each prove, from inside the runtime, that they no longer act
# as the owner user and cannot merge. Extend when a new AI integration is added.
AI_RUNTIMES = ("claude_session", "chatgpt_session")
MAX_EVIDENCE_TTL = timedelta(hours=24)

# Owner-observed outcomes required against the *enforced* ruleset (finding 6).
LIVE_ACCEPTANCE_PROBES: dict[str, str] = {
    "ordinary_app_pr_merges_without_human_review": "ALLOW",
    "trust_root_pr_blocked_without_owner_approval": "DENY",
    "trust_root_pr_mergeable_after_owner_approval": "ALLOW",
    "push_after_owner_approval_requires_reapproval": "DENY",
    "new_workflow_file_requires_owner_approval": "DENY",
    "qa_check_published_by_release_app_does_not_satisfy": "DENY",
    "release_check_published_by_qa_app_does_not_satisfy": "DENY",
    "engineering_app_cannot_merge": "DENY",
    "ai_session_cannot_merge": "DENY",
    "direct_push_to_main_blocked": "DENY",
}

ALLOWED_DISPOSITIONS = frozenset({"BLOCKED_WITH_EXACT_REASON"})
TRUST_ROOT_SOURCE = ".github/workflows/wow-v17-existing-pr-governance-review.yml"
CODEOWNERS_LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")  # GitHub search order
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Autonomy model: ordinary PRs need no human review; they merge only when the
# source-pinned QA and Release App checks pass. Everything under /.github/ also needs
# the sole owner's code-owner approval. No bypass actor may exist: while AI sessions
# act as the owner user, an admin bypass would also be an AI bypass.


# ----------------------------------------------------------------------------- utils

def trust_root_paths(repo_root: Path) -> list[str]:
    """Trust-root paths exactly as the protected existing-PR review enforces them."""
    text = (repo_root / TRUST_ROOT_SOURCE).read_text()
    match = re.search(r'case "\$changed_path" in\s*\n\s*([^\n]+)\)\n', text)
    if not match:
        raise ValueError("TRUST_ROOT_LIST_UNPARSEABLE")
    return [p.strip() for p in match.group(1).split("|") if p.strip()]


# Workflows that make governance decisions or hold control-plane credentials.
GOVERNANCE_WORKFLOWS_EXTRA = (
    ".github/workflows/wow-v17-independent-qa-check.yml",
    ".github/workflows/wow-v17-release-authority-check.yml",
)
TRUST_ROOT_PREFIXES = (".github/", ".agents/")
_REF_RE = re.compile(r"((?:artifacts|scripts|\.agents|\.github/scripts)/[A-Za-z0-9_./-]+\.(?:py|json|md|sh|ya?ml|txt))")
_V17_IMPORT_RE = re.compile(r"\b(?:from|import)\s+v17\.([A-Za-z_][A-Za-z0-9_]*)")


def _local_imports(repo_root: Path, path: Path) -> set[Path]:
    import ast

    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    found: set[Path] = set()
    bases = (path.parent, repo_root / "artifacts/wow-engine", repo_root / "artifacts/wow-engine/v17")
    for name in names:
        for base in bases:
            cand = base / (name.replace(".", "/") + ".py")
            if cand.is_file():
                found.add(cand.resolve())
    return found


def governance_trust_roots(repo_root: Path) -> list[str]:
    """Every file a governance workflow executes or reads, plus its local imports.

    A change to any of these can alter how the gates themselves decide, so it must
    never pass autonomously. Tests are excluded: they gate nothing at decision time.
    """
    repo_root = repo_root.resolve()
    workflows = [p for p in trust_root_paths(repo_root) if p.startswith(".github/workflows/")]
    workflows += [w for w in GOVERNANCE_WORKFLOWS_EXTRA if (repo_root / w).is_file()]
    found: set[Path] = set()
    for wf in workflows:
        text = (repo_root / wf).read_text()
        for ref in _REF_RE.findall(text):
            found.add((repo_root / ref).resolve())
        for mod in _V17_IMPORT_RE.findall(text):
            found.add((repo_root / "artifacts/wow-engine/v17" / f"{mod}.py").resolve())
    found = {f for f in found if f.is_file()}
    frontier = [f for f in found if f.suffix == ".py"]
    while frontier:
        nxt = []
        for f in frontier:
            for dep in _local_imports(repo_root, f):
                if dep not in found:
                    found.add(dep)
                    nxt.append(dep)
        frontier = nxt
    rel = {str(f.relative_to(repo_root)) for f in found}
    return sorted(r for r in rel if "/test" not in r and not Path(r).name.startswith("test_"))


def is_trust_root(path: str, governance_files: set[str] | frozenset[str]) -> bool:
    return path.startswith(TRUST_ROOT_PREFIXES) or path in governance_files


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
    """Owners of ``path``; the last matching rule wins (GitHub semantics)."""
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


def _parse_time(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(stamp: datetime) -> str:
    return stamp.isoformat().replace("+00:00", "Z")


def interpret_merge_probe(status: int, message: str) -> str:
    """Fail-closed reading of a merge attempt made with an all-zero SHA.

    Only an explicit authorization refusal (403/404) proves DENIED. A 409 whose
    message is GitHub's SHA guard means authorization was granted and only the
    deliberately wrong SHA stopped it: NOT_DENIED. Everything else (405 not
    mergeable, 422, conflicts, rulesets, 5xx, no response) proves nothing either way.
    """
    if status in (403, 404):
        return "DENIED"
    if status == 409 and "head branch was modified" in (message or "").lower():
        return "NOT_DENIED"
    return "INCONCLUSIVE"


# ------------------------------------------------------------------------ evaluate

def _evaluate_principals(owner: dict, principals: list[dict], now: datetime) -> list[str]:
    findings: list[str] = []
    by_runtime: dict[str, list[dict]] = {}
    for record in principals:
        by_runtime.setdefault(str(record.get("runtime")), []).append(record)
    for runtime in AI_RUNTIMES:
        records = by_runtime.get(runtime) or []
        if not records:
            findings.append(f"IDENTITY_AI_PRINCIPAL_UNMEASURED:{runtime}")
            continue
        if len(records) > 1:
            findings.append(f"IDENTITY_AI_PRINCIPAL_EVIDENCE_AMBIGUOUS:{runtime}")
            continue
        rec = records[0]
        collected, expires = _parse_time(rec.get("collected_at")), _parse_time(rec.get("expires_at"))
        if rec.get("kind") != "ai_principal" or not rec.get("login") or collected is None or expires is None:
            findings.append(f"IDENTITY_AI_PRINCIPAL_EVIDENCE_INVALID:{runtime}")
            continue
        if not owner.get("nonce") or rec.get("nonce") != owner.get("nonce"):
            findings.append(f"IDENTITY_AI_PRINCIPAL_NONCE_MISMATCH:{runtime}")
        if str(rec.get("repository_id")) != str(owner.get("repository_id")):
            findings.append(f"IDENTITY_AI_PRINCIPAL_REPO_MISMATCH:{runtime}")
        if now > expires or expires - collected > MAX_EVIDENCE_TTL or collected > now + timedelta(minutes=5):
            findings.append(f"IDENTITY_AI_PRINCIPAL_EVIDENCE_STALE:{runtime}")
        if rec.get("login") == owner.get("owner_login"):
            findings.append(f"IDENTITY_AI_CREDENTIAL_IS_OWNER_USER:{runtime}")
        verdict = (rec.get("merge_probe") or {}).get("verdict")
        if verdict == "NOT_DENIED":
            findings.append(f"IDENTITY_AI_CREDENTIAL_CAN_MERGE:{runtime}")
        elif verdict != "DENIED":
            findings.append(f"IDENTITY_AI_MERGE_PROBE_INCONCLUSIVE:{runtime}")
    return findings


def _evaluate_live_acceptance(owner: dict, records: list[dict]) -> list[str]:
    findings: list[str] = []
    by_probe: dict[str, dict] = {}
    for rec in records:
        by_probe[str(rec.get("probe"))] = rec
    for probe, expected in LIVE_ACCEPTANCE_PROBES.items():
        rec = by_probe.get(probe)
        if rec is None:
            findings.append(f"PROTECTION_LIVE_PROBE_MISSING:{probe}")
            continue
        if (rec.get("expected") != expected or rec.get("observed") not in ("ALLOW", "DENY")
                or not SHA_RE.match(str(rec.get("head_sha") or ""))
                or not isinstance(rec.get("pr"), int) or _parse_time(rec.get("observed_at")) is None
                or not owner.get("nonce") or rec.get("nonce") != owner.get("nonce")):
            findings.append(f"PROTECTION_LIVE_PROBE_INVALID:{probe}")
        elif rec["observed"] != expected:
            findings.append(f"PROTECTION_LIVE_PROBE_FAILED:{probe}")
    return findings


def evaluate(owner: dict[str, Any], *, principals: list[dict] | None = None,
             live_acceptance: list[dict] | None = None, trust_roots: list[str],
             now: datetime | None = None) -> dict[str, Any]:
    now = now or _utcnow()
    # An unreadable input is its own finding, never silently read as "absent".
    findings: list[str] = [f"IDENTITY_SNAPSHOT_INCOMPLETE:{e}" for e in owner.get("collection_errors") or []]
    if owner.get("kind") != "owner_inventory" or not owner.get("repository_id") or not owner.get("owner_login"):
        findings.append("IDENTITY_SNAPSHOT_INCOMPLETE:OWNER_INVENTORY_INVALID")
    if owner.get("collector_login") != owner.get("owner_login"):
        findings.append("IDENTITY_SNAPSHOT_NOT_OWNER_COLLECTED")
    errors = tuple(owner.get("collection_errors") or [])
    unreadable = lambda *prefixes: any(e.startswith(prefixes) for e in errors)  # noqa: E731

    # --- A/B: distinct, least-privilege App principals installed on THIS repo --
    bindings = owner.get("role_bindings") or {}
    installs = {str(i.get("app_id")): i for i in owner.get("installations") or []}
    bound_ids = {role: str(bindings.get(role) or "") for role in ROLES}
    for role, app_id in bound_ids.items():
        if not app_id:
            if not unreadable("VARIABLES_"):
                findings.append(f"IDENTITY_APP_UNBOUND:{role}")
        elif app_id not in installs:
            if not unreadable("INSTALLATIONS_"):
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
        repo_ids = install.get("repository_ids")
        if install.get("repository_selection") != "selected":
            findings.append(f"IDENTITY_INSTALLATION_NOT_REPO_SCOPED:{role}")
        elif repo_ids is None:
            findings.append(f"IDENTITY_SNAPSHOT_INCOMPLETE:INSTALLATION_REPOSITORIES_MISSING:{role}")
        elif str(owner.get("repository_id")) not in {str(r) for r in repo_ids}:
            findings.append(f"IDENTITY_INSTALLATION_REPO_NOT_INCLUDED:{role}")

    # --- A: every AI runtime, measured from inside that runtime -------------------
    findings += _evaluate_principals(owner, principals or [], now)

    # --- D: CODEOWNERS over every trust root ------------------------------------
    owner_login = str(owner.get("owner_login") or "")
    codeowners = owner.get("codeowners")
    if codeowners is None:
        if owner.get("codeowners_confirmed_absent") is True:
            findings.append("PROTECTION_CODEOWNERS_MISSING")
        elif not unreadable("CODEOWNERS_"):
            findings.append("IDENTITY_SNAPSHOT_INCOMPLETE:CODEOWNERS_UNCONFIRMED")
    elif codeowners is not None:
        # Named trust roots plus probes for NEW files: a newly added privileged workflow
        # or action is a trust root even though no list names it yet.
        probes = [".github/CODEOWNERS", ".github/workflows/__new_workflow__.yml",
                  ".github/actions/__new_action__/action.yml", ".github/scripts/__new_script__.py",
                  ".agents/skills/__new_skill__/SKILL.md"]
        for path in trust_roots + probes:
            if f"@{owner_login}" not in _codeowners_owners(codeowners, path):
                findings.append(f"PROTECTION_CODEOWNERS_UNCOVERED:{path}")

    # --- D: active ruleset on main ----------------------------------------------
    rulesets = [r for r in owner.get("rulesets") or []
                if r.get("enforcement") == "active" and r.get("target", "branch") == "branch" and _targets_main(r)]
    if not rulesets and not unreadable("RULESETS_", "RULESET_DETAIL_"):
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
            elif not bound_ids[role] or str(check.get("integration_id") or "") != bound_ids[role]:
                findings.append(f"PROTECTION_CHECK_NOT_SOURCE_PINNED:{ctx}")
        for actor in (a for r in rulesets for a in r.get("bypass_actors") or []):
            findings.append(f"PROTECTION_BYPASS_PRESENT:{actor.get('actor_type')}:{actor.get('actor_id')}")

    # --- 6: real allow/deny behaviour, not just plausible JSON --------------------
    findings += _evaluate_live_acceptance(owner, live_acceptance or [])

    findings = sorted(set(findings))
    return {
        "status": "PASS" if not findings else "HOLD",
        # PASS is configuration evidence only; it never asserts a work-item closure.
        "disposition": "BLOCKED_WITH_EXACT_REASON" if findings else None,
        "closure_eligible": False,
        "closure_note": "Closing #1550 still requires independent QA acceptance and verified production operation.",
        "findings": findings,
        "merge_or_release_authorized": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


# ------------------------------------------------------------------------- collect

Fetch = Callable[[str], tuple[int, Any]]


def gh_fetch(path: str) -> tuple[int, Any]:
    """GET via the gh CLI. Returns (HTTP status, parsed JSON or None). Prints nothing."""
    proc = subprocess.run(["gh", "api", "-i", path], capture_output=True, text=True)
    return _parse_gh_response(proc.stdout)


def _parse_gh_response(raw: str) -> tuple[int, Any]:
    sep = "\r\n\r\n" if "\r\n\r\n" in raw else "\n\n"
    head, _, body = raw.partition(sep)
    match = re.match(r"HTTP/[\d.]+\s+(\d{3})", head)
    status = int(match.group(1)) if match else 0
    try:
        return status, json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        return status, None


def collect_owner(repo: str, *, fetch: Fetch = gh_fetch, now: datetime | None = None) -> dict[str, Any]:
    """Owner inventory. Read-only; every unreadable or malformed input is typed."""
    now = now or _utcnow()
    errors: list[str] = []
    snap: dict[str, Any] = {"kind": "owner_inventory", "repository": repo, "nonce": uuid.uuid4().hex,
                            "collected_at": _iso(now), "collection_errors": errors}

    status, user = fetch("user")
    if status != 200 or not isinstance(user, dict) or not user.get("login"):
        errors.append(f"COLLECTOR_IDENTITY_UNREADABLE:{status}")
    snap["collector_login"] = (user or {}).get("login") if isinstance(user, dict) else None

    status, meta = fetch(f"repos/{repo}")
    if status != 200 or not isinstance(meta, dict) or not meta.get("id") or not (meta.get("owner") or {}).get("login"):
        errors.append(f"REPOSITORY_UNREADABLE:{status}")
        meta = {}
    snap["repository_id"] = meta.get("id")
    snap["owner_login"] = (meta.get("owner") or {}).get("login")

    status, listing = fetch(f"repos/{repo}/rulesets?per_page=100&includes_parents=true")
    rulesets: list[dict] = []
    if status != 200 or not isinstance(listing, list):
        errors.append(f"RULESETS_UNREADABLE:{status}")
    else:
        if len(listing) >= 100:
            errors.append("RULESETS_TRUNCATED")
        for item in listing:
            s, detail = fetch(f"repos/{repo}/rulesets/{item.get('id')}")
            if s != 200 or not isinstance(detail, dict) or not isinstance(detail.get("rules"), list):
                errors.append(f"RULESET_DETAIL_UNREADABLE:{item.get('id')}:{s}")
            else:
                rulesets.append(detail)
    snap["rulesets"] = rulesets

    snap["codeowners"], snap["codeowners_location"] = None, None
    absent = 0
    for location in CODEOWNERS_LOCATIONS:
        s, content = fetch(f"repos/{repo}/contents/{location}")
        if s == 404:
            absent += 1
            continue
        if s != 200 or not isinstance(content, dict) or content.get("encoding") != "base64":
            errors.append(f"CODEOWNERS_UNREADABLE:{location}:{s}")
            break
        try:
            snap["codeowners"] = base64.b64decode(content.get("content") or "").decode()
        except (ValueError, UnicodeDecodeError):
            errors.append(f"CODEOWNERS_MALFORMED:{location}")
        snap["codeowners_location"] = location
        break
    snap["codeowners_confirmed_absent"] = absent == len(CODEOWNERS_LOCATIONS)

    status, variables = fetch(f"repos/{repo}/actions/variables?per_page=30")
    values: dict[str, Any] = {}
    if status != 200 or not isinstance(variables, dict) or not isinstance(variables.get("variables"), list):
        errors.append(f"VARIABLES_UNREADABLE:{status}")
    else:
        if int(variables.get("total_count") or 0) > len(variables["variables"]):
            errors.append("VARIABLES_TRUNCATED")
        values = {v.get("name"): v.get("value") for v in variables["variables"]}
    snap["role_bindings"] = {role: values.get(f"WOW_{role.upper()}_APP_ID") for role in ROLES}

    status, inst = fetch("user/installations?per_page=100")
    installations: list[dict] = []
    if status != 200 or not isinstance(inst, dict) or not isinstance(inst.get("installations"), list):
        errors.append(f"INSTALLATIONS_UNREADABLE:{status}")
    else:
        if int(inst.get("total_count") or 0) > len(inst["installations"]):
            errors.append("INSTALLATIONS_TRUNCATED")
        bound = {str(v) for v in snap["role_bindings"].values() if v}
        for item in inst["installations"]:
            record = {k: item.get(k) for k in ("id", "app_id", "app_slug", "repository_selection", "permissions")}
            record["repository_ids"] = None
            if str(item.get("app_id")) in bound and item.get("repository_selection") == "selected":
                s, repos = fetch(f"user/installations/{item.get('id')}/repositories?per_page=100")
                if s != 200 or not isinstance(repos, dict) or not isinstance(repos.get("repositories"), list):
                    errors.append(f"INSTALLATION_REPOSITORIES_UNREADABLE:{item.get('app_id')}:{s}")
                elif int(repos.get("total_count") or 0) > len(repos["repositories"]):
                    errors.append(f"INSTALLATION_REPOSITORIES_TRUNCATED:{item.get('app_id')}")
                else:
                    record["repository_ids"] = [r.get("id") for r in repos["repositories"]]
            installations.append(record)
    snap["installations"] = installations
    return snap


def collect_principal(repo: str, *, runtime: str, nonce: str, merge_probe_pr: int | None,
                      fetch: Fetch = gh_fetch, merge_attempt: Callable[[str], tuple[int, str]] | None = None,
                      now: datetime | None = None, ttl: timedelta = timedelta(hours=12)) -> dict[str, Any]:
    """Evidence about the credential of the runtime this runs in.

    The merge probe calls the MUTATING merge endpoint with an all-zero SHA. GitHub's
    SHA guard refuses it, so nothing merges; it is bounded to one call against one
    open PR and is skipped (INCONCLUSIVE) if that PR is not open.
    """
    now = now or _utcnow()
    record: dict[str, Any] = {"kind": "ai_principal", "runtime": runtime, "repository": repo, "nonce": nonce,
                              "collected_at": _iso(now), "expires_at": _iso(now + min(ttl, MAX_EVIDENCE_TTL))}
    status, user = fetch("user")
    record["login"] = user.get("login") if status == 200 and isinstance(user, dict) else None
    record["account_type"] = user.get("type") if isinstance(user, dict) else None
    status, meta = fetch(f"repos/{repo}")
    record["repository_id"] = meta.get("id") if status == 200 and isinstance(meta, dict) else None
    probe: dict[str, Any] = {"pr": merge_probe_pr, "status": None, "verdict": "INCONCLUSIVE"}
    if merge_probe_pr:
        s, pr = fetch(f"repos/{repo}/pulls/{merge_probe_pr}")
        if s == 200 and isinstance(pr, dict) and pr.get("state") == "open" and not pr.get("merged"):
            code, message = (merge_attempt or _gh_merge_attempt)(f"repos/{repo}/pulls/{merge_probe_pr}/merge")
            probe.update(status=code, message=(message or "")[:200], verdict=interpret_merge_probe(code, message))
        else:
            probe["skipped"] = f"PR_NOT_OPEN_OR_UNREADABLE:{s}"
    record["merge_probe"] = probe
    return record


def _gh_merge_attempt(path: str) -> tuple[int, str]:
    proc = subprocess.run(["gh", "api", "-i", "-X", "PUT", path, "-f", "sha=" + "0" * 40],
                          capture_output=True, text=True)
    status, body = _parse_gh_response(proc.stdout)
    return status, (body or {}).get("message", "") if isinstance(body, dict) else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("collect-owner", help="owner inventory (run with the owner's credential)")
    o.add_argument("--repo", required=True)
    p = sub.add_parser("collect-principal", help="AI runtime evidence (run inside that runtime)")
    p.add_argument("--repo", required=True)
    p.add_argument("--runtime", required=True, choices=AI_RUNTIMES)
    p.add_argument("--nonce", required=True, help="nonce from the owner inventory")
    p.add_argument("--merge-probe-pr", type=int, help="open PR for the bounded merge-capability probe")
    e = sub.add_parser("evaluate")
    e.add_argument("--owner", required=True)
    e.add_argument("--principal", action="append", default=[])
    e.add_argument("--live-acceptance")
    e.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[3]))
    sub.add_parser("spec")
    args = parser.parse_args(argv)
    if args.cmd == "collect-owner":
        print(json.dumps(collect_owner(args.repo), indent=2))
        return 0
    if args.cmd == "collect-principal":
        print(json.dumps(collect_principal(args.repo, runtime=args.runtime, nonce=args.nonce,
                                           merge_probe_pr=args.merge_probe_pr), indent=2))
        return 0
    if args.cmd == "spec":
        print(json.dumps({"app_permissions": APP_PERMISSIONS, "forbidden": sorted(FORBIDDEN_PERMISSIONS),
                          "role_checks": ROLE_CHECKS, "regression_checks": REQUIRED_REGRESSION_CHECKS,
                          "ai_runtimes": AI_RUNTIMES, "live_acceptance_probes": LIVE_ACCEPTANCE_PROBES}, indent=2))
        return 0
    owner = json.loads(Path(args.owner).read_text())
    principals = [json.loads(Path(f).read_text()) for f in args.principal]
    live = json.loads(Path(args.live_acceptance).read_text()) if args.live_acceptance else []
    repo_root = Path(args.repo_root)
    result = evaluate(owner, principals=principals, live_acceptance=live,
                      trust_roots=sorted(set(trust_root_paths(repo_root)) | set(governance_trust_roots(repo_root))))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
