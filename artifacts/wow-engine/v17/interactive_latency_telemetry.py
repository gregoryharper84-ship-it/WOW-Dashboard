"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from threading import Lock
from time import perf_counter
from typing import Any, Iterator, Optional


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})
STAGE_NAMES = frozenset({
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
})
ROW_COUNT_BUCKETS = ((1, "1"), (4, "2-4"), (16, "5-16"), (50, "17-50"), (100, "51-100"))
MAX_SAMPLES_PER_KEY = 256
MAX_AGGREGATE_KEYS = 256

# One mutable per-request state dict: sync endpoints run in a copied context, so
# mutation (not ContextVar.set) is what propagates back to the middleware.
_STATE: ContextVar[Optional[dict[str, Any]]] = ContextVar("wow_interactive_latency_state", default=None)


def row_count_bucket(row_count: Optional[int]) -> str:
    """Bound label cardinality so the in-process aggregator cannot grow unbounded."""
    if row_count is None:
        return "unknown"
    for upper, label in ROW_COUNT_BUCKETS:
        if row_count <= upper:
            return label
    return "100+"


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Accumulate wall time for a named stage on the current request, if one is active."""
    state = _STATE.get()
    if state is None or stage not in STAGE_NAMES:
        yield
        return
    stages = state["stages"]
    started = perf_counter()
    try:
        yield
    finally:
        stages[stage] = stages.get(stage, 0.0) + (perf_counter() - started) * 1000.0


def note_request_shape(row_count: int, sports: Optional[set[str]] = None) -> None:
    """Record row count and sport dimensions without touching the request body."""
    state = _STATE.get()
    if state is None:
        return
    state["row_count"] = int(row_count)
    if sports:
        state["sports"] = {str(s).strip().upper()[:16] for s in sports}


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[index]


class LatencyAggregator:
    """Bounded in-process p50/p95 aggregation keyed by route/sport/row-count bucket."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._samples: dict[tuple[str, str, str], list[float]] = {}

    def record(self, route: str, sport: str, bucket: str, total_ms: float) -> None:
        key = (route, sport, bucket)
        with self._lock:
            samples = self._samples.get(key)
            if samples is None:
                if len(self._samples) >= MAX_AGGREGATE_KEYS:
                    return
                samples = self._samples[key] = []
            samples.append(total_ms)
            if len(samples) > MAX_SAMPLES_PER_KEY:
                del samples[0]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [(key, sorted(values)) for key, values in self._samples.items()]
        return [
            {
                "route": route,
                "sport": sport,
                "row_count_bucket": bucket,
                "samples": len(values),
                "p50_ms": round(_percentile(values, 0.5), 3),
                "p95_ms": round(_percentile(values, 0.95), 3),
                "can_execute": False,
            }
            for (route, sport, bucket), values in sorted(items)
        ]

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


AGGREGATOR = LatencyAggregator()


def install_interactive_latency_middleware(app: Any) -> None:
    """Install one idempotent total-wall-time probe for interactive V17 routes."""
    if getattr(app.state, "wow_interactive_latency_installed", False):
        return

    @app.middleware("http")
    async def _interactive_latency_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in INTERACTIVE_PATHS:
            return await call_next(request)

        state: dict[str, Any] = {"stages": {}, "row_count": None, "sports": None}
        token = _STATE.set(state)
        stages = state["stages"]
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            total_ms = (perf_counter() - started) * 1000.0
            row_count = state["row_count"]
            sports = state["sports"]
            sport = next(iter(sports)) if sports and len(sports) == 1 else ("MIXED" if sports else "unknown")
            bucket = row_count_bucket(row_count)
            AGGREGATOR.record(path, sport, bucket, total_ms)
            stage_text = ",".join(f"{name}={stages[name]:.3f}" for name in sorted(stages)) or "none"
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f "
                "sport=%s row_count_bucket=%s stage_ms=%s can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
                sport,
                bucket,
                stage_text,
            )
            try:
                _STATE.reset(token)
            except ValueError:
                pass

    app.state.wow_interactive_latency_installed = True
