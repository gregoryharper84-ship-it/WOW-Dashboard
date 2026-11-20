#!/usr/bin/env python3
"""WOW V17 single restoration loop (V1).

One queue, one active repair, one verified release at a time.

This module is a *router and evidence recorder*, not a new authority. It reuses
the existing engineering dispatch queue, the existing repair planner, and the
existing ``wow-engineering[bot]`` reservation marker. It never merges, deploys,
approves a review, publishes a probability, or grants execution authority.

The loop has six steps: INSPECT -> SELECT -> REPAIR -> VERIFY -> RELEASE ->
PROVE. Each cycle inspects production health, the active repair, CI and
deployment state, resumes unfinished work first, and records one durable
evidence record (commit, test result, deployment result, next action).

Four rules are enforced here:

* one writer -- an active repair is only resumable on the exact reserved head
  owned by the authenticated Engineering App; no blind redispatch;
* bounded recovery -- a transient failure is retried once, then only an
  authorized alternative; quota failures never create paid retries;
* work conservation -- a release hold blocks that release, not diagnosis and
  preparation of other restoration work;
* evidence -- a repeated heartbeat is explicitly not progress.

``can_execute=false`` and ``V17_TERMINAL_REDUCER`` hold everywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
SCHEMA_VERSION = "1.0"

RESERVATION_MARKER = "<!-- WOW_RECOVERY_ATTEMPTS -->"
EVIDENCE_MARKER = "<!-- WOW_RESTORATION_LOOP_EVIDENCE -->"
ENGINEERING_BOT = "wow-engineering[bot]"

MAX_REPAIR_ATTEMPTS = 3
TRANSIENT_FAILURE_CLASSES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "TRANSIENT_NETWORK",
        "CI_INFRASTRUCTURE",
    }
)
QUOTA_FAILURE_CLASSES = frozenset(
    {
        "PROVIDER_QUOTA_EXHAUSTED",
        "BILLING_QUOTA_EXHAUSTED",
        "CREDIT_EXHAUSTED",
    }
)
DETERMINISTIC_FAILURE_CLASSES = frozenset(
    {"TEST_REGRESSION", "MALFORMED_INPUT", "CODE_DEFECT"}
)

REPAIR_WORKFLOW = "wow-v17-engineering-auto-repair.yml"
PROTECTED_RELEASE_WORKFLOW = "wow-v17-governance-dispatcher.yml"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPTS_RE = re.compile(r"(?m)^- attempts: ([0-9]+)$")
_HEAD_RE = re.compile(r"(?m)^- head_sha: ([0-9a-f]{40})$")
_STATE_RE = re.compile(r"(?m)^- state: ([A-Z_]+)$")

HOLD = "HOLD"


# --------------------------------------------------------------------------- #
# small strict helpers
# --------------------------------------------------------------------------- #
def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA_RE.fullmatch(value))


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decision(
    step: str,
    action: str,
    reason_codes: list[str],
    *,
    hold: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "step": step,
        "action": action,
        "reason_codes": list(reason_codes),
        "hold": hold,
        "release_blocked": False,
        "unrelated_work_may_progress": True,
        "incident_id": None,
        "pr_number": None,
        "head_sha": None,
        "branch": None,
        "dispatch": None,
        "preparation": None,
        "release": None,
        "progress_made": True,
        "next_action": action,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# writer reservation reconciliation (first job)
# --------------------------------------------------------------------------- #
def reconcile_writer_reservation(
    comments: list[dict[str, Any]] | None,
    *,
    expected_head_sha: str | None = None,
) -> dict[str, Any]:
    """Classify the canonical Engineering App writer reservation.

    GitHub comments are audit data, not a compare-and-swap lease. This function
    therefore never proves ownership and never authorizes a dispatch: it only
    separates ``NO_RESERVATION`` (the existing dispatcher may reserve) from
    every ambiguous, foreign, stale or unconfirmed state, all of which hold.

    An accepted reservation on the exact head is an *active writer*: resuming it
    is allowed, minting a second dispatch is not.
    """
    if expected_head_sha is not None and not _is_sha(expected_head_sha):
        raise ValueError("EXPECTED_HEAD_SHA_INVALID")
    if comments is None:
        comments = []
    if not isinstance(comments, list):
        raise ValueError("RESERVATION_COMMENTS_INVALID")

    bot: list[dict[str, Any]] = []
    foreign = 0
    for comment in comments:
        if not isinstance(comment, dict):
            raise ValueError("RESERVATION_COMMENT_INVALID")
        body = comment.get("body")
        if not isinstance(body, str) or RESERVATION_MARKER not in body:
            continue
        login = (comment.get("user") or {}).get("login")
        if login == ENGINEERING_BOT:
            bot.append(comment)
        else:
            foreign += 1

    base = {
        "schema_version": SCHEMA_VERSION,
        "marker": RESERVATION_MARKER,
        "writer_owner": ENGINEERING_BOT,
        "dispatch_allowed": False,
        "comment_id": None,
        "attempts": None,
        "reservation_state": None,
        "head_sha": None,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }

    if foreign:
        return {**base, "status": "FOREIGN_OWNERSHIP_PRESENT",
                "reason_codes": ["WRITER_OWNERSHIP_NOT_AUTHENTICATED"]}
    if not bot:
        return {**base, "status": "NO_RESERVATION", "dispatch_allowed": True,
                "reason_codes": ["NO_WRITER_RESERVATION"]}
    if len(bot) > 1:
        return {**base, "status": "AMBIGUOUS_MULTIPLE_RESERVATIONS",
                "reason_codes": ["WRITER_RESERVATION_NOT_UNIQUE"]}

    comment = bot[0]
    body = comment["body"]
    attempts = _ATTEMPTS_RE.search(body)
    head = _HEAD_RE.search(body)
    state = _STATE_RE.search(body)
    comment_id = comment.get("id")
    if (
        attempts is None
        or head is None
        or state is None
        or not isinstance(comment_id, int)
        or isinstance(comment_id, bool)
        or comment_id <= 0
    ):
        return {**base, "status": "INVALID_RESERVATION",
                "reason_codes": ["WRITER_RESERVATION_MALFORMED"]}

    row = {
        **base,
        "comment_id": comment_id,
        "attempts": int(attempts.group(1)),
        "head_sha": head.group(1),
        "reservation_state": state.group(1),
    }
    if expected_head_sha is not None and row["head_sha"] != expected_head_sha:
        return {**row, "status": "ACCEPTED_STALE_HEAD" if state.group(1) == "DISPATCH_ACCEPTED"
                else "RESERVED_STALE_HEAD",
                "reason_codes": ["WRITER_RESERVATION_DIFFERENT_HEAD"]}
    if state.group(1) == "DISPATCH_RESERVED":
        # The dispatch outcome is unknown; a run may exist. Never resend.
        return {**row, "status": "RESERVED_UNCONFIRMED",
                "reason_codes": ["WRITER_DISPATCH_OUTCOME_UNKNOWN"]}
    if state.group(1) == "DISPATCH_ACCEPTED":
        return {**row, "status": "ACCEPTED_EXACT_HEAD",
                "reason_codes": ["WRITER_RESERVATION_ACCEPTED"]}
    return {**row, "status": "INVALID_RESERVATION",
            "reason_codes": ["WRITER_RESERVATION_STATE_UNRECOGNIZED"]}


# --------------------------------------------------------------------------- #
# bounded recovery
# --------------------------------------------------------------------------- #
def decide_bounded_recovery(
    *,
    failure_class: str,
    retries_used: int,
    authorized_alternative: str | None = None,
) -> dict[str, Any]:
    """Retry a transient failure once, then require an authorized alternative."""
    key = str(failure_class or "").strip().upper()
    if not isinstance(retries_used, int) or isinstance(retries_used, bool) or retries_used < 0:
        raise ValueError("RETRIES_USED_INVALID")
    alternative = str(authorized_alternative or "").strip() or None
    base = {
        "failure_class": key,
        "retries_used": retries_used,
        "authorized_alternative": alternative,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
    if not key:
        return {**base, "action": HOLD, "reason_codes": ["FAILURE_CLASS_MISSING"]}
    if key in QUOTA_FAILURE_CLASSES:
        if alternative:
            return {**base, "action": "USE_AUTHORIZED_ALTERNATIVE",
                    "reason_codes": ["QUOTA_NO_PAID_RETRY"]}
        return {**base, "action": HOLD, "reason_codes": ["QUOTA_NO_PAID_RETRY_NO_ALTERNATIVE"]}
    if key in TRANSIENT_FAILURE_CLASSES:
        if retries_used == 0:
            return {**base, "action": "RETRY_ONCE", "reason_codes": ["TRANSIENT_RETRY_ONCE"]}
        if alternative:
            return {**base, "action": "USE_AUTHORIZED_ALTERNATIVE",
                    "reason_codes": ["TRANSIENT_RETRY_EXHAUSTED"]}
        return {**base, "action": HOLD, "reason_codes": ["TRANSIENT_RETRY_EXHAUSTED"]}
    if key in DETERMINISTIC_FAILURE_CLASSES:
        return {**base, "action": "REPAIR_INPUT", "reason_codes": ["DETERMINISTIC_FAILURE_NOT_RETRIED"]}
    return {**base, "action": HOLD, "reason_codes": ["FAILURE_CLASS_UNCLASSIFIED"]}


# --------------------------------------------------------------------------- #
# metrics and progress
# --------------------------------------------------------------------------- #
def compute_metrics(evidence: dict[str, Any] | None, *, now_utc: str) -> dict[str, Any]:
    """Measure verified repairs and time since the last verified repair.

    A cycle heartbeat is never counted as a repair. Unknown or absent evidence
    is reported as never verified, never as a green score.
    """
    now = _parse_utc(now_utc)
    if now is None:
        raise ValueError("NOW_UTC_INVALID")
    evidence = evidence or {}
    if not isinstance(evidence, dict):
        raise ValueError("EVIDENCE_INVALID")

    ids = evidence.get("verified_incident_ids") or []
    if not isinstance(ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in ids
    ):
        raise ValueError("VERIFIED_INCIDENT_IDS_INVALID")
    verified = len({value.strip() for value in ids})

    last_raw = evidence.get("last_verified_repair_at")
    last = _parse_utc(last_raw) if last_raw else None
    if last_raw and last is None:
        raise ValueError("LAST_VERIFIED_REPAIR_AT_INVALID")
    seconds = int((now - last).total_seconds()) if last is not None else None
    return {
        "verified_repairs": verified,
        "last_verified_repair_at": _iso(last) if last is not None else None,
        "seconds_since_last_verified_repair": max(0, seconds) if seconds is not None else None,
        "status": "VERIFIED" if last is not None else "NEVER_VERIFIED",
    }


def _progress_made(decision: dict[str, Any], last_cycle: dict[str, Any] | None) -> bool:
    if not isinstance(last_cycle, dict):
        return True
    for key in ("step", "action", "incident_id", "head_sha"):
        if last_cycle.get(key) != decision.get(key):
            return True
    return False


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def _queue_error(queue: Any) -> str | None:
    if not isinstance(queue, dict):
        return "QUEUE_INVALID"
    if queue.get("can_execute") is not False:
        return "QUEUE_EXECUTION_AUTHORITY_BROKEN"
    if queue.get("terminal_authority") != TERMINAL_AUTHORITY:
        return "QUEUE_TERMINAL_AUTHORITY_BROKEN"
    if not isinstance(queue.get("records"), list):
        return "QUEUE_RECORDS_INVALID"
    return None


def _release_decision(
    release: dict[str, Any], *, now_utc: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve one in-flight release. Returns (decision, reason_codes)."""
    issue_number = release.get("issue_number")
    pr_number = release.get("pr_number")
    merged = release.get("merged_commit")
    state = str(release.get("state") or "").upper()
    if not _positive_int(issue_number) or not _positive_int(pr_number):
        return _decision("PROVE", HOLD, ["RELEASE_IDENTITY_INVALID"], hold=True), ["RELEASE_IDENTITY_INVALID"]
    if not _is_sha(merged):
        return _decision("PROVE", HOLD, ["RELEASE_MERGED_COMMIT_INVALID"], hold=True), [
            "RELEASE_MERGED_COMMIT_INVALID"
        ]

    identity = {
        "incident_id": str(issue_number),
        "pr_number": pr_number,
        "release": {"state": state, "merged_commit": merged,
                    "deployed_commit": release.get("deployed_commit")},
    }

    if state == "MERGED_PENDING_DEPLOY":
        return _decision(
            "RELEASE", "AWAIT_DEPLOY", ["RELEASE_MERGED_AWAITING_DEPLOY"],
            progress_made=False, next_action="AWAIT_DEPLOY", **identity,
        ), ["RELEASE_MERGED_AWAITING_DEPLOY"]

    if state != "DEPLOYED_PENDING_VERIFY":
        return _decision(
            "PROVE", HOLD, ["RELEASE_STATE_UNRECOGNIZED"], hold=True, **identity,
        ), ["RELEASE_STATE_UNRECOGNIZED"]

    deployed = release.get("deployed_commit")
    if not _is_sha(deployed):
        return _decision(
            "PROVE", HOLD, ["DEPLOYED_COMMIT_UNAVAILABLE"], hold=True, **identity,
        ), ["DEPLOYED_COMMIT_UNAVAILABLE"]
    if deployed != merged:
        # This may be a newer protected-main deployment that already contains
        # the repair. A non-equal SHA is NOT evidence of ancestry. Never advise
        # rolling production back to the older merge without authenticated
        # lineage and a real user-function probe.
        return _decision(
            "PROVE", HOLD, ["DEPLOYED_COMMIT_MISMATCH"], hold=True,
            next_action="VERIFY_LIVE_ANCESTRY_AND_USER_FUNCTION", **identity,
        ), ["DEPLOYED_COMMIT_MISMATCH"]

    probe = release.get("probe")
    if not isinstance(probe, dict):
        return _decision(
            "PROVE", HOLD, ["USER_FUNCTION_PROBE_MISSING"], hold=True,
            next_action="EXERCISE_REPAIRED_USER_FUNCTION", **identity,
        ), ["USER_FUNCTION_PROBE_MISSING"]
    if probe.get("verified_commit") != deployed:
        return _decision(
            "PROVE", HOLD, ["PROBE_COMMIT_MISMATCH"], hold=True,
            next_action="RERUN_PROBE_ON_DEPLOYED_COMMIT", **identity,
        ), ["PROBE_COMMIT_MISMATCH"]
    if probe.get("passed") is not True or not str(probe.get("evidence_ref") or "").strip():
        return _decision(
            "PROVE", HOLD, ["USER_FUNCTION_NOT_PROVEN"], hold=True,
            next_action="KEEP_ISSUE_OPEN_AND_REPAIR", **identity,
        ), ["USER_FUNCTION_NOT_PROVEN"]

    return _decision(
        "PROVE", "CLOSE_VERIFIED_ISSUE",
        ["DEPLOYED_COMMIT_VERIFIED", "USER_FUNCTION_PROVEN"],
        verified=True,
        verified_incident_id=str(issue_number),
        verified_at_utc=now_utc,
        next_action="CLOSE_ISSUE_AND_SELECT_NEXT_REPAIR",
        dispatch={"issue_close": True},
        **identity,
    ), ["DEPLOYED_COMMIT_VERIFIED", "USER_FUNCTION_PROVEN"]


