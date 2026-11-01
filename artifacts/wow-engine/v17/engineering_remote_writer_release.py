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
import re

try:
    from .cursor_cloud_handoff import agent_id as deterministic_agent_id
except ImportError:
    from cursor_cloud_handoff import agent_id as deterministic_agent_id

REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
LEDGER = f"/repos/{REPOSITORY}/issues/1138/comments"
RELEASE_MARKER = "<!-- WOW_REMOTE_WRITER_RELEASE:"
WORKFLOW = ".github/workflows/wow-v17-remote-writer-finalize.yml"
BOT_LOGIN = "github-actions[bot]"
EXPECTED_COMMENT_ID = 6412045537
EXPECTED_INCIDENT_ID = "2054"
EXPECTED_WORKER_ID = "github-actions:40295540537:1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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
        claim = claims[0] if len(claims) == 1 else {}
        valid_finished_run = (
            claim.get("status") in (None, "CURSOR_AGENT_ENQUEUED")
            and isinstance(claim.get("cursor_run_id"), str)
            and claim["cursor_run_id"].startswith("run-")
            and doc.get("cursor_run_id") == claim.get("cursor_run_id")
            and doc.get("terminal_status") == "FINISHED"
            and doc.get("agent_status") == "IDLE"
        )
        # Negative-launch release is bound jointly to the GitHub Actions bot
        # author of both the reservation and release comments, the SHA-256
        # digest of the original reservation body, the exact PR/main SHAs
        # from the authoritative reservation `claim`, worker_id, deterministic
        # agent_id derived from the reservation `claim`, and the verified
        # finalizer workflow run.
        expected_negative_agent_id = (
            deterministic_agent_id(
                REPOSITORY, int(claim["pr_number"]), claim["pr_sha"],
            )
            if (
                isinstance(claim.get("pr_sha"), str)
                and bool(SHA_RE.fullmatch(claim["pr_sha"]))
                and isinstance(claim.get("pr_number"), str)
                and claim["pr_number"].isdigit()
                and int(claim["pr_number"]) > 0
            )
            else None
        )
        valid_negative_launch = (
            claim.get("status") == "CURSOR_LAUNCH_RESERVED"
            and claim.get("cursor_run_id") is None
            and "cursor_run_status" not in claim
            and claim.get("can_execute") is False
            and claim.get("probability_authority") is False
            and claim.get("terminal_authority") == "V17_TERMINAL_REDUCER"
            and doc.get("cursor_run_id") is None
            and doc.get("terminal_status") == "CURSOR_CREATE_FAILED_NO_AGENT_PROVEN"
            and doc.get("agent_status") == "NEVER_CREATED"
            and len(matches) == 1
            and matches[0].get("user", {}).get("login") == BOT_LOGIN
            and expected_negative_agent_id is not None
            and claim.get("cursor_agent_id") == expected_negative_agent_id
            and doc.get("agent_id") == expected_negative_agent_id
            and isinstance(doc.get("pr_sha"), str)
            and bool(SHA_RE.fullmatch(doc["pr_sha"]))
            and isinstance(doc.get("main_sha"), str)
            and bool(SHA_RE.fullmatch(doc["main_sha"]))
            and doc["pr_sha"] == claim.get("pr_sha")
            and doc["main_sha"] == claim.get("main_sha")
        )
        if (type(cid) is not int or len(matches) != 1 or len(claims) != 1
                or cid in ids or comment.get("id", 0) <= cid
                or comment.get("user", {}).get("login") != BOT_LOGIN
                or doc.get("incident_id") != claims[0].get("incident_id")
                or doc.get("provider") != "cursor"
                or claims[0].get("provider") != "cursor"
                or doc.get("original_digest") != _original_digest(matches[0])
                or doc.get("agent_id") != claims[0].get("cursor_agent_id")
                or not (valid_finished_run or valid_negative_launch)
                or doc.get("worker_id") != claims[0].get("worker_id")
                or str(doc.get("pr_number")) != str(claims[0].get("pr_number"))
                or doc.get("can_execute") is not False
                or doc.get("terminal_authority") != "V17_TERMINAL_REDUCER"
                or type(doc.get("workflow_run_id")) is not int
                or doc["workflow_run_id"] <= 0
                or type(doc.get("run_attempt")) is not int
                or doc["run_attempt"] <= 0
                or not isinstance(doc.get("source_sha"), str)
                or not SHA_RE.fullmatch(doc["source_sha"])):
            raise ReleaseBlocked("REMOTE_RELEASE_RECEIPT_IDENTITY_UNVERIFIED")
        try:
            run = get_run(doc["workflow_run_id"])
        except Exception:
            raise ReleaseBlocked("REMOTE_RELEASE_WORKFLOW_NOT_VERIFIED") from None
        if (not isinstance(run, dict)
                or run.get("path") != WORKFLOW
                # A scheduled run may release only a *negative-launch*
                # reservation after the same authenticated live proofs.
                # Ordinary completed-agent releases keep manual provenance.
                or not (
                    run.get("event") in {"workflow_dispatch", "push"}
                    or (valid_negative_launch and run.get("event") == "schedule")
                )
                or run.get("status") != "completed"
                or run.get("conclusion") != "success"
                or run.get("head_branch") != "main"
                or run.get("head_sha") != doc["source_sha"]
                or run.get("run_attempt") != doc["run_attempt"]
                or run.get("repository", {}).get("full_name") != REPOSITORY):
            raise ReleaseBlocked("REMOTE_RELEASE_WORKFLOW_NOT_VERIFIED")
        ids.add(cid)
    return ids


