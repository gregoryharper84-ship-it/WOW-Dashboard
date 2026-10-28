"""One existing-App-PR exact-source update, never a fourth integration PR.

An explicitly targeted workflow_dispatch only. Preflight has no write token;
the write step runs under the existing Engineering App and global writer mutex.
No approval, merge, deploy, provider release, or probability authority.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

try:
    from . import engineering_authorized_adoption as adoption
except ImportError:
    import engineering_authorized_adoption as adoption


def _app_pr(source_pr: int, source_sha: str, app_number: int) -> tuple[dict, str, str]:
    if (type(app_number) is not int or app_number <= 0
            or type(source_pr) is not int or source_pr <= 0
            or not adoption.SHA.fullmatch(source_sha)):
        raise adoption.AdoptionHold("READOPTION_TARGET_INVALID_HOLD")
    item = adoption.api(f"pulls/{app_number}")
    if not isinstance(item, dict):
        raise adoption.AdoptionHold("READOPTION_PR_UNVERIFIED_HOLD")
    head = item.get("head") if isinstance(item.get("head"), dict) else {}
    base = item.get("base") if isinstance(item.get("base"), dict) else {}
    actor = item.get("user") if isinstance(item.get("user"), dict) else {}
    branch, sha = head.get("ref"), head.get("sha")
    if (item.get("number") != app_number or item.get("state") != "open"
            or item.get("draft") is not False
            or actor.get("login") != adoption.APP_AUTHOR
            or actor.get("id") != 339932922
            or not isinstance(branch, str)
            or not re.fullmatch(
                rf"wow-engineering/exact-replay-{source_pr}-[0-9a-f]{{12}}", branch)
            or not isinstance(sha, str) or not adoption.SHA.fullmatch(sha)
            or (head.get("repo") or {}).get("full_name") != adoption.REPO
            or base.get("ref") != "main"
            or (base.get("repo") or {}).get("full_name") != adoption.REPO):
        raise adoption.AdoptionHold("READOPTION_APP_PR_IDENTITY_HOLD")
    body = item.get("body")
    pattern = rf"(?m)^Original author/source: PR #{source_pr}, head \x60([0-9a-f]{{40}})\x60\.$"
    matches = re.findall(pattern, body if isinstance(body, str) else "")
    if (len(matches) != 1
            or not re.search(rf"(?m)^Integration head: \x60{sha}\x60\.$",
                             body if isinstance(body, str) else "")
            or not re.search(r"(?m)^Incident: #([1-9][0-9]*)$", body or "")):
        raise adoption.AdoptionHold("READOPTION_ORIGINAL_SOURCE_UNPROVEN_HOLD")
    previous_sha = matches[0]
    if previous_sha == source_sha:
        raise adoption.AdoptionHold("READOPTION_NO_SOURCE_DELTA_HOLD")
    return item, previous_sha, branch


def _paths(app_number: int, wanted: list[str]) -> None:
    files = adoption.api(f"pulls/{app_number}/files?per_page=100")
    if (not isinstance(files, list) or len(files) != len(wanted)
            or sorted(row.get("filename") for row in files
                      if isinstance(row, dict)) != wanted):
        raise adoption.AdoptionHold("READOPTION_APP_PATH_INVENTORY_HOLD")


def _prepare(source_pr: int, source_sha: str, app_number: int):
    # Same certified source, full exact-head CI, protected-main ancestry and
    # content-drift checks as ordinary initial adoption.
    main_sha, paths, _ = adoption.preflight(source_pr, source_sha)
    item, previous_sha, branch = _app_pr(source_pr, source_sha, app_number)
    _paths(app_number, paths)
    if not adoption.SHA.fullmatch(item["head"]["sha"]):
        raise adoption.AdoptionHold("READOPTION_HEAD_SHA_INVALID_HOLD")
    # GitHub's compare result establishes source previous->new ancestry.
    proof = adoption.api(f"compare/{previous_sha}...{source_sha}?per_page=100")
    if (not isinstance(proof, dict) or proof.get("status") != "ahead"
            or proof.get("behind_by") != 0
            or (proof.get("merge_base_commit") or {}).get("sha") != previous_sha):
        raise adoption.AdoptionHold("READOPTION_SOURCE_NOT_FAST_FORWARD_HOLD")
    delta = proof.get("files")
    if (not isinstance(delta, list) or not delta or len(delta) >= 300
            or any(not isinstance(row, dict)
                   or row.get("filename") not in paths
                   or row.get("status") not in ("modified", "added")
                   for row in delta)):
        raise adoption.AdoptionHold("READOPTION_SOURCE_DELTA_INVALID_HOLD")
    # No WIP increase: exactly this existing App PR, no alternate replay PR.
    if source_pr == app_number:
        raise adoption.AdoptionHold("READOPTION_SOURCE_AND_APP_PR_SAME_HOLD")
    return main_sha, paths, item, previous_sha, branch


def _git_fetch(sha: str, *, token: str) -> None:
    with tempfile.TemporaryDirectory(prefix="wow-readopt-fetch-") as tmp:
        askpass = Path(tmp) / "askpass"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in *Username*) printf "x-access-token\\n";;'
            ' *) printf "%s\\n" "$GIT_READ_TOKEN";; esac\n')
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass),
                   GIT_READ_TOKEN=token, GIT_TERMINAL_PROMPT="0")
        adoption.call("git", "fetch", "--no-tags", "origin", sha, env=env)


def _verify_app_bytes(app_sha: str, previous_sha: str, paths: list[str]) -> None:
    # The App PR must still be an exact mechanical copy of the last source
    # artifact. Never overwrite an independently edited App implementation.
    for path in paths:
        old = adoption.call("git", "rev-parse", f"{previous_sha}:{path}")
        present = adoption.call("git", "rev-parse", f"{app_sha}:{path}")
        if old != present:
            raise adoption.AdoptionHold("READOPTION_APP_BYTES_DIVERGED_HOLD")


def preflight(source_pr: int, source_sha: str, app_number: int) -> dict:
    if not os.environ.get("GH_TOKEN"):
        raise adoption.AdoptionHold("READOPTION_READ_TOKEN_REQUIRED_HOLD")
    main, paths, pr, old, branch = _prepare(source_pr, source_sha, app_number)
    _git_fetch(pr["head"]["sha"], token=os.environ["GH_TOKEN"])
    _git_fetch(old, token=os.environ["GH_TOKEN"])
    _verify_app_bytes(pr["head"]["sha"], old, paths)
    return {"status": "READOPTION_ADMISSION_PASS", "pr": app_number,
            "source_pr": source_pr, "source_sha": source_sha,
            "existing_app_head": pr["head"]["sha"], "existing_branch": branch,
            "main": main, "can_execute": False}


def readopt(source_pr: int, source_sha: str, app_number: int) -> dict:
    if not os.environ.get("APP_TOKEN") or not os.environ.get("GH_TOKEN"):
        raise adoption.AdoptionHold("READOPTION_APP_AND_READ_TOKEN_REQUIRED_HOLD")
    if os.environ["APP_TOKEN"] == os.environ["GH_TOKEN"]:
        raise adoption.AdoptionHold("READOPTION_PRINCIPALS_NOT_SEPARATED_HOLD")
    if (os.environ.get("WOW_CAN_EXECUTE") != "false"
            or os.environ.get("WOW_DRY_RUN_ONLY") != "true"):
        raise adoption.AdoptionHold("READOPTION_DRY_RUN_INVARIANTS_HOLD")
    evidence = preflight(source_pr, source_sha, app_number)
    main, paths, pr, old, branch = _prepare(source_pr, source_sha, app_number)
    if (main != evidence["main"] or pr["head"]["sha"] != evidence["existing_app_head"]):
        raise adoption.AdoptionHold("READOPTION_PREFLIGHT_CHANGED_HOLD")
    previous_head = pr["head"]["sha"]
    adoption.call("git", "checkout", "-b", "wow-readoption-exact", previous_head)
    adoption.call("git", "restore", "--source", source_sha,
                  "--staged", "--worktree", "--", *paths)
    delta = adoption.call("git", "diff", "--cached", "--name-only").splitlines()
    if not delta or not set(delta).issubset(paths):
        raise adoption.AdoptionHold("READOPTION_DELTA_INCOMPLETE_HOLD")
    adoption.call("git", "diff", "--cached", "--check")
    for path in paths:
        if (adoption.call("git", "rev-parse", f":{path}")
                != adoption.call("git", "rev-parse", f"{source_sha}:{path}")):
            raise adoption.AdoptionHold("READOPTION_SOURCE_BYTES_MISMATCH_HOLD")
    adoption.call("git", "config", "user.name", "github-actions[bot]")
    adoption.call("git", "config", "user.email",
                  "41898282+github-actions[bot]@users.noreply.github.com")
    adoption.call("git", "commit", "-m",
                  f"readopt exact owner PR #{source_pr} source {source_sha}")
    new_sha = adoption.call("git", "rev-parse", "HEAD")
    if adoption.api("git/ref/heads/main").get("object", {}).get("sha") != main:
        raise adoption.AdoptionHold("READOPTION_MAIN_MOVED_BEFORE_PUSH_HOLD")
    fresh, prior, fresh_branch = _app_pr(source_pr, source_sha, app_number)
    if (fresh["head"]["sha"] != previous_head or prior != old
            or fresh_branch != branch):
        raise adoption.AdoptionHold("READOPTION_APP_HEAD_MOVED_BEFORE_PUSH_HOLD")
    if adoption.api(f"pulls/{source_pr}").get("head", {}).get("sha") != source_sha:
        raise adoption.AdoptionHold("READOPTION_SOURCE_MOVED_BEFORE_PUSH_HOLD")

    # Ordinary fast-forward push, never force or force-with-lease.
    with tempfile.TemporaryDirectory(prefix="wow-readopt-push-") as tmp:
        askpass = Path(tmp) / "askpass"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in *Username*) printf "x-access-token\\n";;'
            ' *) printf "%s\\n" "$APP_TOKEN";; esac\n')
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0")
        adoption.call("git", "push", "origin",
                      f"HEAD:refs/heads/{branch}", env=env)
    # Updating metadata never grants security review; old SHA approvals stale.
    old_body = pr["body"]
    updated = re.sub(
        rf"(?m)^Original author/source: PR #{source_pr}, head \x60{old}\x60\.$",
        f"Original author/source: PR #{source_pr}, head \x60{source_sha}\x60.",
        old_body)
    updated = updated.replace(
        f"Integration head: \x60{previous_head}\x60.",
        f"Integration head: \x60{new_sha}\x60.")
    updated += ("\n\nUpdated by the authenticated Engineering App under the"
                " existing single-writer lock. Fresh exact-head independent"
                " semantic review, CODEOWNER, QA/Release and certifier decisions"
                " required; prior approvals and checks do not transfer."
                " No auto-merge or deploy; can_execute=false.\n")
    app_env = dict(os.environ, GH_TOKEN=os.environ["APP_TOKEN"])
    adoption.call("gh", "api", "--method", "PATCH",
                  f"/repos/{adoption.REPO}/pulls/{app_number}",
                  "-f", f"body={updated}", env=app_env)
    readback = adoption.api(f"pulls/{app_number}")
    fresh_source = source_sha
    updated_branch = (readback.get("head") or {}).get("ref")
    # Existing App actor and exact readback are required after the push.
    if (not isinstance(readback, dict)
            or (readback.get("user") or {}).get("login") != adoption.APP_AUTHOR
            or (readback.get("head") or {}).get("sha") != new_sha
            or updated_branch != branch or fresh_source != source_sha
            or readback.get("body") != updated):
        raise adoption.AdoptionHold("READOPTION_POST_PUSH_READBACK_HOLD")
    receipt = adoption.review_boundary_receipt(app_number, new_sha)
    receipt.update(status="PR_UPDATED_PENDING_INDEPENDENT_REVIEW",
                   previous_sha=previous_head, source_sha=source_sha)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("preflight", "readopt"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-pr", type=int, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--app-pr", type=int, required=True)
    args = parser.parse_args()
    if args.repo != adoption.REPO:
        raise adoption.AdoptionHold("READOPTION_REPOSITORY_INVALID_HOLD")
    result = (preflight(args.source_pr, args.source_sha, args.app_pr)
              if args.operation == "preflight"
              else readopt(args.source_pr, args.source_sha, args.app_pr))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