def _ci_status(ci: Any) -> tuple[str, list[str], list[str]]:
    if not isinstance(ci, dict):
        return "UNKNOWN", [], []
    status = str(ci.get("status") or "").upper()
    required = [str(name) for name in (ci.get("required") or []) if str(name).strip()]
    failed = [str(name) for name in (ci.get("failed") or []) if str(name).strip()]
    if status not in {"PENDING", "PASSED", "FAILED"}:
        return "UNKNOWN", required, failed
    if status == "PASSED" and (not required or failed):
        return "UNKNOWN", required, failed
    return status, required, failed


def required_ci(checks: list[dict[str, Any]], rules: list[dict[str, Any]],
                head_sha: str) -> dict[str, Any]:
    """Observe active branch requirements, bound to publisher and exact head.

    This is diagnostic only. Classic protection/status contexts not exposed by
    this inventory remain UNKNOWN/PENDING; the existing release lane retains
    authority to resolve all native requirements.
    """
    required: set[tuple[str, int | None]] = set()
    try:
        for rule in rules:
            if rule.get("type") == "required_status_checks":
                for item in rule["parameters"]["required_status_checks"]:
                    name, app_id = item["context"], item.get("integration_id")
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("REQUIRED_CONTEXT_INVALID")
                    if app_id is not None and not _positive_int(app_id):
                        raise ValueError("REQUIRED_APP_INVALID")
                    required.add((name, app_id))
        if not required or not _is_sha(head_sha):
            raise ValueError("REQUIRED_INVENTORY_MISSING")
        failed, pending = [], []
        for name, app_id in sorted(required, key=lambda item: (item[0], item[1] or 0)):
            bound = [row for row in checks
                     if row.get("name") == name and row.get("head_sha") == head_sha
                     and (app_id is None or (row.get("app") or {}).get("id") == app_id)]
            if not bound:
                pending.append(name)
                continue
            if any(not _positive_int(row.get("id")) for row in bound):
                raise ValueError("CHECK_ID_INVALID")
            latest = max(bound, key=lambda row: row["id"])
            if latest.get("status") != "completed":
                pending.append(name)
            elif latest.get("conclusion") != "success":
                failed.append(name)
        return {"status": "FAILED" if failed else "PENDING" if pending else "PASSED",
                "required": sorted({name for name, _ in required}),
                "failed": failed, "pending": pending}
    except (TypeError, KeyError, AttributeError, ValueError):
        return {"status": "UNKNOWN", "required": [], "failed": []}


