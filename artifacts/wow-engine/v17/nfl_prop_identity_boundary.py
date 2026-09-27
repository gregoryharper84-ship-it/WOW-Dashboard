"""Row-isolated NFL display-id -> canonical-id boundary for Pick Request.

This wrapper is intentionally installed *inside* durable Pick Request state/run
control but outside hydration/scoring.  Therefore a display/provider identity
failure becomes a persisted pre-scorer terminal outcome at the existing INGESTED
stage, while resolved sibling rows continue through the canonical scorer.

No sporting probability is computed or changed here. Provider IDs remain aliases;
only provider-verified canonical NFL identity is forwarded to scoring.
"""
from __future__ import annotations

import inspect
from copy import deepcopy
from typing import Any, Optional

from fastapi import Header, HTTPException

import pick_request_runtime_core as pick_runtime
from v17.nfl_prop_boundary_integrity import _resolve_batch_nfl_identity
from v17.top10_model_reconciliation import enforce_top10_completion

_STATE_KEY = "wow_v17_nfl_prop_identity_inner_boundary_installed"
_SCHEDULE_KEY = "wow_v17_nfl_prop_identity_inner_boundary_scheduled"


def _row_key(row: Any, index: int) -> str:
    return str(getattr(row, "row_key", None) or f"row-{index + 1}")


def _normalized_row_keys(batch: pick_runtime.PickRequestBatch) -> pick_runtime.PickRequestBatch:
    rows = [
        row
        if getattr(row, "row_key", None)
        else row.model_copy(update={"row_key": f"row-{index + 1}"})
        for index, row in enumerate(batch.rows)
    ]
    return batch.model_copy(update={"rows": rows})


def _invoke(endpoint: Any, batch: pick_runtime.PickRequestBatch, model_identity: Optional[str]) -> Any:
    try:
        parameters = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "x_wow_model_identity" in parameters:
        return endpoint(batch, x_wow_model_identity=model_identity)
    return endpoint(batch)


def _blocked_identity_outcome(row: Any, receipt: dict[str, Any]) -> dict[str, Any]:
    blocker = str(receipt.get("code") or "PROP_EVENT_IDENTITY_UNRESOLVED")
    detail = receipt.get("detail") if isinstance(receipt.get("detail"), dict) else {}
    return pick_runtime._terminal(
        str(row.row_key),
        "HELD",
        "RUN_INVALID_ACQUISITION_INCOMPLETE",
        detail={
            "blocker": blocker,
            "failure_class": "ACQUISITION",
            "event_id": row.event_id,
            "event_start_time": row.event_start_time,
            "sport": str(row.sport).upper(),
            "player": row.player,
            "stat_type": pick_runtime._canonical_stat(row.sport, row.stat_type),
            "line": row.line,
            "direction": row.direction,
            "identity_resolution": deepcopy(detail),
            "model_evaluated": False,
            "specialist_scoring_attempted": False,
            "scoring_attempted": False,
            "specialist_invoked": False,
            "prediction_id": None,
            "source_snapshot_id": None,
        },
        acquisition={
            "mode": "NFL_EVENT_IDENTITY_RESOLUTION",
            "status": "FAILED",
            "provider": "ESPN",
            "blocker": blocker,
            "source_event_id_alias": receipt.get("source_event_id_alias") or row.event_id,
            "can_execute": False,
        },
    )


