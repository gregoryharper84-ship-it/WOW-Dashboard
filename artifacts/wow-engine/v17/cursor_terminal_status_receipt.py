"""Read-only terminal status receipt for the existing WOW Cursor handoff."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
from urllib import error, parse, request


REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
PR_NUMBER = 2090
EXPECTED_HEAD = "85fdd54921fa92220b433d4fa71cd3399bca8385"
AGENT_ID = "bc-c8a71883-d1ab-593a-8e92-2f7779436cb4"
RUN_ID = "run-7b568d98-de5b-4cce-b2a4-e7743fd43787"
CURSOR_API = "https://api.cursor.com/v1"
GITHUB_API = "https://api.github.com"
TERMINAL_RUN_STATES = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
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


def collect_receipt(cursor_token: str, github_token: str, api=api_json) -> dict:
    if not cursor_token:
        raise ReceiptBlocked("CURSOR_API_KEY_MISSING")
    if not github_token:
        raise ReceiptBlocked("GITHUB_READ_TOKEN_MISSING")

    agent_url = f"{CURSOR_API}/agents/{parse.quote(AGENT_ID, safe='')}"
    run_url = (
        f"{CURSOR_API}/agents/{parse.quote(AGENT_ID, safe='')}/runs/"
        f"{parse.quote(RUN_ID, safe='')}"
    )
    agent = _require_object(
        api(agent_url, token=cursor_token, basic=True),
        "CURSOR_AGENT_RESPONSE_UNVERIFIED",
    )
    if agent.get("id") != AGENT_ID:
        raise ReceiptBlocked("CURSOR_AGENT_ID_MISMATCH")
    if agent.get("latestRunId") != RUN_ID:
        raise ReceiptBlocked("CURSOR_LATEST_RUN_MISMATCH")
    if agent.get("status") == "ACTIVE":
        raise ReceiptBlocked("CURSOR_AGENT_STILL_ACTIVE")

    run = _require_object(
        api(run_url, token=cursor_token, basic=True),
        "CURSOR_RUN_RESPONSE_UNVERIFIED",
    )
    returned_ids = [run[key] for key in ("id", "runId") if key in run]
    if not returned_ids or any(value != RUN_ID for value in returned_ids):
        raise ReceiptBlocked("CURSOR_RUN_ID_MISMATCH")
    if run.get("agentId") is not None and run.get("agentId") != AGENT_ID:
        raise ReceiptBlocked("CURSOR_RUN_AGENT_MISMATCH")
    terminal_status = run.get("status")
    if terminal_status not in TERMINAL_RUN_STATES:
        raise ReceiptBlocked("CURSOR_TERMINAL_STATUS_UNVERIFIED")

    pr_url = f"{GITHUB_API}/repos/{REPOSITORY}/pulls/{PR_NUMBER}"
    pr = _require_object(
        api(pr_url, token=github_token),
        "SOURCE_PR_IDENTITY_UNVERIFIED",
    )
    _verify_pr(pr)

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
        "agent_id": AGENT_ID,
        "agent_status": agent.get("status"),
        "run_id": RUN_ID,
        "latest_run_id": agent["latestRunId"],
        "terminal_status": terminal_status,
        "run_result_field_present": "result" in run,
        "repository": REPOSITORY,
        "pr_number": PR_NUMBER,
        "pr_state": pr["state"],
        "base_ref": pr["base"]["ref"],
        "head_sha": pr["head"]["sha"],
        "changed_files": changed_files,
        "lease_released": False,
        "can_execute": False,
        "production_verified": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        receipt = collect_receipt(
            os.environ.get("CURSOR_API_KEY", ""),
            os.environ.get("GH_TOKEN", ""),
        )
    except ReceiptBlocked as exc:
        receipt = {
            "status": "CURSOR_TERMINAL_STATUS_UNVERIFIED",
            "code": exc.code,
            "agent_id": AGENT_ID,
            "run_id": RUN_ID,
            "repository": REPOSITORY,
            "pr_number": PR_NUMBER,
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
