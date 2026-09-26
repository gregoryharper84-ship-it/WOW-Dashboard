"""Correctness hardening for V17 resumable pick-request run control.

Kept separate from the base run-control module so the scorer and probability
pipeline remain untouched. This patch is installed before run-control routes are
mounted and tightens exact-once recovery in two places:

1. a durably closed request_id rejects manifest reseeding before any row mutation;
2. immutable receipts recovered after a lost response are removed from the
   retry set, while exact durable PENDING rows with the same request_id + row_key
   resume contract remain scorer-eligible exactly once.

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
_RECEIPT_GAP_HOLD_CODE = "RUN_RECEIPT_MATCHED_OUTCOME_RECOVERED_HOLD"


def _refresh_run_manifest_counts(db: Any, request_id: str) -> None:
    """Make run-level counters reflect the persisted row ledger immediately.

    Manifest seeding happens before scorer completion, so relying on finalization
    leaves pending/unresolved counters at their database defaults during a live
    durable run. The row ledger is authoritative; this helper only reconciles the
    aggregate run receipt to that already-persisted state.
    """
    run_id = state._run_id(request_id)
    run = control._run_row(db, run_id)
    if not run:
        return

    rows = state.PickRequestStateStore(db)._load_all(run_id)
    completed = sum(item.get("terminal_status") == "COMPLETED" for item in rows)
    held = sum(item.get("terminal_status") == "HELD" for item in rows)
    rejected = sum(item.get("terminal_status") == "REJECTED" for item in rows)
    pending = sum(item.get("terminal_status") == "PENDING" for item in rows)
    unresolved = sum(
        item.get("terminal_status") == "PENDING"
        or (
            item.get("model_evaluated") is True
            and int(item.get("stage_seq") or 0)
            < state.STAGE_SEQ["RECEIPT_PERSISTED"]
        )
        for item in rows
    )

    refreshed = dict(run)
    if not control._is_closed(run):
        if pending:
            run_status = "RUNNING"
        elif completed == len(rows) and rows:
            run_status = "COMPLETE"
        elif completed:
            run_status = "DEGRADED"
        elif rows:
            run_status = "BLOCKED"
        else:
            run_status = str(run.get("run_status") or "RUNNING")
        refreshed["run_status"] = run_status

    refreshed.update(
        {
            "total_rows": len(rows),
            "completed_rows": completed,
            "held_rows": held,
            "rejected_rows": rejected,
            "pending_rows": pending,
            "unresolved_rows": unresolved,
            "updated_at": state._now(),
            "can_execute": False,
        }
    )
    db.table(state.RUN_TABLE).upsert(refreshed, on_conflict="run_id").execute()


def _closed_safe_seed_manifest(db: Any, request: control.ResumablePickRunRequest) -> None:
    run_id = state._run_id(request.request_id)
    existing_run = control._run_row(db, run_id)
    if control._is_closed(existing_run):
        raise control._closed_error(existing_run or {}, request.request_id)
    _ORIGINAL_SEED_MANIFEST(db, request)
    _refresh_run_manifest_counts(db, request.request_id)


def _recover_missing_outcome_as_hold(
    db: Any,
    request_id: str,
    record: dict[str, Any],
    match: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile an immutable receipt without fabricating missing governance.

    A persisted immutable pregame prediction proves the canonical scorer already
    created a prediction, so re-scoring would violate exact-once safety. If the
    companion row outcome was lost, preserve that receipt identity but fail the
    row closed as a non-publishable/non-rankable orchestration HOLD. The original
    immutable probability remains available through the prediction ledger.
    """
    key = str(record.get("row_key") or "")
    prediction_id = match.get("governed_prediction_id")
    prediction = match.get("prediction") if isinstance(match.get("prediction"), dict) else {}
    source_snapshot_id = prediction.get("source_snapshot_id")

    recovered_outcome = {
        "row_key": key,
        "terminal_status": "HELD",
        "code": _RECEIPT_GAP_HOLD_CODE,
        "model_evaluated": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "prediction_id": prediction_id,
        "source_snapshot_id": source_snapshot_id,
        "infrastructure_blocked": True,
        "detail": {
            "failure_domain": "RECEIPT_SERVICE_UNREACHABLE",
            "recovery_reason": "IMMUTABLE_PREGAME_RECEIPT_PRESENT_DURABLE_OUTCOME_MISSING",
            "request_id": request_id,
            "row_key": key,
        },
        "can_execute": False,
    }

    migrated = dict(record)
    migrated.update(
        {
            "prediction_id": prediction_id,
            "terminal_status": "HELD",
            "terminal_code": _RECEIPT_GAP_HOLD_CODE,
            "failure_domain": "RECEIPT_SERVICE_UNREACHABLE",
            "model_evaluated": True,
            "probability_publishable": False,
            "rank_eligible": False,
            "source_snapshot_id": source_snapshot_id or record.get("source_snapshot_id"),
            "outcome": recovered_outcome,
            "current_stage": "RECEIPT_PERSISTED",
            "stage_seq": state.STAGE_SEQ["RECEIPT_PERSISTED"],
            "updated_at": state._now(),
            "can_execute": False,
        }
    )
    migrated["durable_status"] = state._durable_status(migrated)
    db.table(state.ROW_TABLE).upsert(
        migrated, on_conflict="run_id,row_key"
    ).execute()
    return migrated


def _receipt_preflight_without_rescore(
    db: Any,
    request_id: str,
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    """Return only rows proven safe for one scorer invocation.

    NOT_FOUND rows and exact durable PENDING rows carrying the same request_id,
    row_key, and explicit resume contract are scorer-eligible. MATCHED immutable
    receipts are reconciled into durable state and deliberately omitted from the
    returned retry list. Ambiguous, non-immutable, identity-mismatched states
    fail closed. A matched immutable receipt whose companion row outcome was lost
    is recovered conservatively as a non-rankable HOLD rather than re-scored or
    allowed to stop unrelated board rows.
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

            if status == "NOT_FOUND" or control._durable_pending_resume_is_safe(
                outcome,
                request_id=request_id,
                row_key=key,
            ):
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
                _recover_missing_outcome_as_hold(
                    db,
                    request_id,
                    record,
                    match,
                )
                recovered += 1
                continue

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

            # Mirror the normal state finalizer from already-persisted outcome
            # fields. This never creates model authority; it only finishes the
            # durable lifecycle for work the canonical scorer already produced.
            stage = "RECEIPT_PERSISTED"
            if durable_outcome.get("model_evaluated") is True:
                stage = "GOVERNANCE_AUDITED"
                migrated["model_evaluated"] = True
                migrated["probability_publishable"] = (
                    durable_outcome.get("probability_publishable") is True
                )
                migrated["rank_eligible"] = durable_outcome.get("rank_eligible") is True
                if durable_outcome.get("probability_publishable") is True:
                    stage = "PUBLICATION_AUTHORIZED"
            migrated["current_stage"] = stage
            migrated["stage_seq"] = state.STAGE_SEQ[stage]
            migrated["durable_status"] = state._durable_status(migrated)
            migrated["updated_at"] = state._now()
            migrated["can_execute"] = False
            db.table(state.ROW_TABLE).upsert(
                migrated, on_conflict="run_id,row_key"
            ).execute()
            recovered += 1
            # Critical: do NOT append the recovered row to retryable.

    if recovered:
        _refresh_run_manifest_counts(db, request_id)
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