def _repair_decision(
    repair: dict[str, Any], *, now_utc: str, metrics: dict[str, Any]
) -> dict[str, Any]:
    issue_number = repair.get("issue_number")
    pr_number = repair.get("pr_number")
    head_sha = repair.get("head_sha")
    branch = repair.get("branch")
    if not _positive_int(issue_number) or not _positive_int(pr_number):
        return _decision("INSPECT", HOLD, ["ACTIVE_REPAIR_IDENTITY_INVALID"], hold=True)
    if not _is_sha(head_sha):
        return _decision("INSPECT", HOLD, ["ACTIVE_REPAIR_HEAD_INVALID"], hold=True,
                         incident_id=str(issue_number), pr_number=pr_number)
    if not isinstance(branch, str) or not branch.strip():
        return _decision("INSPECT", HOLD, ["ACTIVE_REPAIR_BRANCH_INVALID"], hold=True,
                         incident_id=str(issue_number), pr_number=pr_number, head_sha=head_sha)

    identity = {
        "incident_id": str(issue_number),
        "pr_number": pr_number,
        "head_sha": head_sha,
        "branch": branch,
    }

    try:
        reservation = reconcile_writer_reservation(
            repair.get("reservation_comments"), expected_head_sha=head_sha
        )
    except ValueError as exc:
        return _decision("REPAIR", HOLD, [str(exc)], hold=True, **identity)

    reservation_status = reservation["status"]
    if reservation_status != "ACCEPTED_EXACT_HEAD":
        action = (
            "RECONCILE_ACTIVE_REPAIR"
            if reservation_status == "RESERVED_UNCONFIRMED"
            else HOLD
        )
        return _decision(
            "REPAIR",
            action,
            [f"WRITER_{reservation_status}"],
            hold=(action == HOLD),
            next_action=(
                "READ_ONLY_RECONCILE_DISPATCH_OUTCOME"
                if action != HOLD
                else "RESTORE_AUTHENTICATED_WRITER_OWNERSHIP"
            ),
            writer_reservation=reservation,
            **identity,
        )

    acceptance = repair.get("acceptance_test")
    if not isinstance(acceptance, str) or not acceptance.strip():
        return _decision(
            "REPAIR", HOLD, ["ACCEPTANCE_TEST_MISSING"], hold=True,
            next_action="ATTACH_CONCRETE_ACCEPTANCE_TEST",
            writer_reservation=reservation, **identity,
        )

    attempts = reservation.get("attempts")
    if isinstance(attempts, int) and attempts >= MAX_REPAIR_ATTEMPTS:
        return _decision(
            "REPAIR", HOLD, ["REPAIR_RETRY_CAP_REACHED"], hold=True,
            next_action="NEEDS_OWNER_RETRY_CAP", needs_owner=True,
            writer_reservation=reservation, **identity,
        )

    failure = repair.get("repair_failure")
    if failure:
        try:
            recovery = decide_bounded_recovery(
                failure_class=str(failure.get("class") or ""),
                retries_used=int(failure.get("retries_used") or 0),
                authorized_alternative=failure.get("authorized_alternative"),
            )
        except (TypeError, ValueError) as exc:
            return _decision("REPAIR", HOLD, [str(exc)], hold=True, **identity)
        if recovery["action"] == "RETRY_ONCE":
            return _decision(
                "REPAIR", "RETRY_SAME_REPAIR_ONCE", recovery["reason_codes"],
                next_action="RETRY_SAME_PR_AND_BRANCH_ONCE",
                dispatch={"workflow": REPAIR_WORKFLOW, "mode": "existing_auto_repair"},
                recovery=recovery, writer_reservation=reservation, **identity,
            )
        if recovery["action"] == "USE_AUTHORIZED_ALTERNATIVE":
            return _decision(
                "REPAIR", "USE_AUTHORIZED_ALTERNATIVE", recovery["reason_codes"],
                next_action="DISPATCH_AUTHORIZED_ALTERNATIVE",
                dispatch={"workflow": REPAIR_WORKFLOW, "mode": "existing_auto_repair"},
                recovery=recovery, writer_reservation=reservation, **identity,
            )
        if recovery["action"] == "REPAIR_INPUT":
            return _decision(
                "REPAIR", "REPAIR_DETERMINISTIC_FAILURE", recovery["reason_codes"],
                next_action="FIX_DETERMINISTIC_INPUT_ON_SAME_PR",
                recovery=recovery, writer_reservation=reservation, **identity,
            )
        return _decision(
            "REPAIR", HOLD, recovery["reason_codes"], hold=True,
            next_action="NO_AUTHORIZED_RECOVERY_WITHOUT_OWNER",
            recovery=recovery, writer_reservation=reservation, **identity,
        )

    ci_status, required, failed = _ci_status(repair.get("ci"))
    if ci_status == "UNKNOWN":
        return _decision(
            "VERIFY", HOLD, ["REQUIRED_CHECKS_OBSERVATION_INVALID"], hold=True,
            next_action="COLLECT_EXACT_HEAD_REQUIRED_CHECKS",
            writer_reservation=reservation, **identity,
        )
    if ci_status == "FAILED":
        return _decision(
            "VERIFY", "FEEDBACK_TO_SAME_REPAIR", ["REQUIRED_CHECKS_FAILED"],
            next_action="REPAIR_SAME_PR_FROM_FAILURE_EVIDENCE",
            failed_checks=failed,
            dispatch={"workflow": REPAIR_WORKFLOW, "mode": "existing_auto_repair"},
            writer_reservation=reservation, **identity,
        )
    if ci_status == "PENDING":
        return _decision(
            "VERIFY", "AWAIT_CI", ["REQUIRED_CHECKS_PENDING"], progress_made=False,
            next_action="AWAIT_EXACT_HEAD_CHECKS", **identity,
        )

    hold = repair.get("release_hold")
    if hold:
        reason = str((hold or {}).get("reason") or "RELEASE_HOLD").strip() or "RELEASE_HOLD"
        return _decision(
            "RELEASE", "HOLD_RELEASE_ONLY", ["RELEASE_HELD", reason],
            hold=True,
            release_blocked=True,
            unrelated_work_may_progress=True,
            next_action="HOLD_THIS_RELEASE_ONLY",
            preparation={
                "allowed_actions": ["DIAGNOSE", "PREPARE_ACCEPTANCE_TEST"],
                "writer_dispatch_allowed": False,
            },
            writer_reservation=reservation, **identity,
        )

    return _decision(
        "RELEASE", "HAND_OFF_PROTECTED_RELEASE",
        ["REQUIRED_CHECKS_PASSED", "ACCEPTANCE_TEST_PRESENT"],
        next_action="EXISTING_PROTECTED_RELEASE_PATH",
        dispatch={"workflow": PROTECTED_RELEASE_WORKFLOW, "mode": "existing_protected_router"},
        writer_reservation=reservation, **identity,
    )


