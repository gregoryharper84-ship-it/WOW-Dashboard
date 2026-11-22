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

from typing import Any, Callable, Mapping, Sequence

from v17.production_release_admission import (
    APPLICATION_DATABASE_PROJECT_NAME,
    COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
    project_ref_from_supabase_url,
)

CAN_EXECUTE = False
ACTION_INVOCATION_TABLE = "wow_action_invocation_receipts"
PICK_REQUEST_RUN_TABLE = "wow_pick_request_runs"

PERSISTENCE_READBACK_MISSING = "PERSISTENCE_READBACK_MISSING"
PERSISTENCE_DESTINATION_MISMATCH = "PERSISTENCE_DESTINATION_MISMATCH"
PERSISTENCE_DESTINATION_UNREADABLE = "PERSISTENCE_DESTINATION_UNREADABLE"
PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING = "PERSISTENCE_CLAIMED_RECEIPT_IDS_MISSING"
PERSISTENCE_READBACK_VERIFIED = "PERSISTENCE_READBACK_VERIFIED"


def _clean_id(value: Any) -> str:
    return str(value or "").strip()


def claimed_receipt_ids_from_auto_advance(receipt: Mapping[str, Any] | None) -> list[str]:
    """Extract non-secret Action/run receipt IDs claimed by an auto-advance receipt."""
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
            for field in (
                "invocation_id",
                "persistence_run_id",
                "request_id",
                "run_id",
                "batch_key",
            ):
                value = _clean_id(row.get(field))
                if value:
                    found.append(value)
                    break
            body = row.get("body")
            if isinstance(body, Mapping):
                for field in ("request_id", "run_id", "invocation_id"):
                    value = _clean_id(body.get(field))
                    if value:
                        found.append(value)
                        break
    # Preserve order while dropping duplicates.
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
    passed = not blockers and bool(verified)
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


def readback_action_invocation_row(db: Any, invocation_id: str) -> dict[str, Any] | None:
    """Independent primary-key readback of one Action invocation receipt."""
    value = _clean_id(invocation_id)
    if not value:
        return None
    try:
        rows = (
            db.table(ACTION_INVOCATION_TABLE)
            .select("invocation_id,route,occurred_at,http_status,caller_class,request_id")
            .eq("invocation_id", value)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return None
    return dict(rows[0]) if rows else None
