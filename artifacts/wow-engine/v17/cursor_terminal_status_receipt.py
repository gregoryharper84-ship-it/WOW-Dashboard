"""Read-only terminal status receipt for the existing WOW Cursor handoff."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request

try:
    from .cursor_cloud_handoff import agent_id as deterministic_agent_id
except ImportError:
    from cursor_cloud_handoff import agent_id as deterministic_agent_id


REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
PR_NUMBER = 2090
EXPECTED_HEAD = "85fdd54921fa92220b433d4fa71cd3399bca8385"
AGENT_ID = "bc-c8a71883-d1ab-593a-8e92-2f7779436cb4"
RUN_ID = "run-7b568d98-de5b-4cce-b2a4-e7743fd43787"
CURSOR_API = "https://api.cursor.com/v1"
GITHUB_API = "https://api.github.com"
TERMINAL_RUN_STATES = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
PROTECTED_BRANCHES = frozenset({"main", "master"})
# One immutable post-cutover reservation was written by the broken producer.
# A separately reviewed Class P source attestation binds only this exact
# bot-authored comment/digest and authenticated dispatcher to its PR branch.
# This NEVER substitutes for the live PR, Cursor 404, archived inventories,
# fresh second probe, or the protected successful finalizer receipt.
EXACT_POST_CUTOVER_BRANCH_WITNESS = {
    "comment_id": 6553867635,
    "digest": "e5ee1bfd536f4ee7d25747ee10b14d0eb819d4dc11e569752425843cb015af62",
    "incident": "1988",
    "pr_number": "2211",
    "source_sha": "27e1a86262d80e032ca2e8c8fb64430c137b6a9a",
    "main_sha": "8857e4882f85f01dc47c78ee94a5318398289179",
    "worker_id": "github-actions:41276607293:1",
    "agent_id": "bc-df9cd4ca-0f45-5d5d-ac25-d500977be83e",
    "branch": "fix/nba-exact-official-player-settlement-20261028",
}

MAX_NEGATIVE_INVENTORY_PAGES = 20
INVENTORY_PAGE_SIZE = 100
NEXT_CURSOR_RE = re.compile(r"^[A-Za-z0-9_.:=-]{1,256}$")
EXPECTED_FILES = {
    ".github/workflows/wow-v17-independent-qa-check.yml",
    "artifacts/wow-engine/docs/failure_codes.md",
    "artifacts/wow-engine/test_v17_independent_release_checks.py",
    "artifacts/wow-engine/v17/independent_release_checks.py",
    "docs/wow/engineering/qa_release_event_identity_repro_2054.md",
}


class ReceiptBlocked(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def api_json(url: str, *, token: str, basic: bool = False) -> dict | list:
    authorization = (
        "Basic " + base64.b64encode((token + ":").encode()).decode()
        if basic else "Bearer " + token
    )
    req = request.Request(
        url,
        headers={
            "Authorization": authorization,
            "Accept": "application/json",
            "User-Agent": "wow-v17-cursor-terminal-status-receipt",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise ReceiptBlocked(f"STATUS_API_HTTP_{exc.code}") from None
    except (error.URLError, TimeoutError, ValueError):
        raise ReceiptBlocked("STATUS_API_RESPONSE_UNVERIFIED") from None
    if not isinstance(result, (dict, list)):
        raise ReceiptBlocked("STATUS_API_RESPONSE_UNVERIFIED")
    return result


def _require_object(result: object, code: str) -> dict:
    if not isinstance(result, dict):
        raise ReceiptBlocked(code)
    return result


def _verify_pr(pr: dict) -> None:
    head = pr.get("head")
    base = pr.get("base")
    if (pr.get("number") != PR_NUMBER or pr.get("state") != "open"
            or not isinstance(head, dict) or not isinstance(base, dict)):
        raise ReceiptBlocked("SOURCE_PR_IDENTITY_UNVERIFIED")
    head_repo = head.get("repo")
    base_repo = base.get("repo")
    if (not isinstance(head_repo, dict) or not isinstance(base_repo, dict)
            or head_repo.get("full_name") != REPOSITORY
            or base_repo.get("full_name") != REPOSITORY
            or head.get("sha") != EXPECTED_HEAD
            or base.get("ref") != "main"
            or pr.get("changed_files") != len(EXPECTED_FILES)):
        raise ReceiptBlocked("SOURCE_PR_HEAD_OR_BASE_UNVERIFIED")


def _verify_pr_files(files: object) -> list[str]:
    if not isinstance(files, list) or any(not isinstance(row, dict) for row in files):
        raise ReceiptBlocked("SOURCE_PR_FILES_UNVERIFIED")
    names = [row.get("filename") for row in files]
    if (len(names) != len(EXPECTED_FILES)
            or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names)
            or set(names) != EXPECTED_FILES):
        raise ReceiptBlocked("SOURCE_PR_FILES_MISMATCH")
    return sorted(names)


def _verify_pr_commits(commits: object) -> None:
    if (not isinstance(commits, list) or len(commits) >= 100
            or any(not isinstance(row, dict) for row in commits)):
        raise ReceiptBlocked("SOURCE_PR_COMMITS_UNVERIFIED")
    if EXPECTED_HEAD not in {row.get("sha") for row in commits}:
        raise ReceiptBlocked("SOURCE_PR_COMMIT_MISMATCH")



def _verified_reservation(comment_id: int, github_token: str, api) -> dict:
    """Bind a read-only probe to one immutable, authenticated Cursor writer."""
    import hashlib
    import re

    if type(comment_id) is not int or comment_id <= 0:
        raise ReceiptBlocked("CURSOR_RESERVATION_ID_INVALID")
    comment = _require_object(
        api(f"{GITHUB_API}/repos/{REPOSITORY}/issues/comments/{comment_id}",
            token=github_token), "CURSOR_RESERVATION_UNVERIFIED",
    )
    body = comment.get("body")
    if (comment.get("id") != comment_id
            or comment.get("issue_url") != f"{GITHUB_API}/repos/{REPOSITORY}/issues/1138"
            or not isinstance(comment.get("user"), dict)
            or comment["user"].get("login") != "github-actions[bot]"
            or comment["user"].get("id") != 41898282
            or not isinstance(body, str)
            or not body.startswith("<!-- WOW_REMOTE_REPAIR_ATTEMPT:")):
        raise ReceiptBlocked("CURSOR_RESERVATION_UNTRUSTED")
    try:
        marker, payload = body.split("\n", 1)
        entry = json.loads(payload)
    except (ValueError, TypeError):
        raise ReceiptBlocked("CURSOR_RESERVATION_INVALID") from None
    if not isinstance(entry, dict):
        raise ReceiptBlocked("CURSOR_RESERVATION_INVALID")
    number = entry.get("pr_number")
    incident = entry.get("incident_id")
    agent_id = entry.get("cursor_agent_id")
    run_id = entry.get("cursor_run_id")
    source_sha = entry.get("pr_sha")
    main_sha = entry.get("main_sha")
    worker_id = entry.get("worker_id")
    status = entry.get("status")
    branch = entry.get("branch")
    exact_witness = False
    if status == "CURSOR_LAUNCH_RESERVED":
        if "branch" not in entry:
            witness = EXACT_POST_CUTOVER_BRANCH_WITNESS
            # No time-window fallback: all other branchless reservations HOLD.
            if (comment_id != witness["comment_id"]
                    or hashlib.sha256(body.encode("utf-8")).hexdigest() != witness["digest"]
                    or incident != witness["incident"]
                    or number != witness["pr_number"]
                    or source_sha != witness["source_sha"]
                    or main_sha != witness["main_sha"]
                    or worker_id != witness["worker_id"]
                    or agent_id != witness["agent_id"]
                    or entry.get("source_pr") != int(witness["pr_number"])):
                raise ReceiptBlocked("CURSOR_RESERVATION_IDENTITY_INVALID")
            branch = witness["branch"]
            exact_witness = True
        if (run_id is not None or not isinstance(branch, str)
                or not branch.strip() or branch in PROTECTED_BRANCHES):
            raise ReceiptBlocked("CURSOR_RESERVATION_IDENTITY_INVALID")
        is_failed_launch = True
    elif status in (None, "CURSOR_AGENT_ENQUEUED"):
        if (not isinstance(run_id, str)
                or re.fullmatch(r"run-[0-9a-f-]{36}", run_id) is None):
            raise ReceiptBlocked("CURSOR_RESERVATION_IDENTITY_INVALID")
        is_failed_launch = False
    else:
        raise ReceiptBlocked("CURSOR_RESERVATION_IDENTITY_INVALID")
    if (marker != f"<!-- WOW_REMOTE_REPAIR_ATTEMPT:{incident} -->"
            or entry.get("provider") != "cursor"
            or entry.get("hold") is not True
            or entry.get("can_execute") is not False
            or entry.get("probability_authority") is not False
            or entry.get("terminal_authority") != "V17_TERMINAL_REDUCER"
            or not isinstance(incident, str) or not re.fullmatch(r"[1-9][0-9]*", incident)
            or not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]*", number)
            or entry.get("lease_group") != f"PR_{number}"
            or not isinstance(agent_id, str)
            or not re.fullmatch(r"bc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", agent_id)
            or not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
            or not isinstance(main_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", main_sha)
            or not isinstance(worker_id, str)
            or (is_failed_launch
                and agent_id != deterministic_agent_id(REPOSITORY, int(number), source_sha))):
        raise ReceiptBlocked("CURSOR_RESERVATION_IDENTITY_INVALID")
    match = re.fullmatch(r"github-actions:([1-9][0-9]*):([1-9][0-9]*)", worker_id)
    if not match:
        raise ReceiptBlocked("CURSOR_RESERVATION_WORKER_ID_INVALID")
    origin = _require_object(
        api(f"{GITHUB_API}/repos/{REPOSITORY}/actions/runs/{match.group(1)}",
            token=github_token), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED",
    )
    expected_conclusion = "failure" if is_failed_launch else "success"
    if (origin.get("id") != int(match.group(1))
            or origin.get("run_attempt") != int(match.group(2))
            or origin.get("path") != ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
            or origin.get("event") != "workflow_dispatch"
            or origin.get("head_sha") != main_sha
            or origin.get("head_branch") != "main"
            or origin.get("status") != "completed"
            or origin.get("conclusion") != expected_conclusion
            or not isinstance(origin.get("repository"), dict)
            or origin["repository"].get("full_name") != REPOSITORY
            or not isinstance(origin.get("head_repository"), dict)
            or origin["head_repository"].get("full_name") != REPOSITORY):
        raise ReceiptBlocked("CURSOR_RESERVATION_WORKFLOW_UNVERIFIED")
    return {
        "reservation_comment_id": comment_id,
        "exact_branchless_witness": exact_witness,
        "reservation_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "reservation_status": status or "CURSOR_AGENT_ENQUEUED",
        "reservation_branch": branch if isinstance(branch, str) else None,
        "incident_id": incident, "pr_number": int(number),
        "original_head_sha": source_sha, "agent_id": agent_id, "run_id": run_id,
        "worker_id": worker_id, "source_workflow_run_id": origin["id"],
        "source_workflow_run_attempt": origin["run_attempt"],
    }


def _verify_dynamic_pr(pr: dict, target: dict, github_token: str, api) -> list[str]:
    """Require exact identity and an ancestors-only head from the reserved SHA."""
    import re

    head, base = pr.get("head"), pr.get("base")
    if (pr.get("number") != target["pr_number"] or pr.get("state") != "open"
            or not isinstance(head, dict) or not isinstance(base, dict)
            or not isinstance(head.get("repo"), dict)
            or not isinstance(base.get("repo"), dict)
            or head["repo"].get("full_name") != REPOSITORY
            or base["repo"].get("full_name") != REPOSITORY
            or base.get("ref") != "main"
            or not isinstance(head.get("sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", head["sha"])):
        raise ReceiptBlocked("CURSOR_SOURCE_PR_IDENTITY_INVALID")
    failed_launch = target.get("reservation_status") == "CURSOR_LAUNCH_RESERVED"
    live_ref = head.get("ref")
    if failed_launch:
        if (not isinstance(live_ref, str) or not live_ref.strip()
                or live_ref in PROTECTED_BRANCHES or live_ref == base.get("ref")):
            raise ReceiptBlocked("CURSOR_SOURCE_PR_IDENTITY_INVALID")
        if target.get("reservation_branch") != live_ref:
            raise ReceiptBlocked("CURSOR_RESERVATION_BRANCH_MISMATCH")
    original, latest = target["original_head_sha"], head["sha"]
    # The single post-cutover attestation cannot follow a changed PR head.
    if target.get("exact_branchless_witness") and latest != original:
        raise ReceiptBlocked("CURSOR_EXACT_WITNESS_PR_MOVED")
    if original != latest:
        comparison = _require_object(
            api(f"{GITHUB_API}/repos/{REPOSITORY}/compare/{original}...{latest}",
                token=github_token), "CURSOR_PR_LINEAGE_UNVERIFIED",
        )
        if (comparison.get("status") != "ahead"
                or comparison.get("behind_by") != 0
                or not isinstance(comparison.get("merge_base_commit"), dict)
                or comparison["merge_base_commit"].get("sha") != original):
            raise ReceiptBlocked("CURSOR_PR_HEAD_NOT_DESCENDANT")
    url = f"{GITHUB_API}/repos/{REPOSITORY}/pulls/{target['pr_number']}"
    files = api(f"{url}/files?per_page=100&page=1", token=github_token)
    if (not isinstance(files, list) or not files or len(files) >= 100
            or any(not isinstance(row, dict) or not isinstance(row.get("filename"), str)
                   for row in files)
            or pr.get("changed_files") != len(files)):
        raise ReceiptBlocked("CURSOR_PR_FILES_UNVERIFIED")
    names = [row["filename"] for row in files]
    if len(set(names)) != len(names):
        raise ReceiptBlocked("CURSOR_PR_FILES_AMBIGUOUS")
    # Guard against PR movement between comparison and completion.
    again = _require_object(api(url, token=github_token),
                            "CURSOR_SOURCE_PR_RECHECK_FAILED")
    if (again.get("number") != target["pr_number"]
            or again.get("state") != "open"
            or not isinstance(again.get("head"), dict)
            or again["head"].get("sha") != latest
            or again["head"].get("ref") != live_ref
            or not isinstance(again["head"].get("repo"), dict)
            or again["head"]["repo"].get("full_name") != REPOSITORY
            or not isinstance(again.get("base"), dict)
            or again["base"].get("ref") != "main"):
        raise ReceiptBlocked("CURSOR_PR_MOVED_DURING_PROBE")
    return sorted(names)


def verify_negative_inventories(
    cursor_token: str, *, agent_id: str, pr_number: int, api,
) -> dict:
    """Prove via complete archived inventories that no Cursor agent exists or is active."""
    pr_url = f"https://github.com/{REPOSITORY}/pull/{pr_number}"

    def _scan_inventory(base_params: dict[str, object], *, unverified_code: str, active_code: str, require_empty: bool) -> tuple[int, int]:
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        next_cursor: str | None = None
        pages = 0
        for _ in range(MAX_NEGATIVE_INVENTORY_PAGES):
            params = dict(base_params)
            if next_cursor is not None:
                params["cursor"] = next_cursor
            query = parse.urlencode(params)
            page = api(f"{CURSOR_API}/agents?{query}", token=cursor_token, basic=True)
            if not isinstance(page, dict):
                raise ReceiptBlocked(unverified_code)
            items = page.get("items")
            if not isinstance(items, list) or len(items) > INVENTORY_PAGE_SIZE:
                raise ReceiptBlocked(unverified_code)
            pages += 1
            if require_empty and items:
                raise ReceiptBlocked("CURSOR_PR_ASSOCIATED_AGENT_EXISTS")
            for item in items:
                if not isinstance(item, dict):
                    raise ReceiptBlocked(unverified_code)
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id.strip():
                    raise ReceiptBlocked(unverified_code)
                if item_id in seen_ids:
                    raise ReceiptBlocked(unverified_code)
                seen_ids.add(item_id)
                if item_id == agent_id:
                    raise ReceiptBlocked("CURSOR_AGENT_INVENTORY_CONTRADICTION")
                if item.get("status") == "ACTIVE":
                    raise ReceiptBlocked(active_code)
            raw_next = page.get("nextCursor")
            if raw_next in (None, ""):
                return pages, len(seen_ids)
            if (not isinstance(raw_next, str)
                    or not NEXT_CURSOR_RE.fullmatch(raw_next)
                    or (not items and not require_empty)
                    or raw_next in seen_cursors):
                raise ReceiptBlocked(unverified_code)
            seen_cursors.add(raw_next)
            next_cursor = raw_next
        # Fail closed on pagination exhaustion: raising ReceiptBlocked aborts
        # negative-launch verification and retains the shared-writer hold.
        raise ReceiptBlocked(unverified_code)

    global_pages, global_count = _scan_inventory(
        {"limit": INVENTORY_PAGE_SIZE, "includeArchived": "true"},
        unverified_code="CURSOR_GLOBAL_AGENT_INVENTORY_UNVERIFIED",
        active_code="CURSOR_GLOBAL_ACTIVE_AGENT_EXISTS",
        require_empty=False,
    )
    pr_pages, pr_count = _scan_inventory(
        {"prUrl": pr_url, "limit": INVENTORY_PAGE_SIZE, "includeArchived": "true"},
        unverified_code="CURSOR_AGENT_INVENTORY_UNVERIFIED",
        active_code="CURSOR_ACTIVE_PR_AGENT_EXISTS",
        require_empty=True,
    )
    return {
        "global_pages": global_pages,
        "global_agents_checked": global_count,
        "pr_pages": pr_pages,
        "pr_agents_checked": pr_count,
    }


def collect_receipt(
    cursor_token: str, github_token: str, api=api_json, *,
    reservation_comment_id: int | None = None,
) -> dict:
    if not cursor_token:
        raise ReceiptBlocked("CURSOR_API_KEY_MISSING")
    if not github_token:
        raise ReceiptBlocked("GITHUB_READ_TOKEN_MISSING")

    target = (_verified_reservation(reservation_comment_id, github_token, api)
              if reservation_comment_id is not None else None)
    agent_id = target["agent_id"] if target else AGENT_ID
    run_id = target["run_id"] if target else RUN_ID
    pr_number = target["pr_number"] if target else PR_NUMBER

    agent_url = f"{CURSOR_API}/agents/{parse.quote(agent_id, safe='')}"
    if target is not None and run_id is None:
        try:
            existing = api(agent_url, token=cursor_token, basic=True)
        except ReceiptBlocked as exc:
            if exc.code != "STATUS_API_HTTP_404":
                raise
        else:
            if isinstance(existing, dict) and existing.get("status") == "ACTIVE":
                raise ReceiptBlocked("CURSOR_AGENT_STILL_ACTIVE")
            raise ReceiptBlocked("CURSOR_AGENT_EXISTS_REQUIRES_RUN_RECONCILIATION")

        negative_proof = verify_negative_inventories(
            cursor_token, agent_id=agent_id, pr_number=pr_number, api=api,
        )
        pr_url = f"{GITHUB_API}/repos/{REPOSITORY}/pulls/{pr_number}"
        pr = _require_object(
            api(pr_url, token=github_token),
            "SOURCE_PR_IDENTITY_UNVERIFIED",
        )
        changed_files = _verify_dynamic_pr(pr, target, github_token, api)
        return {
            "status": "CURSOR_TERMINAL_STATUS_VERIFIED",
            "agent_id": agent_id,
            "agent_status": "NEVER_CREATED",
            "run_id": None,
            "latest_run_id": None,
            "terminal_status": "CURSOR_CREATE_FAILED_NO_AGENT_PROVEN",
            "run_result_field_present": False,
            "negative_launch_proof": negative_proof,
            "repository": REPOSITORY,
            "pr_number": pr_number,
            "base_ref": pr["base"]["ref"],
            "head_sha": pr["head"]["sha"],
            "changed_files": changed_files,
            "release_authorized": False,
            "lease_released": False,
            "can_execute": False,
            "production_verified": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "reservation": target,
        }

    run_url = (
        f"{CURSOR_API}/agents/{parse.quote(agent_id, safe='')}/runs/"
        f"{parse.quote(run_id, safe='')}"
    )
    agent = _require_object(
        api(agent_url, token=cursor_token, basic=True),
        "CURSOR_AGENT_RESPONSE_UNVERIFIED",
    )
    if agent.get("id") != agent_id:
        raise ReceiptBlocked("CURSOR_AGENT_ID_MISMATCH")
    if agent.get("latestRunId") != run_id:
        raise ReceiptBlocked("CURSOR_LATEST_RUN_MISMATCH")
    if agent.get("status") == "ACTIVE":
        raise ReceiptBlocked("CURSOR_AGENT_STILL_ACTIVE")

    run = _require_object(
        api(run_url, token=cursor_token, basic=True),
        "CURSOR_RUN_RESPONSE_UNVERIFIED",
    )
    returned_ids = [run[key] for key in ("id", "runId") if key in run]
    if not returned_ids or any(value != run_id for value in returned_ids):
        raise ReceiptBlocked("CURSOR_RUN_ID_MISMATCH")
    if run.get("agentId") is not None and run.get("agentId") != agent_id:
        raise ReceiptBlocked("CURSOR_RUN_AGENT_MISMATCH")
    terminal_status = run.get("status")
    if terminal_status not in TERMINAL_RUN_STATES:
        raise ReceiptBlocked("CURSOR_TERMINAL_STATUS_UNVERIFIED")

    pr_url = f"{GITHUB_API}/repos/{REPOSITORY}/pulls/{pr_number}"
    pr = _require_object(
        api(pr_url, token=github_token),
        "SOURCE_PR_IDENTITY_UNVERIFIED",
    )
    if target:
        changed_files = _verify_dynamic_pr(pr, target, github_token, api)
    else:
        _verify_pr(pr)

    if not target:
        files = api(
            f"{pr_url}/files?per_page=100&page=1",
            token=github_token,
        )
        changed_files = _verify_pr_files(files)
        commits = api(
            f"{pr_url}/commits?per_page=100&page=1",
            token=github_token,
        )
        _verify_pr_commits(commits)

    return {
        "status": "CURSOR_TERMINAL_STATUS_VERIFIED",
        "agent_id": agent_id,
        "agent_status": agent.get("status"),
        "run_id": run_id,
        "latest_run_id": agent["latestRunId"],
        "terminal_status": terminal_status,
        "run_result_field_present": "result" in run,
        "repository": REPOSITORY,
        "pr_number": pr_number,
        "pr_state": pr["state"],
        "base_ref": pr["base"]["ref"],
        "head_sha": pr["head"]["sha"],
        "changed_files": changed_files,
        "lease_released": False,
        "can_execute": False,
        "production_verified": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        **({"reservation": target} if target else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--reservation-comment-id", type=int)
    args = parser.parse_args()
    try:
        receipt = collect_receipt(
            os.environ.get("CURSOR_API_KEY", ""),
            os.environ.get("GH_TOKEN", ""),
            reservation_comment_id=args.reservation_comment_id,
        )
    except ReceiptBlocked as exc:
        receipt = {
            "status": "CURSOR_TERMINAL_STATUS_UNVERIFIED",
            "code": exc.code,
            "agent_id": None if args.reservation_comment_id else AGENT_ID,
            "run_id": None if args.reservation_comment_id else RUN_ID,
            "repository": REPOSITORY,
            "pr_number": None if args.reservation_comment_id else PR_NUMBER,
            "reservation_comment_id": args.reservation_comment_id,
            "lease_released": False,
            "can_execute": False,
            "production_verified": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }
        exit_code = 1
    else:
        exit_code = 0
    Path(args.receipt).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