def _select_decision(
    queue: dict[str, Any], *, metrics: dict[str, Any]
) -> dict[str, Any]:
    records = queue["records"]
    if not records:
        return _decision(
            "SELECT", "IDLE_NO_WORK", ["QUEUE_EMPTY"], progress_made=False,
            next_action="AWAIT_NEXT_OBSERVATION",
        )

    try:
        from .engineering_agent_team import select_dual_stream_work
    except ImportError:  # pragma: no cover - legacy standalone import
        from engineering_agent_team import select_dual_stream_work

    selected = select_dual_stream_work(records).restoration
    if selected.incident_id is None:
        return _decision(
            "SELECT", "IDLE_NO_WORK", ["NO_ACTIONABLE_INCIDENT"], progress_made=False,
            next_action="AWAIT_NEXT_OBSERVATION",
        )

    row = next(
        (item for item in records if str(item.get("incident_id")) == selected.incident_id),
        None,
    )
    if row is None:
        return _decision("SELECT", HOLD, ["SELECTED_INCIDENT_NOT_IN_QUEUE"], hold=True)

    identity = {"incident_id": selected.incident_id}
    acceptance = row.get("acceptance_test")
    if not isinstance(acceptance, str) or not acceptance.strip():
        return _decision(
            "SELECT", HOLD, ["ACCEPTANCE_TEST_MISSING"], hold=True,
            next_action="ATTACH_CONCRETE_ACCEPTANCE_TEST", **identity,
        )
    if not _positive_int(row.get("pr_number")) or not _is_sha(row.get("head_sha")):
        return _decision(
            "SELECT", HOLD, ["EXISTING_PR_OR_BRANCH_MISSING"], hold=True,
            next_action="REUSE_EXISTING_PR_AND_BRANCH", **identity,
        )
    return _decision(
        "REPAIR", "RESERVE_AND_DISPATCH_REPAIR",
        ["HIGHEST_PRIORITY_RESTORATION_SELECTED"],
        next_action="RESERVE_EXACT_HEAD_THEN_DISPATCH_ONE_AGENT",
        dispatch={"workflow": REPAIR_WORKFLOW, "mode": "existing_auto_repair"},
        pr_number=row.get("pr_number"),
        head_sha=row.get("head_sha"),
        branch=row.get("branch"),
        acceptance_test=acceptance,
        **identity,
    )


