"""Bounded pre-hydration wrapper for interactive V17 prop batches.

The existing /score-pick-request handler remains sole owner of validation,
immutable persistence, fitted scoring, calibration, reconciliation, portfolio
governance, and terminal reduction.  This wrapper moves only successful external
raw-evidence acquisition ahead of that handler and runs independent acquisitions
with a small bounded thread pool.

If pre-hydration cannot prove route eligibility or any acquisition fails, the
row is passed to the captured canonical handler unchanged.  Therefore failure
codes and fail-closed semantics remain owned by the canonical handler.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any, Optional

from fastapi import Header

from mlb_1ip_specialist import CANONICAL_STAT_TYPE as MLB_1IP_STAT_TYPE
from pick_request_runtime_core import PickRequestBatch, PickRequestRow, RawPropEvidence, _canonical_stat
from prop_auto_hydration import auto_hydrate_prop_evidence

LOGGER = logging.getLogger("wow.v17.interactive_latency")
_STATE_KEY = "wow_interactive_pick_hydration_installed"
DEFAULT_WORKERS = 4
MAX_WORKERS = 8


def _worker_count() -> int:
    try:
        value = int(os.getenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", str(DEFAULT_WORKERS)))
    except (TypeError, ValueError):
        value = DEFAULT_WORKERS
    return max(1, min(MAX_WORKERS, value))


def _route_is_prehydration_eligible(row: PickRequestRow, market_api: Any) -> bool:
    """Mirror canonical route preflight without creating new capability."""
    if row.evidence is not None:
        return False
    sport = str(row.sport or "").strip().upper()
    canonical_stat = _canonical_stat(sport, row.stat_type)
    if canonical_stat == MLB_1IP_STAT_TYPE:
        # 1IP owns a dedicated ingress/research/final-refresh contract.
        return False
    try:
        specialist = market_api.prod.base_api._controlling_specialist_provider(sport, canonical_stat)
        if not isinstance(specialist, dict):
            return False
        if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
            return False
        lane = market_api.prod._runtime_capability(market_api.prod.PROP_CAPABILITY_KEY)
        if not isinstance(lane, dict) or lane.get("capability_status") != "AVAILABLE":
            return False
        route = market_api._prop_route_artifact(sport, canonical_stat)
        return bool(
            isinstance(route, dict)
            and route.get("ok") is True
            and route.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_READY"
        )
    except Exception:
        # Pre-hydration is an optimization only. Any uncertainty delegates the
        # untouched row to the canonical handler and preserves its typed result.
        return False


def _hydrate(row: PickRequestRow) -> RawPropEvidence:
    raw = auto_hydrate_prop_evidence(
        sport=str(row.sport or "").strip().upper(),
        player=row.player,
        stat_type=_canonical_stat(row.sport, row.stat_type),
        event_start_time=row.event_start_time,
        source_capture_timestamp=row.source_capture_timestamp,
        source_label=f"{row.source_type}:{row.platform or 'UNKNOWN'}",
    )
    return RawPropEvidence.model_validate(raw)


def prehydrate_batch(batch: PickRequestBatch, *, market_api: Any) -> PickRequestBatch:
    """Return a same-order batch with only successful eligible evidence injected."""
    workers = _worker_count()
    eligible = [
        (index, row)
        for index, row in enumerate(batch.rows)
        if _route_is_prehydration_eligible(row, market_api)
    ]
    if workers <= 1 or len(eligible) <= 1:
        return batch

    started = perf_counter()
    evidence_by_index: dict[int, RawPropEvidence] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(eligible)), thread_name_prefix="wow-prop-hydrate") as pool:
        future_to_index = {pool.submit(_hydrate, row): index for index, row in eligible}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                evidence_by_index[index] = future.result()
            except Exception:
                # Preserve canonical failure typing by leaving the row untouched;
                # the captured handler will perform its normal acquisition path.
                continue

    if evidence_by_index:
        rows = [
            row.model_copy(update={"evidence": evidence_by_index[index]})
            if index in evidence_by_index
            else row
            for index, row in enumerate(batch.rows)
        ]
        hydrated = batch.model_copy(update={"rows": rows})
    else:
        hydrated = batch

    LOGGER.warning(
        "WOW_V17_INTERACTIVE_STAGE route=/score-pick-request stage=prehydrate "
        "rows_in=%s eligible=%s prefetched=%s workers=%s stage_ms=%.3f can_execute=false",
        len(batch.rows),
        len(eligible),
        len(evidence_by_index),
        min(workers, len(eligible)),
        (perf_counter() - started) * 1000.0,
    )
    return hydrated


def install_interactive_pick_hydration_wrapper(app: Any, *, market_api: Any) -> bool:
    """Replace the canonical route with a pre-hydrating delegating wrapper.

    The captured endpoint remains the canonical scorer.  Existing auth
    dependencies and the operation id are copied from the route being wrapped.
    """
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
    if captured_route is None or not callable(getattr(captured_route, "endpoint", None)):
        return False

    captured_endpoint = captured_route.endpoint
    dependencies = list(getattr(captured_route, "dependencies", None) or [])
    operation_id = str(getattr(captured_route, "operation_id", None) or "scoreWowPickRequest")

    app.router.routes[:] = [route for route in app.router.routes if route is not captured_route]

    def score_pick_request_prehydrated(
        batch: PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        prepared = prehydrate_batch(batch, market_api=market_api)
        return captured_endpoint(prepared, x_wow_model_identity)

    app.post(
        "/score-pick-request",
        dependencies=dependencies,
        operation_id=operation_id,
    )(score_pick_request_prehydrated)
    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_interactive_pick_hydration_install(app: Any, *, market_api: Any) -> None:
    """Install at startup after all production routes have been composed."""
    if getattr(app.state, f"{_STATE_KEY}_scheduled", False):
        return

    @app.on_event("startup")
    async def _install_interactive_pick_hydration() -> None:
        installed = install_interactive_pick_hydration_wrapper(app, market_api=market_api)
        LOGGER.warning(
            "WOW_V17_INTERACTIVE_HYDRATION status=%s workers=%s can_execute=false",
            "INSTALLED" if installed else "NOT_INSTALLED_ROUTE_UNAVAILABLE",
            _worker_count(),
        )

    setattr(app.state, f"{_STATE_KEY}_scheduled", True)


__all__ = [
    "DEFAULT_WORKERS",
    "MAX_WORKERS",
    "install_interactive_pick_hydration_wrapper",
    "prehydrate_batch",
    "schedule_interactive_pick_hydration_install",
]
