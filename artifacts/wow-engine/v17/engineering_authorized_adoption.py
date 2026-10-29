"""Global, LLM-free Engineering App adoption of reviewed exact-SHA protected source.

A privileged GitHub App copies candidate bytes; it NEVER provides independent
authorship/review of those bytes, merges, approves, or deploys. Reviews/QA/Release
must assess the new App-authored integration PR on its exact resulting head.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
APP_AUTHOR = "wow-engineering[bot]"
SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_ROOTS = (
    ".github/", ".agents/", ".claude/", "artifacts/", "docs/",
    "scripts/", "tests/", "render.yaml", "requirements.txt",
)
OWNER_LOGIN = "gregoryharper84-ship-it"
MAX_CHANGED_FILES = 30
MAX_SCAN = 50
MAX_SOURCE_PAGES = 4
MAX_CHECK_PAGES = 5
REQUIRED_CHECKS = (
    "WOW required-three regression",
    "WOW governed probability backend",
    "WOW additional required regression",
)
ACTIONS_APP_ID = 15368


class AdoptionHold(ValueError):
    pass


def call(*args: str, env: dict | None = None) -> str:
    return subprocess.check_output(args, text=True, env=env).strip()


def api(endpoint: str) -> dict | list:
    """Read GitHub evidence; fail over only a typed installation quota denial.

    The normal token and Engineering App token remain distinct. The App token
    may be used as a bounded GET-only transport fallback after a GitHub Actions
    installation quota denial; this does not create an approval, alter evidence,
    or authorize a write. Every source/PR/SHA/receipt check still applies.
    """
    if (not isinstance(endpoint, str) or not endpoint
            or endpoint.startswith(("/", ".")) or "//" in endpoint
            or any(part == ".." for part in endpoint.split("/"))):
        raise AdoptionHold("GITHUB_EVIDENCE_ENDPOINT_INVALID_HOLD")
    primary = os.environ.get("GH_TOKEN") or ""
    if not primary:
        raise AdoptionHold("GITHUB_EVIDENCE_READ_IDENTITY_MISSING_HOLD")

    def read_with(token: str) -> tuple[subprocess.CompletedProcess[str], str]:
        env = dict(os.environ, GH_TOKEN=token)
        result = subprocess.run(
            ["gh", "api", f"/repos/{REPO}/{endpoint}"],
            text=True, capture_output=True, check=False, env=env,
            timeout=45,
        )
        return result, result.stderr or ""

    try:
        result, reason = read_with(primary)
        if result.returncode != 0:
            quota = ("API rate limit exceeded for installation" in reason
                     and "(HTTP 403)" in reason)
            if not quota:
                raise AdoptionHold("GITHUB_EVIDENCE_READ_DENIED_OR_UNAVAILABLE_HOLD")
            secondary = os.environ.get("APP_TOKEN") or ""
            if not secondary or secondary == primary:
                raise AdoptionHold("GITHUB_INSTALLATION_QUOTA_DEFERRED_HOLD")
            # The alternate credential is invoked with gh api's default GET
            # only. Never pass it to POST/PATCH, GitHub PR approval or merge.
            result, reason = read_with(secondary)
            if result.returncode != 0:
                raise AdoptionHold("GITHUB_ALTERNATE_READ_UNAVAILABLE_HOLD")
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise AdoptionHold("GITHUB_EVIDENCE_RESPONSE_MALFORMED_HOLD") from None
        if type(payload) not in (dict, list):
            raise AdoptionHold("GITHUB_EVIDENCE_RESPONSE_TYPE_INVALID_HOLD")
        return payload
    except subprocess.TimeoutExpired:
        raise AdoptionHold("GITHUB_EVIDENCE_READ_TIMEOUT_HOLD") from None


def source_paths(files: list[dict]) -> list[str]:
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_CHANGED_FILES:
        raise AdoptionHold("SOURCE_FILE_COUNT_INVALID_HOLD")
    paths = []
    for item in files:
        path = item.get("filename")
        if (not isinstance(path, str) or path.startswith("/") or ".." in path.split("/")
                or not any(path.startswith(prefix) for prefix in ALLOWED_ROOTS)
                or path.startswith((".git/", ".env", ".secrets/"))
                or item.get("status") not in ("added", "modified")
                or item.get("previous_filename")):
            raise AdoptionHold("SOURCE_CHANGE_SCOPE_OR_RENAME_HOLD")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise AdoptionHold("DUPLICATE_SOURCE_PATH_HOLD")
    return sorted(paths)


def canonical_source_incident(pr: dict) -> str:
    """Require one user-authored canonical issue before App admission.

    The existing certifier requires Incident: #N; do not guess an issue or
    require a later owner-authored repair of a mechanical App PR body.
    """
    body = pr.get("body")
    if not isinstance(body, str):
        raise AdoptionHold("SOURCE_CANONICAL_INCIDENT_MISSING_HOLD")
    matches = re.findall(
        r"(?im)^[ \t]*Incident:[ \t]*#?([1-9][0-9]*)[ \t]*$", body
    )
    if len(matches) != 1:
        raise AdoptionHold("SOURCE_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS_HOLD")
    return matches[0]


def validate_evidence(
    pr: dict, files: list[dict], checks: dict, *,
    requested_pr: int, requested_sha: str, repository: str = REPO,
) -> tuple[str, list[str]]:
    if repository != REPO or not SHA.fullmatch(requested_sha) or requested_pr <= 0:
        raise AdoptionHold("SOURCE_REQUEST_INVALID_HOLD")
    if (pr.get("number") != requested_pr or pr.get("state") != "open"
            or pr.get("head", {}).get("sha") != requested_sha
            or pr.get("head", {}).get("repo", {}).get("full_name") != REPO
            or pr.get("user", {}).get("login") != OWNER_LOGIN
            or pr.get("draft") is True
            or pr.get("base", {}).get("ref") != "main"
            or pr.get("base", {}).get("repo", {}).get("full_name") != REPO):
        raise AdoptionHold("SOURCE_PR_OR_HEAD_PROVENANCE_HOLD")
    base = pr.get("base", {}).get("sha")
    if not isinstance(base, str) or not SHA.fullmatch(base):
        raise AdoptionHold("SOURCE_BASE_SHA_INVALID_HOLD")
    # Catch metadata that the final protected certifier requires, before
    # allocating an App writer for a doomed exact-source replay.
    canonical_source_incident(pr)
    if pr.get("changed_files") != len(files):
        raise AdoptionHold("SOURCE_FILE_INVENTORY_INCOMPLETE_HOLD")
    paths = source_paths(files)
    runs = checks.get("check_runs", [])
    if not isinstance(runs, list) or checks.get("total_count") != len(runs):
        raise AdoptionHold("SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD")
    for name in REQUIRED_CHECKS:
        matches = [r for r in runs if r.get("name") == name and
                   (r.get("app") or {}).get("id") == ACTIONS_APP_ID and
                   r.get("head_sha") == requested_sha]
        if not matches:
            raise AdoptionHold("SOURCE_REQUIRED_REGRESSION_MISSING_HOLD")
        # Check latest genuine producer result, not an older green or foreign App.
        latest = max(matches, key=lambda r: int(r.get("id") or 0))
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise AdoptionHold("SOURCE_REQUIRED_REGRESSION_RED_HOLD")
    return base, paths



def _exact_head_checks(sha: str) -> dict:
    """Fetch complete bounded GitHub check-run evidence, never a truncated PASS.

    Busy PRs accumulate more than the GitHub API's first 100 checks from reruns
    and matrix jobs. Treat an unreadable or capped inventory as an explicit
    infrastructure hold, not as the absence of a reviewed candidate.
    """
    if not SHA.fullmatch(sha):
        raise AdoptionHold("SOURCE_CHECK_SHA_INVALID_HOLD")
    prefix = f"commits/{sha}/check-runs?per_page=100"
    first = api(prefix)
    if not isinstance(first, dict):
        raise AdoptionHold("SOURCE_CHECK_INVENTORY_INVALID_HOLD")
    total = first.get("total_count")
    rows = first.get("check_runs")
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise AdoptionHold("SOURCE_CHECK_INVENTORY_INVALID_HOLD")
    if total > MAX_CHECK_PAGES * 100 or len(rows) > total:
        raise AdoptionHold("SOURCE_CHECK_INVENTORY_PAGE_CAP_HOLD")
    current = list(rows)
    page = 2
    while len(current) < total and page <= MAX_CHECK_PAGES:
        payload = api(prefix + f"&page={page}")
        if (not isinstance(payload, dict)
                or payload.get("total_count") != total
                or not isinstance(payload.get("check_runs"), list)
                or not payload["check_runs"]):
            raise AdoptionHold("SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD")
        current.extend(payload["check_runs"])
        page += 1
    if len(current) != total:
        raise AdoptionHold("SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD")
    ids = [r.get("id") for r in current if isinstance(r, dict)]
    if (len(ids) != total or
            any(type(x) is not int or x <= 0 for x in ids) or
            len(set(ids)) != total):
        raise AdoptionHold("SOURCE_CHECK_INVENTORY_DUPLICATE_OR_INVALID_HOLD")
    return {"total_count": total, "check_runs": current}


def _rotating_owner_window(pulls: list[dict], *, slot: int) -> tuple[list[dict], int]:
    """Bound API detail checks while eventually inspecting every owner PR.

    Sorting by PR number plus the hour slot is deterministic across retries in
    the same hour. Rechecking the first 50 newest PRs forever used to starve
    older valid protected repairs. Rotation never grants approval or dispatch.
    """
    owners = sorted(
        (p for p in pulls
         if isinstance(p, dict)
         and p.get("user", {}).get("login") == OWNER_LOGIN
         and p.get("state") == "open"
         and p.get("draft") is False
         and p.get("head", {}).get("repo", {}).get("full_name") == REPO
         and type(p.get("number")) is int),
        key=lambda p: p["number"],
    )
    windows = max(1, (len(owners) + MAX_SCAN - 1) // MAX_SCAN)
    start = (slot % windows) * MAX_SCAN
    return owners[start:start + MAX_SCAN], windows



def _adoption_candidate_window(pulls: list[dict], *, slot: int) -> tuple[list[dict], int]:
    """Prioritize urgent *scheduling*, never bypass source/CI/review proof.

    Two of three scans reserve up to ten slots for P0/P1 source PRs; each
    third scan retains the original complete rotating 50-PR window. This
    guarantees older routine sources still receive a full fair scan and
    prevents a flood of P0s from creating new permanent starvation.
    Labels/titles are untrusted scheduling hints, not security evidence.
    """
    # Fair-cycle index is independent of priority cadence: all owner windows
    # are visited even when window count shares factors with three.
    fair, windows = _rotating_owner_window(
        pulls, slot=(slot // 3 if slot % 3 == 0 else slot)
    )
    if slot % 3 == 0:
        return fair, windows
    owner_candidates = [
        p for p in pulls
        if isinstance(p, dict) and p.get("user", {}).get("login") == OWNER_LOGIN
        and p.get("state") == "open" and p.get("draft") is False
        and p.get("head", {}).get("repo", {}).get("full_name") == REPO
        and type(p.get("number")) is int
    ]
    def priority(p: dict) -> int:
        labels = p.get("labels")
        names = {
            item.get("name", "").strip().upper()
            for item in (labels if isinstance(labels, list) else [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        title = str(p.get("title") or "").upper()
        if "P0" in names or title.startswith(("FIX(P0", "[P0]", "P0:")):
            return 0
        if "P1" in names or title.startswith(("FIX(P1", "[P1]", "P1:")):
            return 1
        return 2
    urgent = sorted(
        (p for p in owner_candidates if priority(p) < 2),
        key=lambda p: (priority(p), p["number"]),
    )
    urgent_cap = min(10, MAX_SCAN)
    groups = max(1, (len(urgent) + urgent_cap - 1) // urgent_cap)
    start = ((slot // 3) % groups) * urgent_cap
    fast = urgent[start:start + urgent_cap]
    seen: set[int] = set()
    selected: list[dict] = []
    for row in [*fast, *fair]:
        if row["number"] in seen:
            continue
        seen.add(row["number"])
        selected.append(row)
        if len(selected) >= MAX_SCAN:
            break
    return selected, windows



def _source_paths_unchanged_on_main(*, source_sha: str, main_sha: str,
                                    paths: list[str]) -> None:
    """Read-only main-drift audit, matching verify_git_source's merge-base rule.

    The compare endpoint's file list represents diff(merge_base, main_sha),
    even when GitHub says the PR is textually mergeable. GitHub caps this list
    at 300; a saturated/unknown list cannot certify a clean source.
    """
    if not SHA.fullmatch(source_sha) or not SHA.fullmatch(main_sha):
        raise AdoptionHold("SOURCE_MAIN_COMPARE_SHA_INVALID_HOLD")
    evidence = api(f"compare/{source_sha}...{main_sha}?per_page=100")
    if (not isinstance(evidence, dict) or
            (evidence.get("base_commit") or {}).get("sha") != source_sha or
            not SHA.fullmatch(str((evidence.get("merge_base_commit") or {}).get("sha") or "")) or
            evidence.get("status") not in ("ahead", "behind", "diverged", "identical")):
        raise AdoptionHold("SOURCE_MAIN_COMPARE_UNVERIFIED_HOLD")
    files = evidence.get("files")
    if not isinstance(files, list) or len(files) >= 300:
        raise AdoptionHold("SOURCE_MAIN_COMPARE_FILE_CAP_HOLD")
    changed: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise AdoptionHold("SOURCE_MAIN_COMPARE_FILE_INVALID_HOLD")
        changed.add(item["filename"])
        previous = item.get("previous_filename")
        if previous is not None:
            if not isinstance(previous, str):
                raise AdoptionHold("SOURCE_MAIN_COMPARE_FILE_INVALID_HOLD")
            changed.add(previous)
    if changed.intersection(paths):
        raise AdoptionHold("SOURCE_PATH_DRIFT_DEFERRED")


def discover(*, slot: int | None = None, protected_main_sha: str | None = None) -> dict:
    """Choose at most one green owner source, from any protected path in repo.

    The hourly controller is already scheduled. This function never writes,
    grants approvals, or treats a typed review HOLD as source CI.
    """
    try:
        from .agent_identity_policy import governance_trust_roots, is_trust_root
    except ImportError:  # Protected-main CLI from within v17/
        from agent_identity_policy import governance_trust_roots, is_trust_root
    roots = frozenset(governance_trust_roots(Path(__file__).resolve().parents[3]))
    # Production CLI supplies the actual checked-out protected-main head.
    # GitHub PR base.sha is a historical PR snapshot and may remain stale
    # after main advances; it MUST NOT replace live main in source-drift checks.
    if protected_main_sha is not None and not SHA.fullmatch(protected_main_sha):
        raise AdoptionHold("DISCOVERY_MAIN_SHA_INVALID_HOLD")
    # Global backpressure: don't produce more protected PRs than the existing
    # review queue can independently certify. No indefinite approval loops.
    live = api("pulls?state=open&base=main&per_page=100")
    if not isinstance(live, list):
        raise AdoptionHold("ACTIVE_APP_PR_INVENTORY_INVALID_HOLD")
    pending = sum(
        p.get("user", {}).get("login") == APP_AUTHOR and
        (p.get("head") or {}).get("ref", "").startswith("wow-engineering/exact-replay-")
        for p in live
    )
    if pending >= 3:
        return {"status": "NO_ELIGIBLE_SOURCE", "detail": "APP_REVIEW_WIP_CAP",
                "can_execute": False}
    # Read the bounded current inventory before choosing an hourly window.
    # Never infer source CI or trust-root eligibility from list metadata alone.
    all_pulls: list[dict] = []
    for page in range(1, MAX_SOURCE_PAGES + 1):
        pulls = api(f"pulls?state=open&base=main&per_page=100&page={page}")
        if not isinstance(pulls, list):
            raise AdoptionHold("SOURCE_PR_INVENTORY_INVALID_HOLD")
        all_pulls.extend(pulls)
        if len(pulls) < 100:
            break
    else:
        # On a 400+ PR inventory, avoid an unfair partial scan. Expand the
        # authenticated pagination contract rather than ignoring older rows.
        return {"status": "NO_ELIGIBLE_SOURCE",
                "detail": "SOURCE_PR_INVENTORY_PAGE_CAP",
                "can_execute": False}

    # Respect global App-review WIP across every fetched page, not just the
    # GitHub first page; three pending adoption PRs do not license a fourth.
    pending = sum(
        p.get("user", {}).get("login") == APP_AUTHOR and
        (p.get("head") or {}).get("ref", "").startswith("wow-engineering/exact-replay-")
        for p in all_pulls
    )
    if pending >= 3:
        return {"status": "NO_ELIGIBLE_SOURCE", "detail": "APP_REVIEW_WIP_CAP",
                "can_execute": False}

    if slot is None:
        slot = int(datetime.now(timezone.utc).timestamp() // 3600)
    if type(slot) is not int or slot < 0:
        raise AdoptionHold("SCAN_SLOT_INVALID_HOLD")
    owner_window, windows = _adoption_candidate_window(all_pulls, slot=slot)
    for item in owner_window:
        pr_number = item.get("number")
        sha = (item.get("head") or {}).get("sha")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            continue
        if type(item.get("changed_files")) is int and item["changed_files"] > MAX_CHANGED_FILES:
            continue
        branch = branch_for(pr_number, sha)
        existing = api(f"pulls?state=all&head={REPO.split('/')[0]}:{branch}&per_page=10")
        if not isinstance(existing, list):
            raise AdoptionHold("REPLAY_PR_INVENTORY_INVALID_HOLD")
        if existing:
            continue
        files = api(f"pulls/{pr_number}/files?per_page=100")
        if not isinstance(files, list):
            raise AdoptionHold("SOURCE_FILE_INVENTORY_INVALID_HOLD")
        try:
            paths = source_paths(files)
        except AdoptionHold:
            continue
        if not any(is_trust_root(path, roots) for path in paths):
            continue
        detail = api(f"pulls/{pr_number}")
        # A definitive GitHub merge conflict cannot be repaired by copying
        # the stale source bytes over protected main. Move to the next owner
        # source in this bounded window instead of repeating a doomed job.
        # A null/unknown mergeability result is not proof of acceptance;
        # strict exact-head and path checks remain in the integration preflight.
        if detail.get("mergeable") is not True:
            # null is a pending GitHub mergeability calculation, not safe proof.
            continue
        checks = _exact_head_checks(sha)
        try:
            source_base_sha, _ = validate_evidence(
                detail, files, checks,
                requested_pr=pr_number, requested_sha=sha)
            # The checkout's current protected-main SHA governs the actual
            # source-to-main drift and immutable Git verification. Retain
            # base.sha only as historical provenance for the PR.
            current_main_sha = protected_main_sha or source_base_sha
            _source_paths_unchanged_on_main(
                source_sha=sha, main_sha=current_main_sha, paths=paths)
            # GitHub mergeable=true/required CI may still accompany a PR with
            # invalid patch whitespace. Verify exact git diff read-only now,
            # so a bad candidate cannot consume the implementation writer.
            verify_git_source(source_base_sha, current_main_sha, sha, paths)
        except (AdoptionHold, subprocess.CalledProcessError) as exc:
            # The exact same source path drift remains a hard HOLD at
            # integration. Emit a typed read-only diagnosis while scanning
            # the remaining candidates; no implementation writer is acquired.
            if str(exc) in ("SOURCE_PATH_DRIFT_DEFERRED",
                            "SOURCE_MAIN_COMPARE_FILE_CAP_HOLD",
                            "SOURCE_MAIN_COMPARE_UNVERIFIED_HOLD",
                            "SOURCE_PATCH_WHITESPACE_OR_GIT_CHECK_HOLD") or isinstance(
                                exc, subprocess.CalledProcessError):
                print(f"{exc}: source_pr={pr_number} source_sha={sha}",
                      file=sys.stderr)
            continue
        return {"status": "SOURCE_READY", "pr_number": pr_number,
                "source_sha": sha, "paths": paths,
                "can_execute": False}
    # Every bounded window gets a turn; no permanent newest-first starvation.
    return {"status": "NO_ELIGIBLE_SOURCE",
            "detail": "ROTATING_SOURCE_WINDOW" if windows > 1 else "NO_ELIGIBLE_SOURCE",
            "can_execute": False}


def branch_for(requested_pr: int, sha: str) -> str:
    if requested_pr <= 0 or not SHA.fullmatch(sha):
        raise AdoptionHold("SOURCE_REQUEST_INVALID_HOLD")
    return f"wow-engineering/exact-replay-{requested_pr}-{sha[:12]}"


def check_source_patch_whitespace(main_sha: str, source_sha: str,
                                  paths: list[str]) -> None:
    """Fail before writer admission if an exact-source replay fails git --check.

    The exact paths must be unchanged from their merge base on protected main.
    Check main..source on those paths, which is the staged diff produced by
    replay onto main. Exit status 2 indicates whitespace errors. Preserve the
    later staged git check as an independent last line of defense.
    """
    if (not SHA.fullmatch(main_sha) or not SHA.fullmatch(source_sha)
            or not paths or any(not isinstance(p, str) or not p for p in paths)):
        raise AdoptionHold("SOURCE_PATCH_CHECK_ARGUMENTS_INVALID_HOLD")
    result = subprocess.run(
        ["git", "diff", "--check", main_sha, source_sha, "--", *paths],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise AdoptionHold("SOURCE_PATCH_WHITESPACE_OR_GIT_CHECK_HOLD")


def verify_git_source(base: str, main_sha: str, source_sha: str, paths: list[str]) -> str:
    if not all(SHA.fullmatch(sha) for sha in (base, main_sha, source_sha)):
        raise AdoptionHold("GIT_SHA_INVALID_HOLD")
    # Checkout never persists credentials. Fetch source refs with the readonly
    # token via a short-lived askpass helper; do not expose it to the source tree.
    read_token = os.environ.get("GH_TOKEN")
    if not read_token:
        raise AdoptionHold("READ_TOKEN_REQUIRED_FOR_SOURCE_FETCH_HOLD")
    with tempfile.TemporaryDirectory(prefix="wow-readonly-fetch-") as temp:
        askpass = Path(temp) / "askpass"
        askpass.write_text(
            '#!/bin/sh\n'
            'case "$1" in *Username*) printf "x-access-token\\n";;'
            ' *) printf "%s\\n" "$GIT_READ_TOKEN";; esac\n'
        )
        askpass.chmod(0o700)
        environment = dict(os.environ, GIT_ASKPASS=str(askpass),
                           GIT_READ_TOKEN=read_token, GIT_TERMINAL_PROMPT="0")
        call("git", "fetch", "--no-tags", "origin", source_sha, main_sha,
             env=environment)
    if call("git", "rev-parse", "refs/remotes/origin/main") != main_sha:
        raise AdoptionHold("MAIN_MOVED_DURING_PREFLIGHT_HOLD")
    # GitHub PR base.sha tracks current main and is not the original branch
    # fork-point. Recalculate the actual source merge-base on each dispatch.
    try:
        source_base = call("git", "merge-base", main_sha, source_sha)
    except subprocess.CalledProcessError as exc:
        raise AdoptionHold("SOURCE_ANCESTRY_OR_MAIN_DRIFT_HOLD") from exc
    if not SHA.fullmatch(source_base):
        raise AdoptionHold("SOURCE_BASE_INVALID_HOLD")
    actual = call("git", "diff", "--name-only", source_base, source_sha).splitlines()
    if sorted(actual) != paths:
        raise AdoptionHold("SOURCE_DIFF_NOT_EXACT_HOLD")
    for path in paths:
        source_mode = call("git", "ls-tree", source_sha, "--", path).split()
        if not source_mode or source_mode[0] != "100644":
            raise AdoptionHold("SOURCE_FILE_MODE_UNTRUSTED_HOLD")
        diff = subprocess.run(["git", "diff", "--quiet", source_base, main_sha, "--", path],
                              check=False, capture_output=True)
        if diff.returncode != 0:
            raise AdoptionHold("MAIN_CHANGED_SOURCE_PATH_HOLD")
    # Preserve exactly the same whitespace policy enforced again on the
    # eventual staged PR inside the authenticated App writer.
    check_source_patch_whitespace(main_sha, source_sha, paths)
    return source_base


def preflight(pr_number: int, source_sha: str) -> tuple[str, list[str], str]:
    pr = api(f"pulls/{pr_number}")
    files = api(f"pulls/{pr_number}/files?per_page=100")
    checks = _exact_head_checks(source_sha)
    base, paths = validate_evidence(pr, files, checks,
                                    requested_pr=pr_number, requested_sha=source_sha)
    main_sha = call("git", "rev-parse", "refs/remotes/origin/main")
    source_base = verify_git_source(base, main_sha, source_sha, paths)
    return main_sha, paths, source_base


def validate_created_pr(
    pulls: list[dict], *, branch: str, integrated_sha: str, main_sha: str,
) -> dict:
    if not isinstance(pulls, list) or len(pulls) != 1:
        raise AdoptionHold("POST_CREATE_APP_PR_INVENTORY_HOLD")
    pr = pulls[0]
    if not isinstance(pr, dict):
        raise AdoptionHold("POST_CREATE_APP_PR_METADATA_INVALID_HOLD")
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    author = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    checks = (
        (type(pr.get("number")) is int and pr["number"] > 0,
         "POST_CREATE_APP_PR_NUMBER_INVALID_HOLD"),
        (pr.get("state") == "open" and pr.get("draft") is False,
         "POST_CREATE_APP_PR_NOT_OPEN_HOLD"),
        (author.get("login") == APP_AUTHOR, "POST_CREATE_APP_PR_ACTOR_MISMATCH_HOLD"),
        (head_repo.get("full_name") == REPO,
         "POST_CREATE_APP_PR_HEAD_REPOSITORY_MISMATCH_HOLD"),
        (head.get("ref") == branch, "POST_CREATE_APP_PR_BRANCH_MISMATCH_HOLD"),
        (head.get("sha") == integrated_sha, "POST_CREATE_APP_PR_HEAD_MISMATCH_HOLD"),
        (base_repo.get("full_name") == REPO,
         "POST_CREATE_APP_PR_BASE_REPOSITORY_MISMATCH_HOLD"),
        (base.get("ref") == "main", "POST_CREATE_APP_PR_BASE_REF_MISMATCH_HOLD"),
        (base.get("sha") == main_sha, "POST_CREATE_APP_PR_BASE_SHA_MISMATCH_HOLD"),
    )
    for valid, reason in checks:
        if not valid:
            raise AdoptionHold(reason)
    return pr


def review_boundary_receipt(pr_number: int, integrated_sha: str) -> dict:
    return {
        "status": "PR_CREATED_PENDING_INDEPENDENT_REVIEW",
        "bootstrap_status": "OWNER_EXACT_HEAD_APPROVAL_REQUIRED",
        "source_review_status": "REQUIRED_PENDING",
        "source_review_workflow": "wow-v17-independent-qa-check",
        "source_review_head_sha": integrated_sha,
        "independent_source_review_complete": False,
        "required_owner_login": OWNER_LOGIN,
        "required_owner_approval_head": integrated_sha,
        "pr": pr_number,
        "sha": integrated_sha,
        "can_execute": False,
    }


def integrate(pr_number: int, source_sha: str) -> None:
    if not os.environ.get("APP_TOKEN") or not os.environ.get("GH_TOKEN"):
        raise AdoptionHold("APP_TOKEN_AND_READ_TOKEN_REQUIRED_HOLD")
    if os.environ.get("APP_TOKEN") == os.environ.get("GH_TOKEN"):
        raise AdoptionHold("READ_AND_WRITE_IDENTITY_NOT_SEPARATED_HOLD")
    if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
        raise AdoptionHold("DRY_RUN_INVARIANTS_HOLD")
    main_sha, paths, base = preflight(pr_number, source_sha)
    source_before = api(f"pulls/{pr_number}")
    incident = canonical_source_incident(source_before)
    if source_before.get("head", {}).get("sha") != source_sha:
        raise AdoptionHold("SOURCE_MOVED_BEFORE_WRITER_HOLD")
    branch = branch_for(pr_number, source_sha)
    # Resolve branch existence with a read-only authenticated askpass, not
    # a credential-less git ls-remote (private repos would fail every time).
    with tempfile.TemporaryDirectory(prefix="wow-replay-check-") as temp:
        askpass = Path(temp) / "askpass"
        askpass.write_text(
            '#!/bin/sh\n'
            'case "$1" in *Username*) printf "x-access-token\\n";;'
            ' *) printf "%s\\n" "$GIT_READ_TOKEN";; esac\n'
        )
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass),
                   GIT_READ_TOKEN=os.environ["GH_TOKEN"], GIT_TERMINAL_PROMPT="0")
        if call("git", "ls-remote", "--heads", "origin", branch, env=env):
            raise AdoptionHold("EXACT_REPLAY_BRANCH_ALREADY_EXISTS_HOLD")
    call("git", "checkout", "-b", branch, main_sha)
    call("git", "restore", "--source", source_sha, "--staged", "--worktree", "--", *paths)
    diff = call("git", "diff", "--cached", "--name-only").splitlines()
    if sorted(diff) != paths:
        raise AdoptionHold("REPLAY_CONTENT_DIFF_INCOMPLETE_HOLD")
    call("git", "diff", "--cached", "--check")
    call("git", "config", "user.name", "github-actions[bot]")
    call("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    call("git", "commit", "-m", f"integrate(reviewed source): PR #{pr_number} exact {source_sha}")
    integrated_sha = call("git", "rev-parse", "HEAD")
    # Main might move during this process. A changed base is not silently deployed.
    current_main = api("git/ref/heads/main")
    if current_main.get("object", {}).get("sha") != main_sha:
        raise AdoptionHold("MAIN_MOVED_BEFORE_PUSH_HOLD")
    current_source = api(f"pulls/{pr_number}")
    if (current_source.get("state") != "open" or
            current_source.get("head", {}).get("sha") != source_sha):
        raise AdoptionHold("SOURCE_MOVED_BEFORE_PUSH_HOLD")
    if canonical_source_incident(current_source) != incident:
        raise AdoptionHold("SOURCE_INCIDENT_MOVED_BEFORE_PUSH_HOLD")
    with tempfile.TemporaryDirectory(prefix="wow-exact-adoption-") as temp:
        askpass = Path(temp) / "askpass"
        askpass.write_text(
            '#!/bin/sh\n'
            'case "$1" in *Username*) printf "x-access-token\\n";;'
            ' *) printf "%s\\n" "$APP_TOKEN";; esac\n'
        )
        askpass.chmod(0o700)
        environment = dict(os.environ, GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0")
        call("git", "push", "origin", f"HEAD:refs/heads/{branch}", env=environment)
    with tempfile.TemporaryDirectory(prefix="wow-adoption-body-") as temp:
        body = Path(temp) / "body.md"
        body.write_text(
            f"Incident: #{incident}\n\n"
            f"## Mechanical Engineering App integration\n\n"
            f"Original author/source: PR #{pr_number}, head `{source_sha}`.\n"
            f"Source parent: `{base}`; main at adoption: `{main_sha}`.\n"
            f"Integration head: `{integrated_sha}`.\n"
            f"**The App did not independently author, assess or approve the source code.**\n"
            f"Source changed paths: {len(paths)}; copied byte-for-byte for each path.\n"
            f"Independent exact-head source review is still required; source CI and this "
            f"copy are not review evidence.\n"
            f"Indispensable bootstrap action: `{OWNER_LOGIN}` must approve exact head "
            f"`{integrated_sha}`; no self-approval is performed.\n"
            f"Mandatory next gates: independent Security/Architect, exact-head QA App, "
            f"Release Authority App, CODEOWNER approval and protected merge.\n"
            f"No auto-merge, deploy, probability change, or orders. "
            f"can_execute=false.\n"
        )
        # Only this mutating call receives the short-lived Engineering App token.
        app_env = dict(os.environ, GH_TOKEN=os.environ["APP_TOKEN"])
        call("gh", "pr", "create", "--repo", REPO, "--base", "main",
             "--head", branch, "--title", f"Engineering App: exact source #{pr_number} integration",
             "--body-file", str(body), env=app_env)
    created = api(f"pulls?state=open&head={REPO.split('/')[0]}:{branch}")
    verified = validate_created_pr(
        created, branch=branch, integrated_sha=integrated_sha, main_sha=main_sha)
    receipt = review_boundary_receipt(verified["number"], integrated_sha)
    receipt["source_sha"] = source_sha
    print(json.dumps(receipt, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("discover", "preflight", "integrate"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-pr", type=int, default=0)
    parser.add_argument("--source-sha", default="")
    args = parser.parse_args()
    if args.repo != REPO:
        raise AdoptionHold("REPOSITORY_NOT_AUTHORIZED_HOLD")
    if args.operation != "discover" and (args.source_pr <= 0 or not SHA.fullmatch(args.source_sha)):
        raise AdoptionHold("SOURCE_REQUEST_INVALID_HOLD")
    if args.operation == "discover":
        # One authenticated protected-main checkout supplies a consistent
        # exact SHA for every candidate in this one discovery cycle.
        protected_main_sha = call("git", "rev-parse", "refs/remotes/origin/main")
        print(json.dumps(discover(protected_main_sha=protected_main_sha), sort_keys=True))
    elif args.operation == "preflight":
        main_sha, paths, base = preflight(args.source_pr, args.source_sha)
        print(json.dumps({"status": "ADOPTABLE", "base": base, "main": main_sha,
                          "source_sha": args.source_sha, "paths": paths,
                          "can_execute": False}, sort_keys=True))
    else:
        integrate(args.source_pr, args.source_sha)


if __name__ == "__main__":
    main()