def _plan_cycle(observation: dict[str, Any]) -> dict[str, Any]:
    """Return the single next action for one 15-minute restoration cycle.

    Unfinished work is resumed before new work is selected. The result always
    carries ``can_execute=false`` and ``V17_TERMINAL_REDUCER``.
    """
    if not isinstance(observation, dict):
        return _decision("INSPECT", HOLD, ["OBSERVATION_INVALID"], hold=True)
    now_raw = observation.get("now_utc")
    now = _parse_utc(now_raw)
    if now is None:
        return _decision("INSPECT", HOLD, ["NOW_UTC_INVALID"], hold=True)
    now_utc = _iso(now)

    errors = observation.get("collection_errors") or []
    if not isinstance(errors, list) or errors:
        return _decision(
            "INSPECT", HOLD, ["OBSERVATION_INCOMPLETE"], hold=True,
            next_action="RESTORE_READ_ONLY_OBSERVATION",
            collection_errors=errors if isinstance(errors, list) else ["COLLECTION_ERRORS_INVALID"],
        )

    queue_error = _queue_error(observation.get("queue"))
    if queue_error:
        return _decision("INSPECT", HOLD, [queue_error], hold=True)

    releases = observation.get("releases_in_flight") or []
    if not isinstance(releases, list):
        return _decision("INSPECT", HOLD, ["RELEASES_IN_FLIGHT_INVALID"], hold=True)
    if len(releases) > 1:
        return _decision(
            "RELEASE", HOLD, ["MULTIPLE_ACTIVE_RELEASES"], hold=True,
            next_action="RESOLVE_TO_ONE_VERIFIED_RELEASE",
        )
    repair = observation.get("active_repair")
    if repair is not None and not isinstance(repair, dict):
        return _decision("INSPECT", HOLD, ["ACTIVE_REPAIR_INVALID"], hold=True)
    if repair is not None and releases:
        if str(repair.get("issue_number")) == str(releases[0].get("issue_number")):
            return _decision(
                "INSPECT", HOLD, ["DUPLICATE_ACTIVE_UNIT"], hold=True,
                next_action="KEEP_ONE_ACTIVE_REPAIR_OR_RELEASE",
                incident_id=str(repair.get("issue_number")),
            )

    try:
        metrics = compute_metrics(observation.get("evidence"), now_utc=now_utc)
    except ValueError as exc:
        return _decision("INSPECT", HOLD, [str(exc)], hold=True)

    production = observation.get("production")
    deployed = production.get("deployed_commit") if isinstance(production, dict) else None
    if isinstance(releases, list):
        releases = [
            {
                **release,
                "deployed_commit": release.get("deployed_commit", deployed),
            }
            if isinstance(release, dict)
            else release
            for release in releases
        ]

    # 1. A deployed release is one short, deterministic step: verify or hold.
    if releases and not isinstance(releases[0], dict):
        return _decision("INSPECT", HOLD, ["RELEASE_RECORD_INVALID"], hold=True)
    if releases and str(releases[0].get("state") or "").upper() == "DEPLOYED_PENDING_VERIFY":
        decision, _codes = _release_decision(releases[0], now_utc=now_utc)
        assert decision is not None
    # 2. Resume the active repair before selecting new work.
    elif repair is not None:
        decision = _repair_decision(repair, now_utc=now_utc, metrics=metrics)
    # 3. Otherwise finish an already-merged release.
    elif releases:
        if not isinstance(releases[0], dict):
            return _decision("INSPECT", HOLD, ["RELEASE_RECORD_INVALID"], hold=True)
        decision, _codes = _release_decision(releases[0], now_utc=now_utc)
        assert decision is not None
    # 4. Select the highest-priority restoration incident from the single queue.
    else:
        decision = _select_decision(observation["queue"], metrics=metrics)

    decision["verified_incident_ids"] = sorted(set(
        (observation.get("evidence") or {}).get("verified_incident_ids") or []))
    if decision.get("verified") is True:
        verified_id = str(decision.get("verified_incident_id") or "")
        if verified_id and verified_id not in ((observation.get("evidence") or {}).get("verified_incident_ids") or []):
            metrics = {
                **metrics,
                "verified_repairs": metrics["verified_repairs"] + 1,
                "last_verified_repair_at": now_utc,
                "seconds_since_last_verified_repair": 0,
                "status": "VERIFIED",
            }
        decision["verified_incident_ids"] = sorted(
            set((observation.get("evidence") or {}).get("verified_incident_ids") or [])
            | ({verified_id} if verified_id else set())
        )

    decision["progress_made"] = (
        decision.get("progress_made", True)
        and _progress_made(decision, observation.get("last_cycle"))
    )
    decision["heartbeat_only"] = not decision["progress_made"]
    decision["metrics"] = metrics
    decision["cycle_at_utc"] = now_utc
    decision["production"] = {
        "reachable": bool(production.get("reachable")) if isinstance(production, dict) else None,
        "deployed_commit": deployed,
    }
    decision["evidence"] = build_evidence_record(decision, observation)
    decision["can_execute"] = False
    decision["terminal_authority"] = TERMINAL_AUTHORITY
    return decision


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #
def plan_cycle(observation: dict[str, Any]) -> dict[str, Any]:
    """Finalize diagnostic holds as well as normal decisions for the summary."""
    decision = _plan_cycle(observation)
    if "evidence" not in decision:
        observed = observation if isinstance(observation, dict) else {}
        now = _parse_utc(observed.get("now_utc"))
        decision.update(cycle_at_utc=_iso(now) if now else None,
                        progress_made=False, heartbeat_only=True)
        try:
            decision["metrics"] = compute_metrics(observed.get("evidence"),
                                                  now_utc=observed.get("now_utc"))
            decision["verified_incident_ids"] = sorted(set(
                (observed.get("evidence") or {}).get("verified_incident_ids") or []))
        except ValueError:
            decision["metrics"] = {"status": "UNKNOWN", "verified_repairs": 0}
        decision["evidence"] = build_evidence_record(decision, observed)
    return decision


