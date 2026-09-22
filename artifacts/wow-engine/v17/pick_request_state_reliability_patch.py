"""Reliability patch for durable V17 pick-request state.

This module changes orchestration bookkeeping only. It does not score a row,
change a probability, alter calibration, or enable execution.

Repairs three failure modes observed on 2026-09-22:
1. ``begin()`` seeded new INGESTED/PENDING rows but left the parent run's
   pending/unresolved counters at their previous values if the Action transport
   failed before ``finalize()``.
2. the linear lifecycle helper could advance a model-computed row through
   ``RECEIPT_PERSISTED`` and ``GOVERNANCE_AUDITED`` even when no prediction_id
   existed, creating a false receipt-persisted claim.
3. exact durable PENDING recovery is now visible from receipt lookup, but the
   resumable runner's older receipt preflight treated that typed recovery state
   as a blocker instead of the explicitly safe same-request/same-row resume it
   represents.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from v17 import pick_request_state_runtime as state

CAN_EXECUTE = False
_INSTALLED = False

_DURABLE_PENDING_CODE = "DURABLE_ROW_PENDING_SAFE_TO_RESUME"
_DURABLE_PENDING_CONTRACT = "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY"


def _manifest_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
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
    return {
        "total_rows": len(rows),
        "completed_rows": completed,
        "held_rows": held,
        "rejected_rows": rejected,
        "pending_rows": pending,
        "unresolved_rows": unresolved,
    }


def _receipt_stage_allowed(record: dict[str, Any], stage: str) -> bool:
    target = state.STAGE_SEQ.get(stage)
    if target is None:
        return True
    if target < state.STAGE_SEQ["RECEIPT_PERSISTED"]:
        return True
    return bool(record.get("prediction_id"))


def _run_control_receipt_result(result: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    """Translate only the exact durable-pending resume contract for run control.

    The public receipt lookup remains unchanged and continues to return
    ``UNRESOLVED / DURABLE_ROW_PENDING_SAFE_TO_RESUME``. The resumable runner's
    historical preflight already treats ``NOT_FOUND`` as safe to score and all
    other non-matched states as blockers. For this internal call only, the exact
    same-request/same-row resume contract is therefore equivalent to "no
    immutable receipt exists yet; proceed exactly once with this durable row".
    """
    patched = deepcopy(result)
    rows: list[dict[str, Any]] = []
    for raw in patched.get("rows") or []:
        outcome = dict(raw)
        resume = outcome.get("resume") if isinstance(outcome.get("resume"), dict) else {}
        row_key = str(outcome.get("row_key") or "")
        safe_pending = (
            outcome.get("status") == "UNRESOLVED"
            and outcome.get("code") == _DURABLE_PENDING_CODE
            and outcome.get("retry_allowed") is True
            and str(resume.get("request_id") or "") == str(request_id)
            and str(resume.get("row_key") or "") == row_key
            and resume.get("contract") == _DURABLE_PENDING_CONTRACT
        )
        if safe_pending:
            outcome["status"] = "NOT_FOUND"
            outcome["run_control_resume_from_durable_pending"] = True
        rows.append(outcome)
    patched["rows"] = rows
    return patched


def install_pick_request_state_reliability_patch() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    original_begin = state.PickRequestStateStore.begin
    original_advance = state.PickRequestStateStore.advance

    # Imported lazily because v17_observability imports the run-control module
    # before installing this patch. Patching the run-control module's local
    # lookup reference preserves the public receipt-lookup contract unchanged.
    from v17 import pick_request_run_control as run_control

    original_run_control_lookup = run_control.lookup_prediction_receipts

    def begin_with_manifest_sync(self: Any, batch: Any) -> Any:
        ctx = original_begin(self, batch)
        rows = self._load_all(ctx.run_id)
        counts = _manifest_counts(rows)
        state._exec(
            self.db.table(state.RUN_TABLE).upsert(
                {
                    "run_id": ctx.run_id,
                    "request_id": ctx.request_id,
                    "source_kind": "PROP_BOARD",
                    "run_status": "RUNNING",
                    **counts,
                    "resumed_rows": len(ctx.resumed_rows),
                    "can_execute": False,
                    "updated_at": state._now(),
                },
                on_conflict="run_id",
            ),
            operation="SYNC_RUN_MANIFEST_AFTER_BEGIN",
        )
        return ctx

    def advance_with_receipt_guard(
        self: Any,
        ctx: Any,
        row_key: str,
        stage: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        record = ctx.rows.get(row_key) if hasattr(ctx, "rows") else None
        if isinstance(record, dict) and not _receipt_stage_allowed(record, stage):
            # A later audit/publication stage cannot imply receipt persistence.
            # Stay at the highest actually proven stage until prediction_id is
            # present. The caller may still persist the terminal outcome itself.
            return
        original_advance(self, ctx, row_key, stage, metadata=metadata)

    def run_control_lookup_with_durable_pending_resume(db: Any, batch: Any) -> dict[str, Any]:
        result = original_run_control_lookup(db, batch)
        return _run_control_receipt_result(
            result,
            request_id=str(getattr(batch, "request_id", "") or ""),
        )

    state.PickRequestStateStore.begin = begin_with_manifest_sync
    state.PickRequestStateStore.advance = advance_with_receipt_guard
    run_control.lookup_prediction_receipts = run_control_lookup_with_durable_pending_resume
    _INSTALLED = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "_manifest_counts",
    "_receipt_stage_allowed",
    "_run_control_receipt_result",
    "install_pick_request_state_reliability_patch",
]
