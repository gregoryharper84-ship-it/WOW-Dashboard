"""Append-only terminal release of a legacy remote writer.

The old issue comment stays immutable. A dedicated, serialized GitHub Actions
workflow may append one matching release receipt after a fresh authenticated
provider lookup. The guard trusts the receipt ONLY after that workflow's
protected-main run has completed successfully. GitHub comments are audit data,
not a compare-and-swap writer-lease database.
"""
from __future__ import annotations

import hashlib
import json
import os

REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
LEDGER = f"/repos/{REPOSITORY}/issues/1138/comments"
RELEASE_MARKER = "<!-- WOW_REMOTE_WRITER_RELEASE:"
WORKFLOW = ".github/workflows/wow-v17-remote-writer-finalize.yml"
BOT_LOGIN = "github-actions[bot]"
EXPECTED_COMMENT_ID = 6412045537
EXPECTED_INCIDENT_ID = "2054"
EXPECTED_WORKER_ID = "github-actions:40295540537:1"


class ReleaseBlocked(Exception):
    pass


def _original_digest(comment: dict) -> str:
    return hashlib.sha256(comment["body"].encode("utf-8")).hexdigest()


def _release_records(comments: list[dict]) -> list[tuple[dict, dict]]:
    result = []
    for comment in comments:
        body = comment.get("body", "")
        if not isinstance(body, str):
            raise ReleaseBlocked("REMOTE_RELEASE_RECEIPT_INVALID")
        if RELEASE_MARKER not in body:
            continue
        try:
            first, payload = body.split("\n", 1)
            document = json.loads(payload)
            if (not isinstance(document, dict)
                    or first != f"{RELEASE_MARKER}{document['incident_id']} -->"):
                raise ValueError("bad release marker")
        except (ValueError, KeyError):
            raise ReleaseBlocked("REMOTE_RELEASE_RECEIPT_INVALID") from None
        result.append((comment, document))
    return result


def verified_released_ids(
    comments: list[dict], held: list[tuple[int, dict]], get_run=None,
) -> set[int]:
    """Fail closed on stale, spoofed, malformed, conflicting or uncompleted releases."""
    releases = _release_records(comments)
    if not releases:
        return set()
    if get_run is None:
        raise ReleaseBlocked("REMOTE_RELEASE_WORKFLOW_NOT_VERIFIED")
    ids = set()
    for comment, doc in releases:
        cid = doc.get("original_comment_id")
        matches = [
            source for source in comments
            if source.get("id") == cid and isinstance(source.get("body"), str)
        ]
        claims = [row for source_id, row in held if source_id == cid]
        if (type(cid) is not int or len(matches) != 1 or len(claims) != 1
                or cid in ids or comment.get("id", 0) <= cid
                or comment.get("user", {}).get("login") != BOT_LOGIN
                or doc.get("incident_id") != claims[0].get("incident_id")
                or doc.get("provider") != "cursor"
                or claims[0].get("provider") != "cursor"
                or doc.get("original_digest") != _original_digest(matches[0])
                or doc.get("agent_id") != claims[0].get("cursor_agent_id")
                or doc.get("cursor_run_id") != claims[0].get("cursor_run_id")
                or doc.get("worker_id") != claims[0].get("worker_id")
                or str(doc.get("pr_number")) != str(claims[0].get("pr_number"))
                or doc.get("terminal_status") != "FINISHED"
                or doc.get("agent_status") != "IDLE"
                or doc.get("can_execute") is not False
                or doc.get("terminal_authority") != "V17_TERMINAL_REDUCER"
                or type(doc.get("workflow_run_id")) is not int
                or doc["workflow_run_id"] <= 0
                or type(doc.get("run_attempt")) is not int
                or doc["run_attempt"] <= 0
                or not isinstance(doc.get("source_sha"), str)
                or len(doc["source_sha"]) != 40
                or any(c not in "0123456789abcdef" for c in doc["source_sha"])):
            raise ReleaseBlocked("REMOTE_RELEASE_RECEIPT_IDENTITY_UNVERIFIED")
        try:
            run = get_run(doc["workflow_run_id"])
        except Exception:
            raise ReleaseBlocked("REMOTE_RELEASE_WORKFLOW_NOT_VERIFIED") from None
        if (not isinstance(run, dict)
                or run.get("path") != WORKFLOW
                or run.get("event") not in {"workflow_dispatch", "push"}
                or run.get("status") != "completed"
                or run.get("conclusion") != "success"
                or run.get("head_branch") != "main"
                or run.get("head_sha") != doc["source_sha"]
                or run.get("run_attempt") != doc["run_attempt"]
                or run.get("repository", {}).get("full_name") != REPOSITORY):
            raise ReleaseBlocked("REMOTE_RELEASE_WORKFLOW_NOT_VERIFIED")
        ids.add(cid)
    return ids