def auto_failed_launch_target(api) -> int | None:
    """Select one provably unresolved failed-launch hold, never guess identity.

    Only the existing scheduled finalizer calls this read-only preflight.
    A concurrent/ambiguous/unverified release or any other held writer stays
    locked. The live Cursor proof happens in append_terminal_release, not here.
    """
    try:
        from .engineering_remote_fallback import reservations
    except ImportError:
        from engineering_remote_fallback import reservations

    comments = api.pages(LEDGER)
    attempts = reservations(comments)
    verified = verified_released_ids(
        comments, attempts,
        get_run=lambda rid: api.request(f"/repos/{REPOSITORY}/actions/runs/{rid}"),
    )
    held = [(cid, row) for cid, row in attempts
            if row["hold"] and cid not in verified]
    if not held:
        return None
    if len(held) != 1:
        raise ReleaseBlocked("REMOTE_AUTO_RELEASE_MULTIPLE_HELD_WRITERS")
    cid, claim = held[0]
    original = [c for c in comments if c.get("id") == cid]
    if (type(cid) is not int or cid <= 0
            or len(original) != 1
            or original[0].get("user", {}).get("login") != BOT_LOGIN
            or original[0].get("issue_url")
               != f"https://api.github.com/repos/{REPOSITORY}/issues/1138"
            or claim.get("provider") != "cursor"
            or claim.get("status") != "CURSOR_LAUNCH_RESERVED"
            or claim.get("cursor_run_id") is not None
            or "cursor_run_status" in claim
            or claim.get("can_execute") is not False
            or claim.get("probability_authority") is not False
            or claim.get("terminal_authority") != "V17_TERMINAL_REDUCER"):
        raise ReleaseBlocked("REMOTE_AUTO_RELEASE_NOT_FAILED_LAUNCH")
    return cid


