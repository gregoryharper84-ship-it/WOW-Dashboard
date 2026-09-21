"""Correctness hardening for V17 resumable pick-request run control.

Kept separate from the base run-control module so the scorer and probability
pipeline remain untouched. This patch is installed before run-control routes are
mounted and tightens exact-once recovery in two places:

1. a durably closed request_id rejects manifest reseeding before any row mutation;
2. an immutable receipt recovered after a lost response is removed from the
   retry set, so the canonical scorer is never invoked for that row again.

can_execute remains false unconditionally.
"""
from __future__ import annotations

from typing import Any

from v17 import pick_request_run_control as control
from v17 import pick_request_state_runtime as state
from v17.prediction_receipt_lookup_runtime import (
    PredictionReceiptLookupBatch,
    PredictionReceiptLookupRow,
    lookup_prediction_receipts,
)

CAN_EXECUTE = False
_INSTALLED = False
_ORIGINAL_SEED_MANIFEST = control._seed_manifest


def _closed_safe_seed_manifest(db: Any, request: control.ResumablePickRunRequest) -> None:
    run_id = state._run_id(request.request_id)
    existing_run = control._run_row(db, run_id)
    if control._is_closed(existing_run):
        raise control._closed_error(existing_run or {}, request.request_id)
    _ORIGINAL_SEED_MANIFEST(db, request)


def _receipt_preflight_without_rescore(
    db: Any,
    request_id: str,
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    """Return only rows proven NOT_FOUND as scorer-eligible.

    MATCHED immutable receipts are reconciled into durable state and deliberately
    omitted from the returned retry list. Ambiguous, non-immutable, or
    ledger/outcome-inconsistent states fail closed.
    """
    if not records:
        return [], 0, None

    retryable: list[dict[str, Any]] = []
    recovered = 0
    by_key = {str(item.get("row_key")): item for item in records}

    for start in range(0, len(records), 50):
        chunk = records[start : start + 50]
        lookup_rows = [
            PredictionReceiptLookupRow(
                row_key=str(item.get("row_key")),
                event_id=str(item.get("event_id")),
                sport=str(item.get("sport")),
                player=str(item.get("player")),
                stat_type=str(item.get("stat_type")),
                line=float(item.get("exact_line")),
                direction=str(item.get("direction")),
            )
            for item in chunk
        ]
        result = lookup_prediction_receipts(
            db,
            PredictionReceiptLookupBatch(request_id=request_id, rows=lookup_rows),
        )

        for outcome in result.get("rows") or []:
            key = str(outcome.get("row_key"))
            record = by_key[key]
            status = str(outcome.get("status") or "")

            if status == "NOT_FOUND":
                retryable.append(record)
                continue

            if status != "MATCHED" or int(outcome.get("match_count") or 0) != 1:
                return retryable, recovered, {
                    "code": outcome.get("code") or "RUN_RECEIPT_PREFLIGHT_BLOCKED",
                    "row_key": key,
                    "receipt_status": status,
                    "can_execute": False,
                }

            match = (outcome.get("matches") or [None])[0]
            if not isinstance(match, dict) or match.get("is_immutable_pregame") is not True:
                return retryable, recovered, {
                    "code": "RUN_RECEIPT_NOT_PROVEN_IMMUTABLE_PREGAME",
                    "row_key": key,
                    "can_execute": False,
                }

            durable_outcome = record.get("outcome")
            if not isinstance(durable_outcome, dict):
                return retryable, recovered, {
                    "code": "RUN_RECEIPT_MATCHED_OUTCOME_UNAVAILABLE",
                    "row_key": key,
                    "can_execute": False,
                }

            terminal_status = str(record.get("terminal_status") or "PENDING")
            if terminal_status == "PENDING":
                terminal_status = str(durable_outcome.get("terminal_status") or "PENDING")
            if terminal_status == "PENDING":
                return retryable, recovered, {
                    "code": "RUN_RECEIPT_MATCHED_TERMINAL_STATUS_UNRESOLVED",
                    "row_key": key,
                    "can_execute": False,
                }

            migrated = dict(record)
            migrated["prediction_id"] = match.get("governed_prediction_id")
            migrated["terminal_status"] = terminal_status
            migrated["current_stage"] = "RECEIPT_PERSISTED"
            migrated["stage_seq"] = state.STAGE_SEQ["RECEIPT_PERSISTED"]
            migrated["durable_status"] = state._durable_status(migrated)
            migrated["updated_at"] = state._now()
            migrated["can_execute"] = False
            db.table(state.ROW_TABLE).upsert(
                migrated, on_conflict="run_id,row_key"
            ).execute()
            recovered += 1
            # Critical: do NOT append the recovered row to retryable.

    return retryable, recovered, None


def install_pick_request_run_control_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    control._seed_manifest = _closed_safe_seed_manifest
    control._receipt_preflight = _receipt_preflight_without_rescore
    _INSTALLED = True


__all__ = [
    "CAN_EXECUTE",
    "install_pick_request_run_control_hardening",
]
