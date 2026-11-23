"""Canonical Action → persistence provenance for WOW V17.

Scheduled workflows and Action HTTP responses can look successful while writing
elsewhere, writing only ephemeral artifacts, or never landing a durable row in
the sealed application database. This module compares the configured Supabase
destination against the sealed project ref and requires an independent readback
of each claimed receipt ID from that same destination.

A matching project ref and a found receipt never certify a model, never publish
probability, and never grant execution. ``can_execute`` stays false.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Sequence

from v17.production_release_admission import (
    APPLICATION_DATABASE_PROJECT_NAME,
    COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
    project_ref_from_supabase_url,
)

CAN_EXECUTE = False
ACTION_INVOCATION_TABLE = "wow_action_invocation_receipts"
PICK_REQUEST_RUN_TABLE = "wow_pick_request_runs"
SCOUT_HANDOFF_JOB_TABLE = "wow_scout_handoff_jobs"

PERSISTENCE_READBACK_MISSING = "PERSISTENCE_READBACK_MISSING"
PERSISTENCE_DESTINATION_MISMATCH = "PERSISTENCE_DESTINATION_MISMATCH"
PERSISTENCE_DESTINATION_UNREADABLE = "PERSISTENCE_DESTINATION_UNREADABLE"
PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING = "PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING"
PERSISTENCE_READBACK_VERIFIED = "PERSISTENCE_READBACK_VERIFIED"
PERSISTENCE_CLIENT_UNAVAILABLE = "PERSISTENCE_CLIENT_UNAVAILABLE"


def require_persistence_readback() -> bool:
    """True when schedule/CLI callers must fail closed without sealed readback."""
    return str(os.environ.get("WOW_REQUIRE_PERSISTENCE_READBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def configured_supabase_url_from_env() -> str:
    return str(os.environ.get("SUPABASE_URL") or "").strip()


def sealed_supabase_client_from_env() -> Any | None:
    """Build a service-role client for the configured sealed destination only.

    Returns ``None`` when URL/key/client import are unavailable. Callers that
    require provenance must treat that as fail-closed configuration evidence.
    """
    url = configured_supabase_url_from_env()
    key = (
        str(os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or str(os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    )
    if not url or not key:
        return None
    try:
        from supabase import create_client
    except ImportError:
        return None
    return create_client(url, key)


def _clean_id(value: Any) -> str:
    return str(value or "").strip()


_DURABLE_RECEIPT_FIELDS = (
    "invocation_id",
    "persistence_run_id",
    "request_id",
    "run_id",
)


def claimed_receipt_ids_from_auto_advance(receipt: Mapping[str, Any] | None) -> list[str]:
    """Extract durable Action/run receipt IDs claimed by an auto-advance receipt.

    Local ordering labels such as synthetic ``batch_key`` values
    (``team_events:1``) are never durable persistence identity and must not be
    claimed. Prefer one durable ID per row from the response/outbound request.
    """
    if not isinstance(receipt, Mapping):
        return []
    found: list[str] = []
    for key in ("prop_receipts", "team_event_receipts"):
        rows = receipt.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            durable = ""
            for field in _DURABLE_RECEIPT_FIELDS:
                durable = _clean_id(row.get(field))
                if durable:
                    break
            if not durable:
                body = row.get("body")
                if isinstance(body, Mapping):
                    for field in _DURABLE_RECEIPT_FIELDS:
                        durable = _clean_id(body.get(field))
                        if durable:
                            break
            if durable:
                found.append(durable)
    # Preserve order while dropping duplicates.
    return list(dict.fromkeys(found))


def claimed_receipt_ids_from_async_summary(summary: Mapping[str, Any] | None) -> list[str]:
    """Extract durable Scout handoff / Action IDs from an async COMPLETE summary.

    Prefers job ``request_id`` values and the run ``source_run_id``. Workflow
    artifact names alone are never durable identity.
    """
    if not isinstance(summary, Mapping):
        return []
    found: list[str] = []
    source_run_id = _clean_id(summary.get("source_run_id"))
    if source_run_id:
        found.append(source_run_id)
    jobs = summary.get("jobs")
    if isinstance(jobs, list):
        for job in jobs:
            if not isinstance(job, Mapping):
                continue
            for field in ("request_id", "invocation_id", "persistence_run_id", "run_id"):
                durable = _clean_id(job.get(field))
                if durable:
                    found.append(durable)
                    break
            payload = job.get("request_payload")
            if isinstance(payload, Mapping):
                for field in ("request_id", "invocation_id", "persistence_run_id", "run_id"):
                    durable = _clean_id(payload.get(field))
                    if durable:
                        found.append(durable)
                        break
    return list(dict.fromkeys(found))


def verify_action_persistence_provenance(
    *,
    configured_supabase_url: str,
    claimed_receipt_ids: Sequence[Any],
    readback_by_id: Callable[[str], Mapping[str, Any] | None],
    sealed_project_ref: str = COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
    require_claimed_ids: bool = True,
) -> dict[str, Any]:
    """Require durable readback from the sealed configured destination.

    ``readback_by_id`` must query the same store identified by
    ``configured_supabase_url``. Callers must not treat an HTTP Action response
    or a workflow artifact as proof of persistence.
    """
    observed = project_ref_from_supabase_url(configured_supabase_url)
    sealed = str(sealed_project_ref or "").strip()
    blockers: list[str] = []

    if not observed:
        blockers.append(PERSISTENCE_DESTINATION_UNREADABLE)
    elif observed != sealed:
        blockers.append(PERSISTENCE_DESTINATION_MISMATCH)

    claimed = [_clean_id(value) for value in claimed_receipt_ids]
    claimed = [value for value in claimed if value]
    claimed = list(dict.fromkeys(claimed))

    if require_claimed_ids and not claimed:
        blockers.append(PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING)

    missing: list[str] = []
    verified: list[str] = []
    if PERSISTENCE_DESTINATION_MISMATCH not in blockers and PERSISTENCE_DESTINATION_UNREADABLE not in blockers:
        for receipt_id in claimed:
            row = readback_by_id(receipt_id)
            if not isinstance(row, Mapping) or not row:
                missing.append(receipt_id)
                continue
            verified.append(receipt_id)

    if missing:
        blockers.append(PERSISTENCE_READBACK_MISSING)

    blockers = list(dict.fromkeys(blockers))
    # Empty claims are only allowed when the caller explicitly opted out of
    # require_claimed_ids; that mode still verifies destination identity.
    passed = not blockers and (bool(verified) or not require_claimed_ids)
    return {
        "status": PERSISTENCE_READBACK_VERIFIED if passed else "BLOCKED_WITH_EXACT_REASON",
        "code": (
            PERSISTENCE_READBACK_VERIFIED
            if passed
            else (blockers[0] if blockers else PERSISTENCE_READBACK_MISSING)
        ),
        "passed": passed,
        "persistence_readback_pass": passed,
        "blockers": blockers,
        "missing_receipt_ids": missing,
        "verified_receipt_ids": verified,
        "claimed_receipt_ids": claimed,
        "observed_project_ref": observed,
        "sealed_project_ref": sealed if sealed else "",
        "application_database_project_name": APPLICATION_DATABASE_PROJECT_NAME,
        "action_invocation_table": ACTION_INVOCATION_TABLE,
        "pick_request_run_table": PICK_REQUEST_RUN_TABLE,
        "can_execute": False,
    }


def _first_row(db: Any, table: str, column: str, value: str, columns: str) -> dict[str, Any] | None:
    try:
        rows = (
            db.table(table)
            .select(columns)
            .eq(column, value)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return None
    return dict(rows[0]) if rows else None


def readback_action_invocation_row(db: Any, invocation_id: str) -> dict[str, Any] | None:
    """Independent primary-key readback of one Action invocation receipt."""
    value = _clean_id(invocation_id)
    if not value:
        return None
    return _first_row(
        db,
        ACTION_INVOCATION_TABLE,
        "invocation_id",
        value,
        "invocation_id,route,occurred_at,http_status,caller_class,request_id",
    )


def readback_durable_receipt_row(db: Any, receipt_id: str) -> dict[str, Any] | None:
    """Independent sealed-destination readback for one claimed durable ID.

    Accepts Action invocation primary keys, Action ``request_id`` correlation,
    pick-request run identity, or Scout handoff job/run identity. First match
    wins; absence remains fail-closed for the caller.
    """
    value = _clean_id(receipt_id)
    if not value:
        return None
    for table, column, columns in (
        (
            ACTION_INVOCATION_TABLE,
            "invocation_id",
            "invocation_id,route,occurred_at,http_status,caller_class,request_id",
        ),
        (
            ACTION_INVOCATION_TABLE,
            "request_id",
            "invocation_id,route,occurred_at,http_status,caller_class,request_id",
        ),
        (
            PICK_REQUEST_RUN_TABLE,
            "request_id",
            "run_id,request_id,created_at,run_status",
        ),
        (
            SCOUT_HANDOFF_JOB_TABLE,
            "request_id",
            "job_id,source_run_id,request_id,target_route,current_state,terminal",
        ),
        (
            SCOUT_HANDOFF_JOB_TABLE,
            "source_run_id",
            "job_id,source_run_id,request_id,target_route,current_state,terminal",
        ),
    ):
        row = _first_row(db, table, column, value, columns)
        if row:
            return row
    return None


def persistence_readback_by_id_from_env() -> Callable[[str], Mapping[str, Any] | None] | None:
    """Return a sealed-destination readback callable, or ``None`` if unavailable."""
    client = sealed_supabase_client_from_env()
    if client is None:
        return None

    def _readback(receipt_id: str) -> Mapping[str, Any] | None:
        return readback_durable_receipt_row(client, receipt_id)

    return _readback
