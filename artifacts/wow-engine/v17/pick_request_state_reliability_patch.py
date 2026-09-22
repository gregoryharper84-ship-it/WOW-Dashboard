"""Reliability patch for durable V17 pick-request state.

This module changes orchestration bookkeeping only. It does not score a row,
change a probability, alter calibration, or enable execution.

Repairs two failure modes observed on 2026-09-22:
1. ``begin()`` seeded new INGESTED/PENDING rows but left the parent run's
   pending/unresolved counters at their previous values if the Action transport
   failed before ``finalize()``.
2. the linear lifecycle helper could advance a model-computed row through
   ``RECEIPT_PERSISTED`` and ``GOVERNANCE_AUDITED`` even when no prediction_id
   existed, creating a false receipt-persisted claim.
"""
from __future__ import annotations

from typing import Any

from v17 import pick_request_state_runtime as state

CAN_EXECUTE = False
_INSTALLED = False


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


def install_pick_request_state_reliability_patch() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    original_begin = state.PickRequestStateStore.begin
    original_advance = state.PickRequestStateStore.advance

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

    state.PickRequestStateStore.begin = begin_with_manifest_sync
    state.PickRequestStateStore.advance = advance_with_receipt_guard
    _INSTALLED = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "_manifest_counts",
    "_receipt_stage_allowed",
    "install_pick_request_state_reliability_patch",
]
