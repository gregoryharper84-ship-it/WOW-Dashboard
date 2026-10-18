"""P0 #1845 — preflight and App-only non-force FF, never merge or release.

This trusted-main script refuses stale source/head, foreign PRs, unexpected
paths and unknown adoption identities. GitHub Actions concurrency and the
restricted Engineering environment serialize the writer; external lease
certification remains an independent prerequisite to enabling that environment.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
TARGET_PR = 1834
SOURCE_PR = 1846
TARGET_BRANCH = "copilot/gregoryharper84-ship-itwow-dashboard-1759-adopt-re"
EXPECTED_SOURCE = "58ee0f2763c276c61c1a178bc9a83404facdd63a"
EXPECTED_TARGET = "e80fc36753e9a7b2e3b6fc1843b5028f1248f9fe"
EXPECTED_PATHS = frozenset({
    ".github/workflows/wow-v17-release-fence-postgres-proof.yml",
    ".github/workflows/wow-v17-render-production-deploy.yml",
    "artifacts/wow-engine/migrations/20261018_release_observer_RECOVERY_REVIEW_ONLY.sql",
    "artifacts/wow-engine/migrations/20261018_release_rpc_boundary_REVIEW_ONLY.sql",
    "artifacts/wow-engine/migrations/20261018_release_service_state_fence_REVIEW_ONLY.sql",
    "artifacts/wow-engine/tests/test_render_release_recovery_postgres.py",
    "artifacts/wow-engine/tests/test_render_release_rpc_postgres.py",
    "artifacts/wow-engine/tests/test_render_release_service_fence_candidate.py",
    "artifacts/wow-engine/tests/test_render_release_service_fence_postgres_integration.py",
    "artifacts/wow-engine/tests/test_v17_render_production_deploy_workflow.py",
    "artifacts/wow-engine/tests/test_v17_render_release_provenance_gate.py",
    "artifacts/wow-engine/v17/RELEASE_FENCE_REVIEW_NOT_DEPLOYED.md",
    "artifacts/wow-engine/v17/render_deployment_pointer.py",
    "artifacts/wow-engine/v17/render_release_service_fence_candidate.py",
    "artifacts/wow-engine/v17/test_render_deployment_pointer.py",
})
SHA = re.compile(r"^[0-9a-f]{40}$")


class AdoptionHold(ValueError):
    pass


def run(*args: str, env: dict | None = None) -> str:
    return subprocess.check_output(args, text=True, env=env).strip()


def gh(path: str) -> dict:
    return json.loads(run("gh", "api", f"/repos/{REPO}/{path}"))


def validate_prs(target: dict, source: dict, compare: dict, *,
                 target_sha: str, source_sha: str, branch: str) -> str:
    if not SHA.fullmatch(target_sha) or not SHA.fullmatch(source_sha):
        raise AdoptionHold("SHA_FORMAT_HOLD")
    if target_sha != EXPECTED_TARGET or source_sha != EXPECTED_SOURCE:
        raise AdoptionHold("SOURCE_OR_TARGET_NOT_CERTIFIED_HOLD")
    for pr in (target, source):
        if pr.get("state") != "open" or pr.get("head", {}).get("repo", {}).get("full_name") != REPO:
            raise AdoptionHold("PR_REPOSITORY_OR_STATE_UNTRUSTED_HOLD")
    if (target.get("number") != TARGET_PR or
            target.get("head", {}).get("ref") != branch or
            target.get("base", {}).get("ref") != "main"):
        raise AdoptionHold("ENGINEERING_TARGET_PROVENANCE_HOLD")
    if (source.get("number") != SOURCE_PR or
            source.get("head", {}).get("sha") != source_sha or
            source.get("base", {}).get("ref") != branch):
        raise AdoptionHold("SOURCE_PR_PROVENANCE_HOLD")
    current = target.get("head", {}).get("sha")
    if current == source_sha:
        return "ALREADY_ADOPTED_NO_ACTION"
    if current != target_sha:
        raise AdoptionHold("TARGET_HEAD_STALE_OR_OTHER_WRITER_HOLD")
    if compare.get("status") != "ahead" or compare.get("behind_by") != 0:
        raise AdoptionHold("NON_FAST_FORWARD_OR_MISSING_ANCESTRY_HOLD")
    paths = {f.get("filename") for f in compare.get("files", [])}
    if paths != EXPECTED_PATHS or compare.get("ahead_by", 0) < 1 or compare.get("too_large"):
        raise AdoptionHold("UNEXPECTED_SOURCE_SCOPE_HOLD")
    return "ADOPTABLE"


def plan(*, repo: str, target_pr: int, source_pr: int,
         expected_target: str, expected_source: str, target_branch: str) -> str:
    if repo != REPO or target_pr != TARGET_PR or source_pr != SOURCE_PR or target_branch != TARGET_BRANCH:
        raise AdoptionHold("UNREGISTERED_PR_OR_BRANCH_HOLD")
    target = gh(f"pulls/{target_pr}")
    source = gh(f"pulls/{source_pr}")
    compare = gh(f"compare/{expected_target}...{expected_source}")
    return validate_prs(target, source, compare, target_sha=expected_target,
                        source_sha=expected_source, branch=target_branch)


def adopt(**kwargs) -> str:
    if not os.environ.get("APP_TOKEN") or os.environ.get("GH_TOKEN") != os.environ.get("APP_TOKEN"):
        raise AdoptionHold("AUTHENTICATED_ENGINEERING_APP_TOKEN_MISSING_HOLD")
    if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
        raise AdoptionHold("DRY_RUN_INVARIANT_HOLD")
    decision = plan(**kwargs)
    if decision != "ADOPTABLE":
        return decision
    # Neither candidate nor target code is run with the App token.
    target = kwargs["expected_target"]
    source = kwargs["expected_source"]
    branch = kwargs["target_branch"]
    # A separate GitHub Actions concurrency group serializes worker branches;
    # a competing external writer must also be excluded by authorized operations.
    run("git", "fetch", "--no-tags", "origin", target, source)
    run("git", "merge-base", "--is-ancestor", target, source)
    remote = run("git", "ls-remote", "--heads", "origin", branch).split()
    if len(remote) != 2 or remote[0] != target:
        raise AdoptionHold("REMOTE_BRANCH_DRIFT_HOLD")
    with TemporaryDirectory(prefix="wow-askpass-") as d:
        askpass = Path(d) / "git-askpass"
        askpass.write_text(
            '#!/bin/sh\n'
            'case "$1" in *Username*) printf "x-access-token\\n";;'
            ' *) printf "%s\\n" "$APP_TOKEN";; esac\n')
        askpass.chmod(0o700)
        env = os.environ.copy()
        env["GIT_ASKPASS"] = str(askpass)
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Non-force push rejects branch history loss, including many races.
        run("git", "push", "origin", f"{source}:refs/heads/{branch}", env=env)
    # Never claim write without a readback of the exact PR head.
    after = gh(f"pulls/{TARGET_PR}")
    if after.get("head", {}).get("sha") != source:
        raise AdoptionHold("POST_PUSH_EXACT_HEAD_NOT_VERIFIED_HOLD")
    return "ADOPTED_PENDING_INDEPENDENT_REVIEW"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["preflight", "adopt"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--target-pr", type=int, required=True)
    parser.add_argument("--source-pr", type=int, required=True)
    parser.add_argument("--expected-target", required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--target-branch", required=True)
    args = parser.parse_args()
    kw = {"repo": args.repo, "target_pr": args.target_pr,
          "source_pr": args.source_pr, "expected_target": args.expected_target,
          "expected_source": args.expected_source, "target_branch": args.target_branch}
    result = adopt(**kw) if args.operation == "adopt" else plan(**kw)
    print(result)


if __name__ == "__main__":
    main()