def _ordered_rows(
    batch: pick_runtime.PickRequestBatch,
    inner_rows: list[dict[str, Any]],
    blocked: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {
        str(item.get("row_key")): deepcopy(item)
        for item in inner_rows
        if isinstance(item, dict) and item.get("row_key")
    }
    by_key.update({key: deepcopy(value) for key, value in blocked.items()})
    return [by_key[key] for key in (_row_key(row, i) for i, row in enumerate(batch.rows)) if key in by_key]


def _merge_response(
    batch: pick_runtime.PickRequestBatch,
    inner: dict[str, Any] | None,
    blocked_full: dict[str, dict[str, Any]],
    identity_receipts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    response = dict(inner or {})
    inner_rows = response.get("rows") if isinstance(response.get("rows"), list) else []
    if batch.response_mode == "COMPACT":
        blocked_visible = {
            key: pick_runtime._compact_pick_outcome(outcome)
            for key, outcome in blocked_full.items()
        }
    else:
        blocked_visible = blocked_full

    rows = _ordered_rows(batch, inner_rows, blocked_visible)
    completed = sum(item.get("terminal_status") == "COMPLETED" for item in rows)
    held = sum(item.get("terminal_status") == "HELD" for item in rows)
    rejected = sum(item.get("terminal_status") == "REJECTED" for item in rows)
    rows_in = len(batch.rows)
    reconciliation_pass = rows_in == len(rows) == completed + held + rejected
    if not reconciliation_pass:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PICK_REQUEST_RECONCILIATION_FAILED",
                "failure_domain": "NFL_EVENT_IDENTITY_BOUNDARY",
                "rows_in": rows_in,
                "rows_accounted": len(rows),
                "rows_completed": completed,
                "rows_held": held,
                "rows_rejected": rejected,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    response.update(
        {
            "ok": completed > 0,
            "request_id": batch.request_id,
            "run_controller_status": (
                "COMPLETE" if completed == rows_in else ("DEGRADED" if completed else "BLOCKED")
            ),
            "rows_in": rows_in,
            "rows_completed": completed,
            "rows_held": held,
            "rows_rejected": rejected,
            "pick_rejected_count": sum(item.get("pick_rejected") is True for item in rows),
            "infrastructure_blocked_count": sum(item.get("infrastructure_blocked") is True for item in rows),
            "reconciliation_pass": True,
            "response_mode": batch.response_mode,
            "rows": rows,
            "probability_objective": "GOVERNED_MODEL_ONLY",
            "nfl_event_identity_resolution": {
                "rows": identity_receipts,
                "rows_blocked_before_scoring": len(blocked_full),
                "canonical_identity_rewrites": sum(
                    receipt.get("status") == "PASS"
                    and bool(receipt.get("canonical_event_id"))
                    and receipt.get("source_event_id_alias") != receipt.get("canonical_event_id")
                    for receipt in identity_receipts.values()
                ),
                "probability_mutated": False,
                "can_execute": False,
            },
            "can_execute": False,
        }
    )
    # Production durable state (installed outside this boundary) reconstructs
    # telemetry from full persisted outcomes. For direct/focused invocation,
    # preserve the captured scorer telemetry and explicitly add the pre-scorer
    # identity count without pretending those rows reached specialist scoring.
    telemetry = dict(response.get("telemetry") or {})
    telemetry["nfl_event_identity_blocked_before_scoring"] = len(blocked_full)
    response["telemetry"] = telemetry
    if batch.response_mode == "COMPACT":
        response.setdefault(
            "detail_retrieval",
            {
                "mode": "IMMUTABLE_RECEIPT_LOOKUP",
                "operation_id": "lookupWowV17PredictionReceipts",
            },
        )
    return enforce_top10_completion(response, list(batch.rows))


def install_nfl_prop_identity_boundary(app: Any, *, market_api: Any) -> bool:
    if getattr(app.state, _STATE_KEY, False):
        return True
    route = next(
        (
            candidate
            for candidate in app.router.routes
            if getattr(candidate, "path", None) == "/score-pick-request"
            and "POST" in (getattr(candidate, "methods", set()) or set())
        ),
        None,
    )
    if route is None or not callable(getattr(route, "endpoint", None)):
        return False

    captured = route.endpoint
    dependencies = list(getattr(route, "dependencies", None) or [])
    operation_id = getattr(route, "operation_id", None) or "scoreWowPickRequest"
    app.router.routes[:] = [candidate for candidate in app.router.routes if candidate is not route]

    @app.post(
        "/score-pick-request",
        dependencies=dependencies,
        operation_id=operation_id,
    )
    def score_pick_request_identity_bound(
        batch: pick_runtime.PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ) -> dict[str, Any]:
        normalized = _normalized_row_keys(batch)
        prepared, receipts = _resolve_batch_nfl_identity(normalized, market_api=market_api)

        blocked_full: dict[str, dict[str, Any]] = {}
        score_rows: list[Any] = []
        for index, row in enumerate(prepared.rows):
            key = _row_key(row, index)
            receipt = receipts.get(key)
            if isinstance(receipt, dict) and receipt.get("status") == "BLOCKED":
                blocked_full[key] = _blocked_identity_outcome(row, receipt)
            else:
                score_rows.append(row)

        inner: dict[str, Any] | None = None
        if score_rows:
            score_batch = prepared.model_copy(update={"rows": score_rows})
            result = _invoke(captured, score_batch, x_wow_model_identity)
            if not isinstance(result, dict):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "PICK_REQUEST_RESPONSE_INVALID",
                        "failure_domain": "NFL_EVENT_IDENTITY_BOUNDARY",
                        "can_execute": False,
                    },
                )
            inner = result

        return _merge_response(normalized, inner, blocked_full, receipts)

    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_nfl_prop_identity_boundary(app: Any, *, market_api: Any) -> None:
    if getattr(app.state, _SCHEDULE_KEY, False):
        return

    @app.on_event("startup")
    async def _install_nfl_prop_identity_boundary() -> None:
        install_nfl_prop_identity_boundary(app, market_api=market_api)

    setattr(app.state, _SCHEDULE_KEY, True)


__all__ = [
    "install_nfl_prop_identity_boundary",
    "schedule_nfl_prop_identity_boundary",
]