def append_terminal_release(api, *, cursor_token: str, run_id: int,
                            run_attempt: int, source_sha: str,
                            reservation_comment_id: int | None = None) -> dict:
    """Called only under the EXISTING implementation-single-writer mutex."""
    try:
        from . import cursor_terminal_status_receipt as _cursor_receipt_mod
        from .engineering_remote_fallback import (
            ProviderUnavailable, assert_no_local_writer, reservations,
        )
    except ImportError:  # Legacy standalone CLI entrypoint
        import cursor_terminal_status_receipt as _cursor_receipt_mod
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
    expected_id = (
        reservation_comment_id if reservation_comment_id is not None
        else EXPECTED_COMMENT_ID
    )
    if (len(held) != 1 or held[0][0] != expected_id
            or held[0][1].get("provider") != "cursor"):
        raise ReleaseBlocked("REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED")
    old = held[0][1]
    if reservation_comment_id is None and (
            old.get("incident_id") != EXPECTED_INCIDENT_ID
            or old.get("worker_id") != EXPECTED_WORKER_ID):
        raise ReleaseBlocked("REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED")
    originals = [c for c in comments if c.get("id") == expected_id]
    is_failed_launch = old.get("status") == "CURSOR_LAUNCH_RESERVED"
    if (len(originals) != 1
            or not isinstance(originals[0].get("user"), dict)
            or originals[0]["user"].get("login") != BOT_LOGIN
            or not isinstance(originals[0].get("body"), str)
            or old.get("status") not in {"CURSOR_AGENT_ENQUEUED", "CURSOR_LAUNCH_RESERVED"}
            or (reservation_comment_id is None and is_failed_launch)
            or (is_failed_launch and (
                old.get("cursor_run_id") is not None
                or "cursor_run_status" in old
                or old.get("can_execute") is not False
                or old.get("probability_authority") is not False
                or old.get("terminal_authority") != "V17_TERMINAL_REDUCER"
            ))
            or (not is_failed_launch and (
                not isinstance(old.get("cursor_run_id"), str)
                or not old["cursor_run_id"].startswith("run-")
            ))):
        raise ReleaseBlocked("REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED")
    try:
        assert_no_local_writer(comments)
    except ProviderUnavailable:
        raise ReleaseBlocked("REMOTE_RELEASE_CONCURRENT_WRITER") from None

    # GET the live provider run, agent and source PR again; the saved artifact
    # alone cannot authorize release and no Cursor write request is made.
    proof = _cursor_receipt_mod.collect_receipt(
        cursor_token, api.token,
        **({"reservation_comment_id": reservation_comment_id}
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
    valid_proof_outcome = (
        (
            not is_failed_launch
            and proof.get("terminal_status") == "FINISHED"
            and proof.get("agent_status") == "IDLE"
            and proof.get("run_id") == old.get("cursor_run_id")
        )
        or (
            is_failed_launch
            and proof.get("terminal_status") == "CURSOR_CREATE_FAILED_NO_AGENT_PROVEN"
            and proof.get("agent_status") == "NEVER_CREATED"
            and proof.get("run_id") is None
            and proof.get("latest_run_id") is None
            and isinstance(proof.get("negative_launch_proof"), dict)
        )
    )
    if (proof.get("status") != "CURSOR_TERMINAL_STATUS_VERIFIED"
            or not valid_proof_outcome
            or proof.get("agent_id") != old.get("cursor_agent_id")
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

    if reservation_comment_id is not None:
        # This must be a FRESH witness after the initial terminal probe, ledger
        # and protected-main readbacks. The Actions mutex cannot fence a
        # concurrent external PR push or resumed Cursor run by itself.
        from urllib.parse import quote
        try:
            from .cursor_terminal_status_receipt import (
                CURSOR_API, ReceiptBlocked, verify_negative_inventories,
            )
        except ImportError:  # Legacy standalone CLI entrypoint
            from cursor_terminal_status_receipt import (
                CURSOR_API, ReceiptBlocked, verify_negative_inventories,
            )
        pr_number = proof.get("pr_number")
        expected_head = proof.get("head_sha")
        if (type(pr_number) is not int or pr_number <= 0
                or not isinstance(expected_head, str)
                or len(expected_head) != 40
                or any(ch not in "0123456789abcdef" for ch in expected_head)):
            raise ReleaseBlocked("REMOTE_RELEASE_SOURCE_PROOF_INVALID")
        fresh_pr = api.request(f"/repos/{REPOSITORY}/pulls/{pr_number}")
        if (not isinstance(fresh_pr, dict)
                or fresh_pr.get("number") != pr_number
                or fresh_pr.get("state") != "open"
                or not isinstance(fresh_pr.get("head"), dict)
                or not isinstance(fresh_pr.get("base"), dict)
                or fresh_pr["head"].get("sha") != expected_head
                or not isinstance(fresh_pr["head"].get("repo"), dict)
                or fresh_pr["head"]["repo"].get("full_name") != REPOSITORY
                or fresh_pr["base"].get("ref") != "main"
                or not isinstance(fresh_pr["base"].get("repo"), dict)
                or fresh_pr["base"]["repo"].get("full_name") != REPOSITORY):
            raise ReleaseBlocked("REMOTE_RELEASE_PR_MOVED_AFTER_PROBE")
        agent_id = proof.get("agent_id")
        cursor_run_id = proof.get("run_id")
        if not isinstance(agent_id, str) or not agent_id.startswith("bc-"):
            raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_PROOF_INVALID")
        agent_url = f"{CURSOR_API}/agents/{quote(agent_id, safe='')}"
        if is_failed_launch:
            if cursor_run_id is not None:
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_PROOF_INVALID")
            try:
                _cursor_receipt_mod.api_json(agent_url, token=cursor_token, basic=True)
            except ReceiptBlocked as exc:
                if exc.code != "STATUS_API_HTTP_404":
                    raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_RECHECK_UNVERIFIED") from None
            except Exception:
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_RECHECK_UNVERIFIED") from None
            else:
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_REACTIVATED")
            try:
                _cursor_receipt_mod.verify_negative_inventories(
                    cursor_token, agent_id=agent_id, pr_number=pr_number,
                    api=_cursor_receipt_mod.api_json,
                )
            except ReceiptBlocked as exc:
                if exc.code in {
                    "CURSOR_AGENT_INVENTORY_CONTRADICTION",
                    "CURSOR_GLOBAL_ACTIVE_AGENT_EXISTS",
                    "CURSOR_ACTIVE_PR_AGENT_EXISTS",
                }:
                    raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_REACTIVATED") from None
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_RECHECK_UNVERIFIED") from None
            except Exception:
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_RECHECK_UNVERIFIED") from None
        else:
            if not isinstance(cursor_run_id, str) or not cursor_run_id.startswith("run-"):
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_PROOF_INVALID")
            run_url = f"{agent_url}/runs/{quote(cursor_run_id, safe='')}"
            try:
                fresh_agent = _cursor_receipt_mod.api_json(agent_url, token=cursor_token, basic=True)
                fresh_run = _cursor_receipt_mod.api_json(run_url, token=cursor_token, basic=True)
            except Exception:
                # A transport or provider error is not proof that a writer stopped.
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_RECHECK_UNVERIFIED") from None
            returned_run_ids = ([fresh_run[key] for key in ("id", "runId")
                                 if key in fresh_run]
                                if isinstance(fresh_run, dict) else [])
            if (not isinstance(fresh_agent, dict)
                    or fresh_agent.get("id") != agent_id
                    or fresh_agent.get("latestRunId") != cursor_run_id
                    or fresh_agent.get("status") != "IDLE"
                    or not isinstance(fresh_run, dict)
                    or not returned_run_ids
                    or any(value != cursor_run_id for value in returned_run_ids)
                    or fresh_run.get("agentId") not in (None, agent_id)
                    or fresh_run.get("status") != "FINISHED"):
                raise ReleaseBlocked("REMOTE_RELEASE_PROVIDER_REACTIVATED")

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
    if is_failed_launch:
        doc["pr_sha"] = old["pr_sha"]
        doc["main_sha"] = old["main_sha"]
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
