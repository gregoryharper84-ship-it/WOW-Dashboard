"""Bounded row-level concurrency for COMPACT V17 Pick Requests.

The canonical Pick Request scorer is row-isolated but historically iterated its
rows serially. Interactive ChatGPT requests therefore paid the full wall time of
multiple independent specialist rows. This wrapper keeps the canonical scorer
as the only row authority while running independent rows concurrently in
bounded single-row FULL calls, then reapplies the canonical cross-row portfolio
layer before compacting the response.

The wrapper never retries a completed row, never changes a sporting probability,
and never authorizes execution. FULL/debug requests keep the historical serial
path. ``can_execute=false`` is asserted at every merge boundary.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import logging
import os
from typing import Any, Optional

from fastapi import Header

import pick_request_runtime_core as pick_runtime
from pick_request_runtime_core import PickRequestBatch
from v17.interactive_pick_hydration import prehydrate_batch
from v17.top10_model_reconciliation import enforce_top10_completion

_LOG = logging.getLogger("wow.v17.interactive.parallel")
_STATE_KEY = "_wow_v17_interactive_parallel_installed"
_DEFAULT_SCORE_WORKERS = 2
_MAX_SCORE_WORKERS = 4


def _score_worker_count() -> int:
    raw = os.getenv("WOW_INTERACTIVE_PROP_SCORE_WORKERS", str(_DEFAULT_SCORE_WORKERS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_SCORE_WORKERS
    return max(1, min(value, _MAX_SCORE_WORKERS))


def _row_key(row: Any, index: int) -> str:
    return str(getattr(row, "row_key", None) or f"row-{index + 1}")


def _single_row_batch(batch: PickRequestBatch, row: Any) -> PickRequestBatch:
    # Preserve the caller request_id. Canonical research job identity also
    # contains row_key, so independent rows remain unique without inventing a
    # second external request identity.
    return batch.model_copy(update={"response_mode": "FULL", "rows": [row]})


def _unexpected_row_failure(row: Any, index: int, exc: Exception) -> dict[str, Any]:
    # A single internal row call that unexpectedly throws is a scorer/completion
    # failure, not MODEL_UNAVAILABLE. The canonical reducer maps
    # ROW_SCORING_UNAVAILABLE to MODEL_SCORER_FAILED while preserving fail-closed
    # publication semantics.
    return pick_runtime._terminal(
        _row_key(row, index),
        "HELD",
        "ROW_SCORING_UNAVAILABLE",
        detail={
            "error_type": type(exc).__name__,
            "specialist_scoring_attempted": True,
            "scoring_attempted": True,
            "specialist_invoked": True,
        },
        acquisition={
            "mode": "INTERACTIVE_PARALLEL_ROW",
            "status": "FAILED",
            "can_execute": False,
        },
    )


def _extract_single_outcome(row: Any, index: int, response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("can_execute") is not False:
        return _unexpected_row_failure(row, index, RuntimeError("INVALID_SINGLE_ROW_RESPONSE"))
    rows = response.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return _unexpected_row_failure(row, index, RuntimeError("INVALID_SINGLE_ROW_RECONCILIATION"))
    return deepcopy(rows[0])


def _merge_compact_response(
    batch: PickRequestBatch,
    outcomes: list[dict[str, Any]],
    *,
    workers: int,
) -> dict[str, Any]:
    scored_legs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, (row, outcome) in enumerate(zip(batch.rows, outcomes)):
        if outcome.get("terminal_status") != "COMPLETED":
            continue
        result = outcome.get("result")
        if not isinstance(result, dict):
            continue
        canonical_stat = pick_runtime._canonical_stat(row.sport, row.stat_type)
        scored_legs.append(
            (
                pick_runtime._portfolio_leg(_row_key(row, index), row, canonical_stat, result),
                outcome,
            )
        )

    # The single-row calls cannot observe cross-row dependence. Reapply the
    # exact canonical downstream portfolio layer across the complete caller
    # batch. This never mutates model_probability/calibration/lower bounds.
    pick_runtime._apply_portfolio_governance(batch.request_id, scored_legs)

    for outcome in outcomes:
        outcome["can_execute"] = False

    completed = sum(outcome.get("terminal_status") == "COMPLETED" for outcome in outcomes)
    held = sum(outcome.get("terminal_status") == "HELD" for outcome in outcomes)
    rejected = sum(outcome.get("terminal_status") == "REJECTED" for outcome in outcomes)
    rows_in = len(batch.rows)
    reconciliation_pass = rows_in == completed + held + rejected
    if completed == rows_in:
        controller = "COMPLETE"
    elif completed > 0:
        controller = "DEGRADED"
    else:
        controller = "BLOCKED"

    response = {
        "ok": completed > 0,
        "request_id": batch.request_id,
        "run_controller_status": controller,
        "rows_in": rows_in,
        "rows_completed": completed,
        "rows_held": held,
        "rows_rejected": rejected,
        "pick_rejected_count": sum(outcome.get("pick_rejected") is True for outcome in outcomes),
        "infrastructure_blocked_count": sum(
            outcome.get("infrastructure_blocked") is True for outcome in outcomes
        ),
        "reconciliation_pass": reconciliation_pass,
        "telemetry": pick_runtime._telemetry(outcomes),
        "specialist_utilization_summary": pick_runtime._specialist_utilization_summary(outcomes),
        "response_mode": "COMPACT",
        "rows": [pick_runtime._compact_pick_outcome(outcome) for outcome in outcomes],
        "detail_retrieval": {
            "mode": "IMMUTABLE_RECEIPT_LOOKUP",
            "operation_id": "lookupWowV17PredictionReceipts",
        },
        "interactive_parallelism": {
            "status": "APPLIED",
            "workers": workers,
            "rows": rows_in,
            "single_row_canonical_calls": rows_in,
            "sporting_probability_mutated": False,
            "can_execute": False,
        },
        "probability_objective": "GOVERNED_MODEL_ONLY",
        "can_execute": False,
    }
    return enforce_top10_completion(response, list(batch.rows))


def install_interactive_pick_parallel_wrapper(app: Any, *, market_api: Any) -> bool:
    if getattr(app.state, _STATE_KEY, False):
        return True

    captured_route = next(
        (
            route
            for route in app.router.routes
            if getattr(route, "path", None) == "/score-pick-request"
            and "POST" in (getattr(route, "methods", set()) or set())
        ),
        None,
    )
    if captured_route is None:
        return False

    captured_endpoint = captured_route.endpoint
    dependencies = list(getattr(captured_route, "dependencies", []) or [])
    operation_id = getattr(captured_route, "operation_id", None) or "scoreWowPickRequest"

    app.router.routes[:] = [route for route in app.router.routes if route is not captured_route]

    @app.post(
        "/score-pick-request",
        dependencies=dependencies,
        operation_id=operation_id,
    )
    def score_pick_request_parallel(
        batch: PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(
            default=None,
            alias="X-WOW-Model-Identity",
        ),
    ) -> dict[str, Any]:
        workers = _score_worker_count()
        if str(batch.response_mode or "FULL").upper() != "COMPACT" or len(batch.rows) <= 1 or workers <= 1:
            response = captured_endpoint(batch, x_wow_model_identity)
            if isinstance(response, dict):
                response["can_execute"] = False
            return response

        # Hydrate shared player/event evidence once before splitting MORE/LESS or
        # other independent directions into canonical single-row calls.
        prepared = prehydrate_batch(batch, market_api=market_api)
        results: dict[int, dict[str, Any]] = {}
        max_workers = min(workers, len(prepared.rows))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wow-v17-prop-score") as pool:
            pending = {
                pool.submit(
                    captured_endpoint,
                    _single_row_batch(prepared, row),
                    x_wow_model_identity,
                ): (index, row)
                for index, row in enumerate(prepared.rows)
            }
            for future in as_completed(pending):
                index, row = pending[future]
                try:
                    results[index] = _extract_single_outcome(row, index, future.result())
                except Exception as exc:
                    # Do not retry already-completed rows and risk duplicate
                    # immutable writes. Preserve the exact failed row as a typed
                    # scorer failure instead.
                    results[index] = _unexpected_row_failure(row, index, exc)

        outcomes = [
            results.get(index)
            or _unexpected_row_failure(row, index, RuntimeError("ROW_RESULT_MISSING"))
            for index, row in enumerate(prepared.rows)
        ]
        merged = _merge_compact_response(prepared, outcomes, workers=max_workers)
        _LOG.warning(
            "WOW_V17_INTERACTIVE_SCORING status=PARALLEL rows_in=%s workers=%s completed=%s held=%s rejected=%s reconciliation=%s can_execute=false",
            merged.get("rows_in"),
            max_workers,
            merged.get("rows_completed"),
            merged.get("rows_held"),
            merged.get("rows_rejected"),
            str(merged.get("reconciliation_pass") is True).lower(),
        )
        return merged

    setattr(score_pick_request_parallel, _STATE_KEY, True)
    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_interactive_pick_parallel_install(app: Any, *, market_api: Any) -> None:
    scheduled_key = f"{_STATE_KEY}_scheduled"
    if getattr(app.state, scheduled_key, False):
        return

    @app.on_event("startup")
    async def _install_interactive_pick_parallel() -> None:
        installed = install_interactive_pick_parallel_wrapper(app, market_api=market_api)
        _LOG.warning(
            "WOW_V17_INTERACTIVE_SCORING status=%s workers=%s can_execute=false",
            "INSTALLED" if installed else "NOT_INSTALLED",
            _score_worker_count(),
        )

    setattr(app.state, scheduled_key, True)


__all__ = [
    "install_interactive_pick_parallel_wrapper",
    "schedule_interactive_pick_parallel_install",
]
