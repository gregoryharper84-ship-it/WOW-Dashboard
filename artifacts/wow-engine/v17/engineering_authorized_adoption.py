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
import subprocess
import tempfile
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
    return json.loads(call("gh", "api", f"/repos/{REPO}/{endpoint}"))


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



def discover() -> dict:
    """Choose at most one green owner source, from any protected path in repo.

    The hourly controller is already scheduled. This function never writes,
    grants approvals, or treats a typed review HOLD as source CI.
    """
    try:
        from .agent_identity_policy import governance_trust_roots, is_trust_root
    except ImportError:  # Protected-main CLI from within v17/
        from agent_identity_policy import governance_trust_roots, is_trust_root
    roots = frozenset(governance_trust_roots(Path(__file__).resolve().parents[3]))
    considered = 0
    for page in range(1, 5):
        pulls = api(f"pulls?state=open&base=main&per_page=100&page={page}")
        if not isinstance(pulls, list):
            raise AdoptionHold("SOURCE_PR_INVENTORY_INVALID_HOLD")
        for item in sorted(pulls, key=lambda p: p.get("number") or 0, reverse=True):
            if (item.get("user", {}).get("login") != OWNER_LOGIN
                    or item.get("draft") is True
                    or item.get("head", {}).get("repo", {}).get("full_name") != REPO):
                continue
            if considered >= MAX_SCAN:
                return {"status": "NO_ELIGIBLE_SOURCE", "can_execute": False,
                        "detail": "BOUNDED_SOURCE_SCAN"}
            considered += 1
            pr_number = item.get("number")
            sha = (item.get("head") or {}).get("sha")
            if type(pr_number) is not int or not isinstance(sha, str) or not SHA.fullmatch(sha):
                continue
            if type(item.get("changed_files")) is int and item["changed_files"] > MAX_CHANGED_FILES:
                continue
            # Durable duplicate detection by deterministic branch/PR lineage.
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
            # Pull-list summaries omit changed_files; resolve the exact PR
            # resource before checking the inventory and immutable head.
            detail = api(f"pulls/{pr_number}")
            checks = api(f"commits/{sha}/check-runs?per_page=100")
            try:
                validate_evidence(detail, files, checks, requested_pr=pr_number,
                                  requested_sha=sha)
            except AdoptionHold:
                continue
            return {"status": "SOURCE_READY", "pr_number": pr_number,
                    "source_sha": sha, "paths": paths,
                    "can_execute": False}
        if len(pulls) < 100:
            break
    return {"status": "NO_ELIGIBLE_SOURCE", "can_execute": False}


def branch_for(requested_pr: int, sha: str) -> str:
    if requested_pr <= 0 or not SHA.fullmatch(sha):
        raise AdoptionHold("SOURCE_REQUEST_INVALID_HOLD")
    return f"wow-engineering/exact-replay-{requested_pr}-{sha[:12]}"


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
            '#!/bin/sh\\n'
            'case "$1" in *Username*) printf "x-access-token\\\\n";;'
            ' *) printf "%s\\\\n" "$GIT_READ_TOKEN";; esac\\n'
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
    return source_base


def preflight(pr_number: int, source_sha: str) -> tuple[str, list[str], str]:
    pr = api(f"pulls/{pr_number}")
    files = api(f"pulls/{pr_number}/files?per_page=100")
    checks = api(f"commits/{source_sha}/check-runs?per_page=100")
    base, paths = validate_evidence(pr, files, checks,
                                    requested_pr=pr_number, requested_sha=source_sha)
    main_sha = call("git", "rev-parse", "refs/remotes/origin/main")
    source_base = verify_git_source(base, main_sha, source_sha, paths)
    return main_sha, paths, source_base


def integrate(pr_number: int, source_sha: str) -> None:
    if not os.environ.get("APP_TOKEN") or not os.environ.get("GH_TOKEN"):
        raise AdoptionHold("APP_TOKEN_AND_READ_TOKEN_REQUIRED_HOLD")
    if os.environ.get("APP_TOKEN") == os.environ.get("GH_TOKEN"):
        raise AdoptionHold("READ_AND_WRITE_IDENTITY_NOT_SEPARATED_HOLD")
    if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
        raise AdoptionHold("DRY_RUN_INVARIANTS_HOLD")
    main_sha, paths, base = preflight(pr_number, source_sha)
    branch = branch_for(pr_number, source_sha)
    if call("git", "ls-remote", "--heads", "origin", branch):
        raise AdoptionHold("EXACT_REPLAY_BRANCH_ALREADY_EXISTS_HOLD")
    call("git", "checkout", "-b", branch, main_sha)
    call("git", "restore", "--source", source_sha, "--staged", "--worktree", "--", *paths)
    diff = call("git", "diff", "--cached", "--name-only").splitlines()
    if sorted(diff) != paths:
        raise AdoptionHold("REPLAY_CONTENT_DIFF_INCOMPLETE_HOLD")
    call("git", "diff", "--cached", "--check")
    call("git", "config", "user.name", "WOW Engineering App integrator")
    call("git", "config", "user.email", "wow-engineering[bot]@users.noreply.github.com")
    call("git", "commit", "-m", f"integrate(reviewed source): PR #{pr_number} exact {source_sha}")
    integrated_sha = call("git", "rev-parse", "HEAD")
    # Main might move during this process. A changed base is not silently deployed.
    current_main = api("git/ref/heads/main")
    if current_main.get("object", {}).get("sha") != main_sha:
        raise AdoptionHold("MAIN_MOVED_BEFORE_PUSH_HOLD")
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
            f"## Mechanical Engineering App integration\n\n"
            f"Original author/source: PR #{pr_number}, head `{source_sha}`.\n"
            f"Source parent: `{base}`; main at adoption: `{main_sha}`.\n"
            f"Integration head: `{integrated_sha}`.\n"
            f"**The App did not independently author, assess or approve the source code.**\n"
            f"Source changed paths: {len(paths)}; copied byte-for-byte for each path.\n"
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
    if (not isinstance(created, list) or len(created) != 1
            or created[0].get("user", {}).get("login") != APP_AUTHOR
            or created[0].get("head", {}).get("sha") != integrated_sha
            or created[0].get("draft") is True):
        raise AdoptionHold("POST_CREATE_APP_PR_IDENTITY_NOT_VERIFIED_HOLD")
    print(json.dumps({"status": "PR_CREATED_PENDING_INDEPENDENT_REVIEW",
                      "pr": created[0]["number"], "sha": integrated_sha,
                      "source_sha": source_sha, "can_execute": False}, sort_keys=True))


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
        print(json.dumps(discover(), sort_keys=True))
    elif args.operation == "preflight":
        main_sha, paths, base = preflight(args.source_pr, args.source_sha)
        print(json.dumps({"status": "ADOPTABLE", "base": base, "main": main_sha,
                          "source_sha": args.source_sha, "paths": paths,
                          "can_execute": False}, sort_keys=True))
    else:
        integrate(args.source_pr, args.source_sha)


if __name__ == "__main__":
    main()
