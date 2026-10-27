"""Fenced reconciliation of one verified, terminated Cursor writer.

The existing terminal collector remains read-only. This module re-collects live
provider proof, checks the canonical #1138 reservation and original PR ancestry,
then releases ONLY the matching ended remote writer. It must be invoked from the
existing implementation-single-writer GitHub Actions concurrency group.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

try:
    from . import cursor_terminal_status_receipt as terminal
    from . import engineering_remote_fallback as remote
except ImportError:
    import cursor_terminal_status_receipt as terminal
    import engineering_remote_fallback as remote


class ReconciliationBlocked(Exception):
    pass


def _fail(code: str) -> None:
    raise ReconciliationBlocked(code)


def _bound(row: dict, proof: dict) -> bool:
    return (
        row.get("provider") == "cursor"
        and row.get("incident_id") == "2054"
        and row.get("lease_group") == "PR_2090"
        and row.get("pr_number") == str(proof["pr_number"])
        and row.get("cursor_agent_id") == proof["agent_id"]
        and row.get("cursor_run_id") == proof["run_id"]
        and row.get("source_pr") == proof["pr_number"]
    )


def release_verified_terminal(api, proof: dict) -> dict:
    """Re-read identities under a shared writer mutex; never trust a stale artifact."""
    if not isinstance(proof, dict):
        _fail("TERMINAL_PROOF_INVALID")
    if (
        proof.get("status") != "CURSOR_TERMINAL_STATUS_VERIFIED"
        or proof.get("terminal_status") != "FINISHED"
        or proof.get("agent_id") != terminal.AGENT_ID
        or proof.get("run_id") != terminal.RUN_ID
        or proof.get("latest_run_id") != terminal.RUN_ID
        or proof.get("repository") != remote.REPOSITORY
        or proof.get("pr_number") != terminal.PR_NUMBER
        or proof.get("head_sha") != terminal.EXPECTED_HEAD
        or proof.get("can_execute") is not False
        or proof.get("lease_released") is not False
        or proof.get("terminal_authority") != "V17_TERMINAL_REDUCER"
    ):
        _fail("TERMINAL_PROOF_IDENTITY_MISMATCH")

    comments = api.pages(remote.LEDGER)
    entries = remote.reservations(comments)
    bound = [(comment_id, row) for comment_id, row in entries if _bound(row, proof)]
    if len(bound) != 1:
        _fail("EXACT_CURSOR_RESERVATION_MISSING_OR_DUPLICATE")
    comment_id, original = bound[0]
    if any(row["hold"] for cid, row in entries if cid != comment_id):
        _fail("OTHER_REMOTE_WRITER_PRESENT")
    matching_comments = [c for c in comments if c.get("id") == comment_id]
    if len(matching_comments) != 1 or (
        matching_comments[0].get("user", {}).get("login") != "github-actions[bot]"
    ):
        _fail("RESERVATION_WRITER_IDENTITY_UNVERIFIED")

    # Reservation captures the starting PR SHA, not the final agent-produced
    # head SHA. Both must appear on the current, independently validated PR.
    commit_path = (
        f"/repos/{remote.REPOSITORY}/pulls/{terminal.PR_NUMBER}"
        "/commits?per_page=100&page=1"
    )
    commits = api.request(commit_path)
    if (not isinstance(commits, list) or not 1 <= len(commits) < 100
            or any(not isinstance(c, dict) for c in commits)):
        _fail("PR_ANCESTRY_UNVERIFIED")
    shas = [row.get("sha") for row in commits]
    if (len(set(shas)) != len(shas)
            or original.get("pr_sha") not in shas
            or proof["head_sha"] != shas[-1]):
        _fail("RESERVED_PR_ANCESTRY_MISMATCH")

    comment_path = f"/repos/{remote.REPOSITORY}/issues/comments/{comment_id}"
    # Fresh read immediately before any mutation, rejecting competing edits.
    current_comment = api.request(comment_path)
    if not isinstance(current_comment, dict) or (
        current_comment.get("body") != matching_comments[0].get("body")
    ):
        _fail("RESERVATION_CHANGED_DURING_RECONCILIATION")
    current_entries = remote.reservations([current_comment])
    if len(current_entries) != 1 or current_entries[0][0] != comment_id:
        _fail("RESERVATION_READBACK_INVALID")
    current = current_entries[0][1]
    if current != original or not _bound(current, proof):
        _fail("RESERVATION_CHANGED_DURING_RECONCILIATION")

    if not current["hold"]:
        if (current.get("status") != "CURSOR_TERMINAL_VERIFIED_LEASE_RELEASED"
                or current.get("cursor_run_status") != "FINISHED"):
            _fail("RELEASE_STATE_CONTRADICTORY")
        return _receipt("ALREADY_RELEASED", proof, comment_id)

    updated = {
        **current,
        "hold": False,
        "status": "CURSOR_TERMINAL_VERIFIED_LEASE_RELEASED",
        "cursor_run_status": "FINISHED",
        "verified_terminal_run_id": proof["run_id"],
        "verified_terminal_head_sha": proof["head_sha"],
        "next_action": (
            "Writer terminated; independently review source PR before merge or deploy. "
            "Provider admission must still pass its separate current safeguards."
        ),
    }
    saved = api.request(comment_path, {"body": remote._body(updated)}, "PATCH")
    if not isinstance(saved, dict) or saved.get("id") != comment_id:
        _fail("RELEASE_WRITE_ACK_INVALID")
    readback = api.request(comment_path)
    if not isinstance(readback, dict):
        _fail("RELEASE_READBACK_MISSING")
    parsed = remote.reservations([readback])
    if len(parsed) != 1 or parsed[0] != (comment_id, updated):
        _fail("RELEASE_READBACK_MISMATCH")
    return _receipt("RELEASED_AND_READBACK_VERIFIED", proof, comment_id)


def _receipt(status: str, proof: dict, comment_id: int) -> dict:
    return {
        "status": status,
        "ledger_comment_id": comment_id,
        "incident_id": "2054",
        "pr_number": terminal.PR_NUMBER,
        "agent_id": proof["agent_id"],
        "run_id": proof["run_id"],
        "head_sha": proof["head_sha"],
        "lease_released": True,
        "production_verified": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        cursor_key, github_key = os.environ.get("CURSOR_API_KEY", ""), os.environ.get("GH_TOKEN", "")
        # Failure to establish fresh proof must not write to the ledger.
        proof = terminal.collect_receipt(cursor_key, github_key)
        result = release_verified_terminal(remote.GitHub(github_key), proof)
        exit_code = 0
    except (terminal.ReceiptBlocked, ReconciliationBlocked, remote.ProviderUnavailable) as exc:
        result = {
            "status": "CURSOR_LEASE_RECONCILIATION_BLOCKED",
            "code": exc.code if isinstance(exc, terminal.ReceiptBlocked) else str(exc),
            "lease_released": False,
            "can_execute": False,
            "production_verified": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }
        exit_code = 1
    Path(args.receipt).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