def append_terminal_release(api, *, cursor_token: str, run_id: int,
                            run_attempt: int, source_sha: str,
                            reservation_comment_id: int | None = None) -> dict:
    """Called only under the EXISTING implementation-single-writer mutex."""
    try:
        from .cursor_terminal_status_receipt import collect_receipt
        from .engineering_remote_fallback import (
            ProviderUnavailable, assert_no_local_writer, reservations,
        )
    except ImportError:  # Legacy standalone CLI entrypoint
        from cursor_terminal_status_receipt import collect_receipt
        from engineering_remote_fallback import (
            ProviderUnavailable, assert_no_local_writer, reservations,
        )

    if (not cursor_token or type(run_id) is not int or run_id <= 0
            or type(run_attempt) is not int or run_attempt <= 0
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or api.request(f"/repos/{REPOSITORY}/commits/main").get("sha") != source_sha):
        raise ReleaseBlocked("REMOTE_RELEASE_PROTECTED_MAIN_UNVERIFIED")
    if reservation_comment_id is not None and (
            type(reservation_comment_id) is not int or reservation_comment_id <= 0):
        raise ReleaseBlocked("REMOTE_RELEASE_TARGET_INVALID")
    comments = api.pages(LEDGER)
    all_reservations = reservations(comments)
    # The original release remains an immutable valid receipt. Do not reject it
    # when a distinct, later Cursor reservation is the only effective writer.
    verified = verified_released_ids(
        comments, all_reservations,
        get_run=lambda rid: api.request(f"/repos/{REPOSITORY}/actions/runs/{rid}"),
    )
    held = [(cid, row) for cid, row in all_reservations
            if row["hold"] and cid not in verified]
    expected_id = (reservation_comment_id if reservation_comment_id is not None
                   else EXPECTED_COMMENT_ID)
    if (len(held) != 1 or held[0][0] != expected_id
            or held[0][1].get("provider") != "cursor"):
        raise ReleaseBlocked("REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED")
    old = held[0][1]
    if reservation_comment_id is None and (
            old.get("incident_id") != EXPECTED_INCIDENT_ID
            or old.get("worker_id") != EXPECTED_WORKER_ID):
        raise ReleaseBlocked("REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED")
    originals = [c for c in comments if c.get("id") == expected_id]
    if (len(originals) != 1
            or not isinstance(originals[0].get("user"), dict)
            or originals[0]["user"].get("login") != BOT_LOGIN
            or not isinstance(originals[0].get("body"), str)
            or old.get("status") != "CURSOR_AGENT_ENQUEUED"):
        raise ReleaseBlocked("REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED")
    try:
        assert_no_local_writer(comments)
    except ProviderUnavailable:
        raise ReleaseBlocked("REMOTE_RELEASE_CONCURRENT_WRITER") from None

    # GET the live provider run, agent and source PR again; the saved artifact
    # alone cannot authorize release and no Cursor write request is made.
    proof = collect_receipt(
        cursor_token, api.token,
        **({"reservation_comment_id": expected_id}
           if reservation_comment_id is not None else {}),
    )
    if reservation_comment_id is not None:
        identity = proof.get("reservation")
        if (not isinstance(identity, dict)
                or identity.get("reservation_comment_id") != expected_id
                or identity.get("reservation_sha256") != _original_digest(originals[0])
                or identity.get("incident_id") != old["incident_id"]
                or identity.get("worker_id") != old["worker_id"]
                or identity.get("original_head_sha") != old["pr_sha"]):
            raise ReleaseBlocked("REMOTE_RELEASE_PROBE_IDENTITY_MISMATCH")
    if (proof.get("status") != "CURSOR_TERMINAL_STATUS_VERIFIED"
            or proof.get("terminal_status") != "FINISHED"
            or proof.get("agent_status") != "IDLE"
            or proof.get("agent_id") != old.get("cursor_agent_id")
            or proof.get("run_id") != old.get("cursor_run_id")
            or str(proof.get("pr_number")) != str(old.get("pr_number"))
            or proof.get("can_execute") is not False):
        raise ReleaseBlocked("REMOTE_RELEASE_TERMINAL_PROOF_MISMATCH")

    # Recheck the entire active ledger immediately before writing. The
    # existing Actions mutex serializes cooperating implementation writers.
    current = api.pages(LEDGER)
    current_reservations = reservations(current)
    current_verified = verified_released_ids(
        current, current_reservations,
        get_run=lambda rid: api.request(f"/repos/{REPOSITORY}/actions/runs/{rid}"),
    )
    now_held = [(cid, row) for cid, row in current_reservations
                if row["hold"] and cid not in current_verified]
    original = [c for c in current if c.get("id") == expected_id]
    if (now_held != held or current_verified != verified
            or len(original) != 1
            or _original_digest(original[0]) != _original_digest(originals[0])):
        raise ReleaseBlocked("REMOTE_RELEASE_FENCE_CHANGED")
    if api.request(f"/repos/{REPOSITORY}/commits/main").get("sha") != source_sha:
        raise ReleaseBlocked("REMOTE_RELEASE_SOURCE_MOVED")

    doc = {
        "status": "REMOTE_RELEASE_PENDING_WORKFLOW_SUCCESS",
        "incident_id": old["incident_id"],
        "original_comment_id": expected_id,
        "original_digest": _original_digest(original[0]),
        "provider": "cursor",
        "agent_id": proof["agent_id"],
        "cursor_run_id": proof["run_id"],
        "worker_id": old["worker_id"],
        "pr_number": proof["pr_number"],
        "terminal_status": proof["terminal_status"],
        "agent_status": proof["agent_status"],
        "workflow_run_id": run_id,
        "run_attempt": run_attempt,
        "source_sha": source_sha,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    body = f"{RELEASE_MARKER}{old['incident_id']} -->\n" + json.dumps(doc, sort_keys=True)
    created = api.request(LEDGER, {"body": body}, "POST")
    cid = created.get("id") if isinstance(created, dict) else None
    readback = api.request(f"/repos/{REPOSITORY}/issues/comments/{cid}") if cid else {}
    if (type(cid) is not int or cid <= expected_id
            or readback.get("id") != cid or readback.get("body") != body
            or readback.get("user", {}).get("login") != BOT_LOGIN):
        raise ReleaseBlocked("REMOTE_RELEASE_READBACK_UNVERIFIED")
    # The guard will clear ONLY after this workflow returns success.
    return {"status": "REMOTE_RELEASE_PENDING_WORKFLOW_SUCCESS",
            "release_comment_id": cid, "original_comment_id": expected_id,
            "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER"}
