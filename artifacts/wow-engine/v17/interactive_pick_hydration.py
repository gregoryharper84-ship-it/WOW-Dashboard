"""Bounded pre-hydration wrapper for interactive V17 prop batches.

The existing /score-pick-request handler remains sole owner of validation,
immutable persistence, fitted scoring, calibration, reconciliation, portfolio
governance, and terminal reduction. This wrapper moves only successful external
raw-evidence acquisition ahead of that handler and runs independent acquisitions
with a small bounded thread pool.

Rows that share one immutable evidence identity (for example MORE and LESS for
the same player/stat/event) share one external hydration result. Direction and
line are deliberately not part of the hydration identity because evidence is
upstream of settlement direction/threshold. The canonical scorer still receives
and scores every row independently.

If pre-hydration cannot prove route eligibility or any acquisition fails, the
row is passed to the captured canonical handler unchanged. Therefore failure
codes and fail-closed semantics remain owned by the canonical handler.

One narrow exception is an all-row EVENT_ALREADY_STARTED batch after the same
specialist/capability/certified-artifact preflight has already passed. Those
rows are terminalized immediately through the canonical terminal reducer and
Top-10 reconciler instead of repeating expensive downstream work that cannot
change a pregame-only event invalidation. This preserves blocker precedence,
row identity, reconciliation, and can_execute=false while keeping historical
reproduction traffic from exhausting the interactive transport path.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Optional

from fastapi import Header

from mlb_1ip_specialist import CANONICAL_STAT_TYPE as MLB_1IP_STAT_TYPE
import pick_request_runtime_core as pick_runtime
from pick_request_runtime_core import PickRequestBatch, PickRequestRow, RawPropEvidence, _canonical_stat
from prop_auto_hydration_router import auto_hydrate_prop_evidence
from v17.top10_model_reconciliation import enforce_top10_completion

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


def _event_started(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.utcoffset() is None:
        return False
    return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def _all_rows_started_and_preflight_ready(batch: PickRequestBatch, *, market_api: Any) -> bool:
    """Prove the exact canonical preflight before a fast EVENT_ALREADY_STARTED exit.

    Results are cached only for the lifetime of this single request and only by
    (sport, canonical stat). No model output, probability, calibration, or
    certification state is persisted or shared between requests.
    """
    specialist_cache: dict[tuple[str, str], Any] = {}
    route_cache: dict[tuple[str, str], Any] = {}
    lane: Any = None
    lane_loaded = False

    for row in batch.rows:
        if not _event_started(row.event_start_time):
            return False
        sport = str(row.sport or "").strip().upper()
        canonical_stat = _canonical_stat(sport, row.stat_type)
        key = (sport, canonical_stat)
        try:
            if key not in specialist_cache:
                specialist_cache[key] = market_api.prod.base_api._controlling_specialist_provider(
                    sport, canonical_stat
                )
            specialist = specialist_cache[key]
            if not isinstance(specialist, dict):
                return False
            if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
                return False

            if not lane_loaded:
                lane = market_api.prod._runtime_capability(market_api.prod.PROP_CAPABILITY_KEY)
                lane_loaded = True
            if not isinstance(lane, dict) or lane.get("capability_status") != "AVAILABLE":
                return False

            if key not in route_cache:
                route_cache[key] = market_api._prop_route_artifact(sport, canonical_stat)
            route = route_cache[key]
            if not isinstance(route, dict):
                return False
            if route.get("ok") is not True or route.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
                return False
        except Exception:
            return False
    return True


def _event_started_response(batch: PickRequestBatch) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    for index, row in enumerate(batch.rows):
        row_key = row.row_key or f"row-{index + 1}"
        outcomes.append(
            pick_runtime._terminal(
                row_key,
                "REJECTED",
                "EVENT_ALREADY_STARTED",
                detail={
                    "terminal_label": "NO_PLAY",
                    "specialist_invoked": False,
                    "fast_path": "PREGAME_EVENT_INVALIDATED",
                },
                acquisition={
                    "mode": "AUTO_HYDRATION",
                    "status": "FAILED",
                    "provider": "PREGAME_EVENT_GUARD",
                    "source_type": row.source_type,
                    "platform": row.platform,
                    "can_execute": False,
                },
            )
        )

    rows_in = len(batch.rows)
    response = {
        "ok": False,
        "request_id": batch.request_id,
        "run_controller_status": "BLOCKED",
        "rows_in": rows_in,
        "rows_completed": 0,
        "rows_held": 0,
        "rows_rejected": rows_in,
        "pick_rejected_count": sum(1 for row in outcomes if row.get("pick_rejected") is True),
        "infrastructure_blocked_count": sum(1 for row in outcomes if row.get("infrastructure_blocked") is True),
        "reconciliation_pass": True,
        "telemetry": pick_runtime._telemetry(outcomes),
        "specialist_utilization_summary": pick_runtime._specialist_utilization_summary(outcomes),
        "response_mode": batch.response_mode,
        "rows": (
            [pick_runtime._compact_pick_outcome(outcome) for outcome in outcomes]
            if batch.response_mode == "COMPACT"
            else outcomes
        ),
        "detail_retrieval": (
            {
                "mode": "IMMUTABLE_RECEIPT_LOOKUP",
                "operation_id": "lookupWowV17PredictionReceipts",
            }
            if batch.response_mode == "COMPACT"
            else None
        ),
        "probability_objective": "GOVERNED_MODEL_ONLY",
        "can_execute": False,
    }
    return enforce_top10_completion(response, list(batch.rows))


def _hydration_key(row: PickRequestRow) -> tuple[str, ...]:
    """Identity of external evidence acquisition, intentionally direction-free."""
    return (
        str(row.sport or "").strip().upper(),
        _canonical_stat(row.sport, row.stat_type),
        " ".join(str(row.player or "").strip().split()),
        str(row.event_id or "").strip(),
        str(row.event_start_time or "").strip(),
        str(row.source_capture_timestamp or "").strip(),
        str(row.source_type or "").strip().upper(),
        str(row.platform or "").strip().upper(),
        str(row.opponent or "").strip().upper(),
    )


def _hydrate(row: PickRequestRow) -> RawPropEvidence:
    raw = auto_hydrate_prop_evidence(
        sport=str(row.sport or "").strip().upper(),
        player=row.player,
        stat_type=_canonical_stat(row.sport, row.stat_type),
        event_start_time=row.event_start_time,
        source_capture_timestamp=row.source_capture_timestamp,
        source_label=f"{row.source_type}:{row.platform or 'UNKNOWN'}",
        opponent=row.opponent,
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
    if workers <= 1 or not eligible:
        return batch

    groups: dict[tuple[str, ...], tuple[PickRequestRow, list[int]]] = {}
    for index, row in eligible:
        key = _hydration_key(row)
        if key not in groups:
            groups[key] = (row, [])
        groups[key][1].append(index)

    started = perf_counter()
    evidence_by_index: dict[int, RawPropEvidence] = {}
    failure_codes: set[str] = set()
    successful_fetches = 0
    pool_workers = min(workers, len(groups))

    with ThreadPoolExecutor(max_workers=pool_workers, thread_name_prefix="wow-prop-hydrate") as pool:
        future_to_key = {
            pool.submit(_hydrate, representative): key
            for key, (representative, _indices) in groups.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            _representative, indices = groups[key]
            try:
                evidence = future.result()
            except Exception as exc:
                # Preserve canonical failure typing by leaving every row in this
                # evidence group untouched. The captured handler performs its
                # normal acquisition path and remains terminal-status authority.
                failure_codes.add(str(getattr(exc, "code", None) or type(exc).__name__))
                continue
            successful_fetches += 1
            for index in indices:
                evidence_by_index[index] = evidence

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
        "rows_in=%s eligible=%s unique_fetches=%s successful_fetches=%s prefetched=%s "
        "reused=%s failed_fetches=%s failure_codes=%s workers=%s stage_ms=%.3f can_execute=false",
        len(batch.rows),
        len(eligible),
        len(groups),
        successful_fetches,
        len(evidence_by_index),
        max(0, len(evidence_by_index) - successful_fetches),
        len(groups) - successful_fetches,
        ",".join(sorted(failure_codes)) if failure_codes else "NONE",
        pool_workers,
        (perf_counter() - started) * 1000.0,
    )
    return hydrated


def install_interactive_pick_hydration_wrapper(app: Any, *, market_api: Any) -> bool:
    """Replace the canonical route with a pre-hydrating delegating wrapper.

    The captured endpoint remains the canonical scorer. Existing auth
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
        if _all_rows_started_and_preflight_ready(batch, market_api=market_api):
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_STAGE route=/score-pick-request stage=event-invalidated-fast-path "
                "rows_in=%s response_mode=%s can_execute=false",
                len(batch.rows),
                batch.response_mode,
            )
            return _event_started_response(batch)
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
