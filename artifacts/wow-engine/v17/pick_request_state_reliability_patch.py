"""Reliability patch for durable V17 pick-request state.

This module changes orchestration bookkeeping only. It does not score a row,
change a probability, alter calibration, or enable execution.

Repairs three failure modes:
1. ``begin()`` seeded new INGESTED/PENDING rows but left the parent run's
   pending/unresolved counters at their previous values if the Action transport
   failed before ``finalize()``.
2. the linear lifecycle helper could advance a model-computed row through
   ``RECEIPT_PERSISTED`` and ``GOVERNANCE_AUDITED`` even when no prediction_id
   existed, creating a false receipt-persisted claim.
3. an exact-board identity mismatch discovered after canonical scoring could
   make the whole batch fail reconciliation after other row receipts had already
   been persisted. The durable worker then stopped the whole run instead of
   isolating the mismatched row as a typed terminal hold.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from v17 import pick_request_state_runtime as state
from v17.exact_board_reconciliation import (
    EXACT_BOARD_IDENTITY_MISMATCH,
    enforce_exact_board_identity,
)

CAN_EXECUTE = False
_INSTALLED = False
IDENTITY_CONFLICT_TERMINAL = "PROP_EVENT_IDENTITY_CONFLICT"


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


def _identity_conflict_outcome(
    outcome: dict[str, Any], mismatch: dict[str, Any]
) -> dict[str, Any]:
    prediction_id = state._prediction_id(outcome)
    detail = {
        "blocker_code": EXACT_BOARD_IDENTITY_MISMATCH,
        "conflicting_fields": list(mismatch.get("fields") or []),
        "expected": deepcopy(mismatch.get("expected") or {}),
        "observed": deepcopy(mismatch.get("observed") or {}),
        "original_terminal_status": outcome.get("terminal_status"),
        "original_terminal_code": outcome.get("code"),
        "original_model_evaluated": outcome.get("model_evaluated") is True,
        "original_prediction_id": prediction_id,
        "scorer_receipt_preserved": bool(prediction_id),
        "specialist_scoring_attempted": outcome.get("model_evaluated") is True,
        "specialist_invoked": outcome.get("model_evaluated") is True,
        "can_execute": False,
    }
    isolated = {
        "row_key": outcome.get("row_key"),
        "terminal_status": "HELD",
        "code": IDENTITY_CONFLICT_TERMINAL,
        "model_evaluated": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "pick_rejected": False,
        "infrastructure_blocked": True,
        "detail": detail,
        "can_execute": False,
    }
    if prediction_id:
        isolated["prediction_id"] = prediction_id
    if outcome.get("source_snapshot_id"):
        isolated["source_snapshot_id"] = outcome.get("source_snapshot_id")
    return isolated


def _isolate_exact_identity_conflicts(
    source_rows: list[Any], outcomes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert post-score source/canonical mismatches into row-isolated holds.

    The fitted scorer receipt remains immutable and linked by prediction_id, but
    the mismatched probability package is removed from the publication/ranking
    response so it cannot influence portfolio governance. Unrelated rows are
    unchanged and remain eligible for exact-once continuation.
    """
    probe = enforce_exact_board_identity(
        {"rows": deepcopy(outcomes), "reconciliation_pass": True, "can_execute": False},
        source_rows,
    )
    audit = probe.get("exact_board_identity_reconciliation") or {}
    mismatches = [
        dict(item)
        for item in (audit.get("mismatches") or [])
        if isinstance(item, dict)
    ]
    if not mismatches:
        return outcomes, []

    by_key = {str(item.get("row_key") or ""): item for item in mismatches}
    isolated: list[dict[str, Any]] = []
    for outcome in outcomes:
        mismatch = by_key.get(str(outcome.get("row_key") or ""))
        if mismatch is None:
            isolated.append(outcome)
            continue
        isolated.append(_identity_conflict_outcome(outcome, mismatch))
    return isolated, mismatches


def _durable_status_without_false_publication(record: dict[str, Any]) -> str:
    seq = int(record.get("stage_seq") or 0)
    if record.get("model_evaluated") is True and seq < state.STAGE_SEQ["RECEIPT_PERSISTED"]:
        return "MODEL_COMPUTED_RESEARCH_ONLY"
    if (
        record.get("model_evaluated") is True
        and seq >= state.STAGE_SEQ["RECEIPT_PERSISTED"]
        and record.get("probability_publishable") is not True
    ):
        return "SPORTING_MODEL_COMPLETE_PUBLICATION_PENDING"
    if (
        seq >= state.STAGE_SEQ["PUBLICATION_AUTHORIZED"]
        and record.get("terminal_status") == "COMPLETED"
        and record.get("probability_publishable") is True
    ):
        return "GOVERNED_PUBLICATION_AUTHORIZED"
    status = str(record.get("terminal_status") or "PENDING")
    code = str(record.get("terminal_code") or "")
    return f"{status}:{code}" if code else status


def install_pick_request_state_reliability_patch() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    original_begin = state.PickRequestStateStore.begin
    original_advance = state.PickRequestStateStore.advance
    original_merged_response = state._merged_response

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

    def merged_response_with_identity_isolation(ctx: Any, inner: Any):
        source_rows = list(ctx.batch.rows)
        outcomes = state._current_full_outcomes(ctx, inner)
        isolated, mismatches = _isolate_exact_identity_conflicts(source_rows, outcomes)
        if mismatches:
            by_key = {str(item.get("row_key") or ""): item for item in isolated}
            for row_key, record in ctx.rows.items():
                replacement = by_key.get(str(row_key))
                if replacement is None or replacement.get("code") != IDENTITY_CONFLICT_TERMINAL:
                    continue
                prediction_id = replacement.get("prediction_id") or record.get("prediction_id")
                record["outcome"] = deepcopy(replacement)
                record["terminal_status"] = "HELD"
                record["terminal_code"] = IDENTITY_CONFLICT_TERMINAL
                record["model_evaluated"] = False
                record["probability_publishable"] = False
                record["rank_eligible"] = False
                if prediction_id:
                    record["prediction_id"] = prediction_id
                    # The immutable scorer receipt exists, and the identity
                    # rejection itself is a governance decision. Publication is
                    # not authorized even if an earlier hook advanced too far.
                    record["current_stage"] = "GOVERNANCE_AUDITED"
                    record["stage_seq"] = state.STAGE_SEQ["GOVERNANCE_AUDITED"]
            adjusted_inner = dict(inner or {})
            adjusted_inner["rows"] = isolated
            response, final_outcomes = original_merged_response(ctx, adjusted_inner)
            response["row_identity_isolation"] = {
                "status": "APPLIED",
                "terminal_code": IDENTITY_CONFLICT_TERMINAL,
                "row_keys": sorted(str(item.get("row_key") or "") for item in mismatches),
                "mismatch_count": len(mismatches),
                "scorer_receipts_preserved": True,
                "can_execute": False,
            }
            response["can_execute"] = False
            return response, final_outcomes
        return original_merged_response(ctx, inner)

    state.PickRequestStateStore.begin = begin_with_manifest_sync
    state.PickRequestStateStore.advance = advance_with_receipt_guard
    state._durable_status = _durable_status_without_false_publication
    state._merged_response = merged_response_with_identity_isolation
    _INSTALLED = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "IDENTITY_CONFLICT_TERMINAL",
    "_durable_status_without_false_publication",
    "_identity_conflict_outcome",
    "_isolate_exact_identity_conflicts",
    "_manifest_counts",
    "_receipt_stage_allowed",
    "install_pick_request_state_reliability_patch",
]
