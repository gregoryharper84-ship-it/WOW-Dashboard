"""Re-propose reviewed owner-authored trust-root patches via an Engineering App token.

Dry run is default. --write creates a *draft* only; no merge, release, deploy,
QA attestation, protected settings, model change or execution permissions.

Run only inside the existing engineering worker with a freshly minted
wow-engineering GitHub App installation token supplied in GH_TOKEN.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = re.compile(r"^[a-f0-9]{40}$")
APP_LOGIN = "wow-engineering[bot]"
SOURCE_ALLOWLIST = {
    1683: frozenset({
        "render.yaml",
        ".github/workflows/wow-engine-verify.yml",
        "artifacts/wow-engine/v17/test_render_interactive_prop_memory_budget.py",
    }),
    1716: frozenset({
        "artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
        "artifacts/wow-engine/tests/test_v17_engineering_dispatch_queue.py",
    }),
}
# Owner-reviewed exact source heads. The App may copy ONLY these bytes: a source
# PR whose head moves after review (any owner-credential push, human or AI) fails
# closed until a protected change re-pins it. PR number + path allowlists alone
# would let the App launder unreviewed bytes into "wow-engineering[bot]" authorship.
SOURCE_PINNED_HEADS = {
    1683: "4e63cedcf4d4005b9a9f4326a6ad447a6e13b079",
    1716: "de943398cdd847c06c6a353f5764325f43f81850",
}
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 4_194_304


class ReproposalBlocked(RuntimeError):
    """Typed refusal; no fallback to an owner credential."""


@dataclass(frozen=True)
class ProposedFile:
    path: str
    content: bytes
    status: str


def validate_pr_metadata(pr: dict, *, number: int, expected_head: str) -> None:
    if number not in SOURCE_ALLOWLIST or not SHA.fullmatch(expected_head):
        raise ReproposalBlocked("SOURCE_NOT_ALLOWLISTED")
    if SOURCE_PINNED_HEADS.get(number) != expected_head:
        raise ReproposalBlocked("SOURCE_HEAD_NOT_OWNER_PINNED")
    if pr.get("number") != number or pr.get("state") != "open" or not pr.get("draft"):
        raise ReproposalBlocked("SOURCE_PR_STATE_CHANGED")
    if (pr.get("user") or {}).get("login") != REPO.split("/")[0]:
        raise ReproposalBlocked("SOURCE_PR_AUTHOR_UNEXPECTED")
    if (pr.get("head") or {}).get("sha") != expected_head:
        raise ReproposalBlocked("SOURCE_HEAD_MOVED")
    if (pr.get("head") or {}).get("repo", {}).get("full_name") != REPO:
        raise ReproposalBlocked("SOURCE_HEAD_FOREIGN_REPOSITORY")
    if (pr.get("base") or {}).get("ref") != "main":
        raise ReproposalBlocked("SOURCE_BASE_NOT_MAIN")
    if not SHA.fullmatch((pr.get("base") or {}).get("sha", "")):
        raise ReproposalBlocked("SOURCE_BASE_SHA_INVALID")


def validate_changes(changes: list[dict], *, source_pr: int) -> list[tuple[str, str]]:
    if not isinstance(changes, list) or len(changes) != len(SOURCE_ALLOWLIST[source_pr]):
        raise ReproposalBlocked("SOURCE_FILE_COUNT_CHANGED")
    result = []
    for change in changes:
        if not isinstance(change, dict):
            raise ReproposalBlocked("SOURCE_FILE_INVALID")
        path, status = change.get("filename"), change.get("status")
        if path not in SOURCE_ALLOWLIST[source_pr] or status not in ("added", "modified"):
            raise ReproposalBlocked("SOURCE_FILE_NOT_ALLOWLISTED")
        result.append((path, status))
    if len(set(path for path, _ in result)) != len(result):
        raise ReproposalBlocked("SOURCE_FILE_DUPLICATED")
    if set(path for path, _ in result) != SOURCE_ALLOWLIST[source_pr]:
        raise ReproposalBlocked("SOURCE_FILE_SET_MISMATCH")
    return sorted(result)


def decode_content(payload: dict | None, *, may_be_absent: bool = False) -> bytes | None:
    if payload is None and may_be_absent:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ReproposalBlocked("CONTENTS_RESPONSE_INVALID")
    if payload.get("encoding") != "base64":
        raise ReproposalBlocked("CONTENTS_ENCODING_INVALID")
    try:
        encoded = payload["content"]
        if not isinstance(encoded, str):
            raise ReproposalBlocked("CONTENTS_DECODE_INVALID")
        blob = base64.b64decode("".join(encoded.split()), validate=True)
    except (TypeError, ValueError, KeyError, binascii.Error) as exc:
        raise ReproposalBlocked("CONTENTS_DECODE_INVALID") from exc
    if len(blob) > MAX_FILE_BYTES or not blob:
        raise ReproposalBlocked("CONTENTS_SIZE_INVALID")
    return blob


def compare_source_and_main(
    files: list[tuple[str, str]],
    *,
    base_contents: dict[str, bytes | None],
    main_contents: dict[str, bytes | None],
    source_contents: dict[str, bytes],
) -> list[ProposedFile]:
    total = 0
    proposal = []
    for path, status in files:
        before, current, updated = (base_contents.get(path), main_contents.get(path),
                                    source_contents.get(path))
        if current != before:
            raise ReproposalBlocked("BASE_FILE_DRIFT:" + path)
        if status == "added" and (before is not None or current is not None):
            raise ReproposalBlocked("NEW_FILE_ALREADY_EXISTS:" + path)
        if status == "modified" and before is None:
            raise ReproposalBlocked("MODIFIED_FILE_MISSING:" + path)
        if not isinstance(updated, bytes) or not updated or len(updated) > MAX_FILE_BYTES:
            raise ReproposalBlocked("SOURCE_CONTENT_INVALID:" + path)
        if before == updated:
            raise ReproposalBlocked("SOURCE_FILE_NOOP:" + path)
        total += len(updated)
        if total > MAX_TOTAL_BYTES:
            raise ReproposalBlocked("SOURCE_TOTAL_SIZE_EXCEEDED")
        proposal.append(ProposedFile(path, updated, status))
    return proposal


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def request(self, verb: str, route: str, data: dict | None = None,
                *, allow_404: bool = False) -> Any:
        if not route.startswith("/") or "://" in route:
            raise ReproposalBlocked("ENDPOINT_INVALID")
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request("https://api.github.com" + route, data=payload, method=verb,
                                     headers={
                                         "Authorization": "Bearer " + self.token,
                                         "Accept": "application/vnd.github+json",
                                         "X-GitHub-Api-Version": "2022-11-28",
                                         "Content-Type": "application/json",
                                     })
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            raise ReproposalBlocked("GITHUB_API_FAILED:" + str(exc.code)) from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise ReproposalBlocked("GITHUB_API_UNAVAILABLE") from exc

    def get(self, path: str, *, allow_404: bool = False) -> Any:
        return self.request("GET", "/repos/" + REPO + path, allow_404=allow_404)

    def post(self, path: str, data: dict) -> Any:
        return self.request("POST", "/repos/" + REPO + path, data)


def require_engineering_installation(api: GitHub) -> None:
    # Reject owner PATs and OAuth/user tokens: the worker must mint an
    # installation token. GitHub installation tokens use the ghs_ prefix.
    if not api.token.startswith("ghs_"):
        raise ReproposalBlocked("ENGINEERING_APP_INSTALLATION_TOKEN_REQUIRED")
    installs = api.request("GET", "/installation/repositories?per_page=100")
    repositories = installs.get("repositories") if isinstance(installs, dict) else None
    if not isinstance(repositories, list) or not any(
        item.get("full_name") == REPO for item in repositories
    ):
        raise ReproposalBlocked("ENGINEERING_INSTALLATION_REPO_UNVERIFIED")


def _source_blob(api: GitHub, path: str, sha: str, *, absent_ok: bool) -> bytes | None:
    from urllib.parse import quote
    result = api.get("/contents/" + quote(path, safe="/") + "?ref=" + sha, allow_404=absent_ok)
    return decode_content(result, may_be_absent=absent_ok)


def build_plan(api: GitHub, source_pr: int, expected_head: str) -> tuple[str, list[ProposedFile]]:
    source = api.get(f"/pulls/{source_pr}")
    validate_pr_metadata(source, number=source_pr, expected_head=expected_head)
    changed = api.get(f"/pulls/{source_pr}/files?per_page=100")
    if source.get("changed_files") != len(changed):
        raise ReproposalBlocked("SOURCE_FILE_LIST_TRUNCATED")
    files = validate_changes(changed, source_pr=source_pr)
    current_ref = api.get("/git/ref/heads/main")
    main_sha = (current_ref.get("object") or {}).get("sha")
    if not isinstance(main_sha, str) or not SHA.fullmatch(main_sha):
        raise ReproposalBlocked("MAIN_SHA_INVALID")
    # A PR's base.sha may move when main advances. Use the server-verified
    # merge base of the exact source head and the pinned current main instead.
    # Otherwise a later main edit to a protected path can be mistaken for
    # the original branch base and silently overwritten by the re-proposal.
    comparison = api.get(f"/compare/{main_sha}...{expected_head}")
    merge_base = comparison.get("merge_base_commit") if isinstance(comparison, dict) else None
    base_sha = merge_base.get("sha") if isinstance(merge_base, dict) else None
    if not isinstance(base_sha, str) or not SHA.fullmatch(base_sha):
        raise ReproposalBlocked("SOURCE_MERGE_BASE_UNVERIFIED")
    source_files = {p: _source_blob(api, p, expected_head, absent_ok=False) for p, _ in files}
    base_files = {p: _source_blob(api, p, base_sha, absent_ok=True) for p, _ in files}
    main_files = {p: _source_blob(api, p, main_sha, absent_ok=True) for p, _ in files}
    return main_sha, compare_source_and_main(
        files, base_contents=base_files, main_contents=main_files,
        source_contents=source_files,
    )


def create_draft(api: GitHub, source_pr: int, source_head: str,
                 main_sha: str, files: list[ProposedFile]) -> dict[str, Any]:
    require_engineering_installation(api)
    latest = api.get("/git/ref/heads/main")
    if (latest.get("object") or {}).get("sha") != main_sha:
        raise ReproposalBlocked("MAIN_HEAD_MOVED_REEVALUATE")
    branch = f"engineering/repropose-{source_pr}-{source_head[:12]}-{main_sha[:12]}"
    existing = api.get("/git/ref/heads/" + branch, allow_404=True)
    if existing is not None:
        from urllib.parse import quote
        refs = api.get("/pulls?state=open&head=" + quote(REPO.split("/")[0] + ":" + branch, safe=""))
        matches = [p for p in refs if isinstance(p, dict)
                   and p.get("draft") is True
                   and (p.get("user") or {}).get("login") == APP_LOGIN
                   and (p.get("head") or {}).get("sha") == (existing.get("object") or {}).get("sha")
                   and f"Source head: {source_head}" in (p.get("body") or "")]
        if len(matches) != 1:
            raise ReproposalBlocked("EXISTING_BRANCH_UNVERIFIED_MANUAL_RECONCILIATION")
        return {
            "state": "DUPLICATE", "number": matches[0]["number"],
            "sha": (existing.get("object") or {}).get("sha"),
            "source": source_pr, "source_head": source_head, "base": main_sha,
            "owner_approval_required": True, "independent_qa_required": True,
            "release_authority_required": True, "can_execute": False,
        }
    base_commit = api.get("/git/commits/" + main_sha)
    base_tree = (base_commit.get("tree") or {}).get("sha")
    if not isinstance(base_tree, str) or not SHA.fullmatch(base_tree):
        raise ReproposalBlocked("MAIN_TREE_INVALID")
    entries = []
    for item in files:
        blob = api.post("/git/blobs", {
            "content": base64.b64encode(item.content).decode(), "encoding": "base64",
        })
        sha = blob.get("sha")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ReproposalBlocked("BLOB_CREATION_UNVERIFIED")
        entries.append({"path": item.path, "mode": "100644", "type": "blob", "sha": sha})
    tree = api.post("/git/trees", {"base_tree": base_tree, "tree": entries})
    if not isinstance(tree.get("sha"), str) or not SHA.fullmatch(tree["sha"]):
        raise ReproposalBlocked("TREE_CREATION_UNVERIFIED")
    commit = api.post("/git/commits", {
        "message": f"fix(governance): app re-proposal of reviewed PR #{source_pr} at {source_head}",
        "tree": tree["sha"], "parents": [main_sha],
    })
    new_sha = commit.get("sha")
    if not isinstance(new_sha, str) or not SHA.fullmatch(new_sha):
        raise ReproposalBlocked("COMMIT_CREATION_UNVERIFIED")
    new_ref = api.post("/git/refs", {"ref": "refs/heads/" + branch, "sha": new_sha})
    if (new_ref.get("ref") != "refs/heads/" + branch
            or (new_ref.get("object") or {}).get("sha") != new_sha):
        raise ReproposalBlocked("BRANCH_REF_UNVERIFIED_DRAFT_HOLD")
    opened = api.post("/pulls", {
        "title": f"fix(governance): Engineering App re-proposal of #{source_pr}",
        "head": branch, "base": "main", "draft": True,
        "body": (f"Exact-source re-proposal of owner-authored PR #{source_pr}. "
                 f"Source head: {source_head}; fresh main: {main_sha}. "
                 "Require independent exact-head QA/Release checks and owner CODEOWNERS "
                 "approval; never self-approve. can_execute=false; dry-run only."),
    })
    # Validate after creating the draft; no ability to merge or approve.
    if (not opened.get("draft") or (opened.get("user") or {}).get("login") != APP_LOGIN
            or (opened.get("head") or {}).get("sha") != new_sha):
        raise ReproposalBlocked("CREATED_PR_IDENTITY_UNVERIFIED_DRAFT_HOLD")
    return {
        "state": "PR_CREATED", "number": opened["number"], "sha": new_sha,
        "source": source_pr, "source_head": source_head, "base": main_sha,
        "owner_approval_required": True, "independent_qa_required": True,
        "release_authority_required": True, "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-pr", type=int, required=True, choices=tuple(SOURCE_ALLOWLIST))
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            raise ReproposalBlocked("GH_TOKEN_MISSING")
        api = GitHub(token)
        main_sha, proposed = build_plan(api, args.source_pr, args.expected_head)
        if args.write:
            result = create_draft(api, args.source_pr, args.expected_head, main_sha, proposed)
        else:
            result = {
                "state": "PREFLIGHT_ONLY", "source": args.source_pr,
                "source_head": args.expected_head, "main": main_sha,
                "files": [f.path for f in proposed], "can_execute": False,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except ReproposalBlocked as exc:
        print(json.dumps({"state": "BLOCKED_WITH_EXACT_REASON",
                          "reason": str(exc), "can_execute": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