def build_evidence_record(
    decision: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    """Record the commit, test result, deployment result and next action."""
    repair = observation.get("active_repair") or {}
    ci_status, required, failed = _ci_status(repair.get("ci") if isinstance(repair, dict) else None)
    releases = observation.get("releases_in_flight") or []
    release = (
        releases[0]
        if isinstance(releases, list) and releases and isinstance(releases[0], dict)
        else {}
    )
    if ci_status == "UNKNOWN" and decision.get("verified") is True:
        ci_status = "PROBE_PASSED"
    production = observation.get("production") if isinstance(observation.get("production"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "cycle_at_utc": decision.get("cycle_at_utc"),
        "step": decision.get("step"),
        "action": decision.get("action"),
        "incident_id": decision.get("incident_id"),
        "pr_number": decision.get("pr_number"),
        "head_sha": decision.get("head_sha"),
        "commit": decision.get("head_sha") or release.get("merged_commit") or production.get("deployed_commit"),
        "test_result": {
            "status": ci_status,
            "required": required,
            "failed": failed,
        },
        "deployment_result": {
            "state": release.get("state"),
            "deployed_commit": production.get("deployed_commit"),
        },
        "next_action": decision.get("next_action"),
        "reason_codes": decision.get("reason_codes") or [],
        "progress_made": decision.get("progress_made") is True,
        "metrics": decision.get("metrics"),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _csv(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return ",".join(str(value) for value in values if str(value).strip())


def render_evidence_comment(decision: dict[str, Any]) -> str:
    """Render one machine-readable, in-place cycle record."""
    record = decision.get("evidence") or {}
    metrics = decision.get("metrics") or {}
    test_result = record.get("test_result") or {}
    deployment = record.get("deployment_result") or {}
    lines = [
        EVIDENCE_MARKER,
        f"### WOW V17 restoration loop — {record.get('step')}/{record.get('action')}",
        f"- cycle_at_utc: {record.get('cycle_at_utc')}",
        f"- incident_id: {record.get('incident_id') or ''}",
        f"- pr_number: {record.get('pr_number') or ''}",
        f"- head_sha: {record.get('head_sha') or ''}",
        f"- commit: {record.get('commit') or ''}",
        f"- test_result: {test_result.get('status') or 'UNKNOWN'}"
        f" (failed={_csv(test_result.get('failed')) or 'none'})",
        f"- deployment_result: {deployment.get('state') or 'NONE'}"
        f" (deployed_commit={deployment.get('deployed_commit') or ''})",
        f"- next_action: {record.get('next_action') or ''}",
        f"- reason_codes: {_csv(record.get('reason_codes'))}",
        f"- collection_errors: {_csv(decision.get('collection_errors'))}",
        f"- progress_made: {str(record.get('progress_made')).lower()}",
        f"- hold: {str(decision.get('hold')).lower()}",
        f"- verified_incident_ids: {_csv(decision.get('verified_incident_ids'))}",
        f"- verified_repairs: {metrics.get('verified_repairs', 0)}",
        f"- last_verified_repair_at: {metrics.get('last_verified_repair_at') or ''}",
        f"- seconds_since_last_verified_repair: {metrics.get('seconds_since_last_verified_repair') if metrics.get('seconds_since_last_verified_repair') is not None else ''}",
        "- can_execute: false",
        f"- terminal_authority: {TERMINAL_AUTHORITY}",
    ]
    return "\n".join(lines)


def parse_evidence_comment(comments: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Read the latest loop evidence comment into metrics input."""
    if not comments:
        return None
    found: dict[str, Any] | None = None
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        if (comment.get("user") or {}).get("login") != "github-actions[bot]":
            continue
        body = comment.get("body")
        if not isinstance(body, str) or EVIDENCE_MARKER not in body:
            continue
        ids: list[str] = []
        last_at: str | None = None
        for line in body.splitlines():
            if line.startswith("- verified_incident_ids: "):
                ids = [item.strip() for item in line.split(":", 1)[1].split(",") if item.strip()]
            elif line.startswith("- last_verified_repair_at: "):
                value = line.split(":", 1)[1].strip()
                last_at = value or None
        found = {"verified_incident_ids": ids, "last_verified_repair_at": last_at}
    return found


# --------------------------------------------------------------------------- #
# read-only collection (network lives here and nowhere else)
# --------------------------------------------------------------------------- #
class CollectionError(Exception):
    pass


def _gh_pages(path: str) -> list[dict[str, Any]]:
    cmd = ["gh", "api", "--paginate", "--slurp", path]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=90
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise CollectionError(f"GH_API_FAILED:{type(exc).__name__}") from exc
    try:
        pages = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise CollectionError("GH_API_INVALID_JSON") from exc
    items: list[dict[str, Any]] = []
    for page in pages if isinstance(pages, list) else []:
        if isinstance(page, list):
            items.extend(item for item in page if isinstance(item, dict))
        elif isinstance(page, dict):
            items.append(page)
    return items


def _http_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _acceptance_test(body: str) -> str:
    match = re.search(r"(?im)^Acceptance-Test:[ \t]*(.+)$", body)
    return match.group(1).strip() if match else ""


def _production_observation(base_url: str | None) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not base_url:
        return {"reachable": False, "deployed_commit": None}, ["PRODUCTION_URL_MISSING"]
    try:
        payload = _http_json(base_url.rstrip("/") + "/health/live")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {"reachable": False, "deployed_commit": None}, ["PRODUCTION_HEALTH_UNREACHABLE"]
    healthy = isinstance(payload, dict) and payload.get("status") in {None, "ok", "healthy", "live"}
    return {"reachable": bool(healthy), "deployed_commit": None}, errors


def _render_live_commit(service_id: str | None, api_key: str | None) -> tuple[str | None, list[str]]:
    # Absent optional proof configuration is an unknown deployed commit, not a
    # failed observation: repair/verify work must continue while PROVE holds.
    if not service_id or not api_key:
        return None, []
    try:
        payload = _http_json(
            f"https://api.render.com/v1/services/{service_id}/deploys?limit=5",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, ["RENDER_API_UNREACHABLE"]
    for deploy in payload if isinstance(payload, list) else []:
        row = deploy.get("deploy") if isinstance(deploy, dict) else None
        if not isinstance(row, dict) or row.get("status") != "live":
            continue
        commit = (row.get("commit") or {}).get("id")
        if _is_sha(commit):
            return commit, []
    return None, ["NO_LIVE_RENDER_DEPLOY"]


def collect_observation(
    *,
    repo: str,
    manifest_path: str,
    now_utc: str,
    production_url: str | None = None,
    render_service_id: str | None = None,
    render_api_key: str | None = None,
    ledger_issue: int | None = None,
    gh_pages: Callable[[str], list[dict[str, Any]]] = _gh_pages,
    http_json: Callable[..., Any] = _http_json,
) -> dict[str, Any]:
    """Collect one read-only observation. Failures become typed holds, not crashes."""
    from .engineering_dispatch_queue import build_queue
    from .engineering_incident_identity import exact_incident_id

    errors: list[str] = []
    try:
        issues = [
            issue
            for issue in gh_pages(f"/repos/{repo}/issues?state=open&per_page=100")
            if "pull_request" not in issue
        ]
    except CollectionError as exc:
        issues = []
        errors.append(str(exc))
    manifest = json.loads(Path(manifest_path).read_text())
    queue = build_queue(manifest, issues)

    active_repair: dict[str, Any] | None = None
    releases: list[dict[str, Any]] = []
    try:
        pulls = gh_pages(f"/repos/{repo}/pulls?state=all&per_page=100")
    except CollectionError as exc:
        pulls = []
        errors.append(str(exc))
    bot_pulls = [
        pull
        for pull in pulls
        if (pull.get("user") or {}).get("login") == ENGINEERING_BOT
        and (pull.get("base") or {}).get("ref") == "main"
        and ((pull.get("head") or {}).get("repo") or {}).get("full_name") == repo
    ]
    bot_pulls.sort(key=lambda pull: int(pull.get("number") or 0))

    ranks = {
        str(record.get("incident_id")): (
            int(record.get("priority_rank") or 9999),
            str(record.get("severity") or "P4"),
        )
        for record in queue["records"]
    }
    open_bot_pulls = [
        pull for pull in bot_pulls if pull.get("state") == "open" and not pull.get("draft")
    ]
    ranked_pulls: list[tuple[tuple[int, str], int, dict[str, Any], str | None]] = []
    for pull in open_bot_pulls:
        try:
            pull_incident: str | None = exact_incident_id(str(pull.get("body") or ""))
        except ValueError:  # identity failure is evidence, not a crash
            pull_incident = None
        ranked_pulls.append(
            (
                ranks.get(str(pull_incident), (9999, "P4")),
                int(pull["number"]),
                pull,
                pull_incident,
            )
        )
    ranked_pulls.sort(key=lambda item: (item[0][0], item[0][1], item[1]))
    if ranked_pulls:
        _, _, pull, incident = ranked_pulls[0]
        pr_number = int(pull["number"])
        try:
            comments = gh_pages(f"/repos/{repo}/issues/{pr_number}/comments?per_page=100")
            checks = gh_pages(f"/repos/{repo}/commits/{pull['head']['sha']}/check-runs?per_page=100")
            rules = gh_pages(f"/repos/{repo}/rules/branches/main")
        except CollectionError as exc:
            comments, checks, rules = [], [], []
            errors.append(str(exc))
        check_runs = [
            run
            for page in checks
            for run in (page.get("check_runs") or [] if isinstance(page, dict) else [])
            if isinstance(run, dict)
        ]
        active_repair = {
            "issue_number": int(incident) if incident else 0,
            "pr_number": pr_number,
            "head_sha": pull["head"]["sha"],
            "branch": pull["head"]["ref"],
            "acceptance_test": _acceptance_test(str(pull.get("body") or "")),
            "reservation_comments": comments,
            "ci": required_ci(check_runs, rules, pull["head"]["sha"]),
        }
        if not incident:
            errors.append("ACTIVE_REPAIR_INCIDENT_UNRESOLVED")
        if len(ranked_pulls) > 1:
            # Exactly one repair is active; the rest stay visible but unpursued.
            active_repair["other_open_repairs"] = sorted(
                item[1] for item in ranked_pulls[1:]
            )

    # Track each still-open governed incident independently. An unrelated newer
    # PR must not hide an earlier merged-but-unproved repair.
    merged = sorted([pull for pull in bot_pulls if pull.get("merged_at")],
                    key=lambda pull: (str(pull["merged_at"]), int(pull["number"])),
                    reverse=True)
    open_incidents = {str(row["incident_id"]) for row in queue["records"]}
    seen_incidents: set[str] = set()
    for pull in merged:
        try:
            incident = exact_incident_id(str(pull.get("body") or ""))
        except ValueError:
            incident = None
        if incident in open_incidents and incident not in seen_incidents:
            seen_incidents.add(incident)
            merged_commit = pull.get("merge_commit_sha")
            if _is_sha(merged_commit):
                releases.append(
                    {
                        "issue_number": int(incident),
                        "pr_number": int(pull["number"]),
                        "merged_commit": merged_commit,
                        "state": "MERGED_PENDING_DEPLOY",
                    }
                )

    production, production_errors = _production_observation(production_url)
    errors.extend(production_errors)
    deployed, render_errors = _render_live_commit(render_service_id, render_api_key)
    errors.extend(render_errors)
    production["deployed_commit"] = deployed

    for release in releases:
        # Exact-commit verification is intentional. A different known live SHA
        # is a diagnostic mismatch, not an indefinitely pending deployment.
        if deployed:
            release["state"] = "DEPLOYED_PENDING_VERIFY"
            release["deployed_commit"] = deployed

    # One durable metric ledger keeps verified-repair counts across incidents.
    # Without it, metrics fall back to the active/release/selected issue.
    evidence_issue = (
        ledger_issue
        or (active_repair.get("issue_number") if active_repair else None)
        or (releases[0].get("issue_number") if releases else None)
        or (queue["records"][0].get("issue_number") if queue["records"] else None)
    )
    evidence = None
    if evidence_issue:
        try:
            issue_comments = gh_pages(
                f"/repos/{repo}/issues/{evidence_issue}/comments?per_page=100"
            )
        except CollectionError as exc:
            issue_comments = []
            errors.append(str(exc))
        evidence = parse_evidence_comment(issue_comments)
    return {
        "now_utc": now_utc,
        "queue": queue,
        "active_repair": active_repair,
        "releases_in_flight": releases,
        "production": production,
        "evidence": evidence,
        "collection_errors": errors,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load(path: str) -> Any:
    return json.loads(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WOW V17 single restoration loop")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="plan one cycle from an observation file")
    plan.add_argument("--observation", required=True)
    plan.add_argument("--format", choices=["json", "comment"], default="json")

    reconcile = sub.add_parser("reconcile", help="classify the writer reservation")
    reconcile.add_argument("--comments", required=True)
    reconcile.add_argument("--head-sha", default=None)

    collect = sub.add_parser("collect", help="collect a read-only observation")
    collect.add_argument("--repo", required=True)
    collect.add_argument("--manifest", required=True)
    collect.add_argument("--output", required=True)
    collect.add_argument("--production-url", default="")
    collect.add_argument("--render-service-id", default="")
    collect.add_argument("--render-api-key", default="")
    collect.add_argument("--ledger-issue", type=int, default=0)

    args = parser.parse_args(argv)
    if args.command == "reconcile":
        result = reconcile_writer_reservation(
            _load(args.comments), expected_head_sha=args.head_sha
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "collect":
        observation = collect_observation(
            repo=args.repo,
            manifest_path=args.manifest,
            now_utc=_iso(datetime.now(timezone.utc)),
            production_url=args.production_url or None,
            render_service_id=args.render_service_id or None,
            render_api_key=args.render_api_key or os.environ.get("RENDER_API_KEY") or None,
            ledger_issue=args.ledger_issue or None,
        )
        Path(args.output).write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n")
        print(json.dumps(observation["collection_errors"]))
        return 0

    decision = plan_cycle(_load(args.observation))
    if args.format == "comment":
        print(render_evidence_comment(decision))
    else:
        print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if not decision.get("hold") else 0


if __name__ == "__main__":
    raise SystemExit(main())
