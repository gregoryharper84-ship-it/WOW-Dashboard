"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

import contextvars
import logging
import re
import threading
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterator


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})
STAGES = (
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
)
_MAX_SAMPLES_PER_KEY = 512
_MAX_KEYS = 256
_SPORT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,24}$")
_BUCKET_EDGES = (1, 2, 4, 8, 16, 32, 64)

_REQUEST_STATE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "wow_interactive_latency_state", default=None
)


def _bucket(value: Any) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if n < 0:
        return "unknown"
    for edge in _BUCKET_EDGES:
        if n <= edge:
            return f"<={edge}"
    return f">{_BUCKET_EDGES[-1]}"


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    rank = max(0, min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1)))))
    return sorted_values[rank]


class LatencyAggregator:
    """Bounded in-process p50/p95 store. Per-process, not a global figure."""

    def __init__(self, max_keys: int = _MAX_KEYS, max_samples: int = _MAX_SAMPLES_PER_KEY) -> None:
        self._max_keys = max_keys
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._samples: dict[tuple[str, str, str, str, str], list[float]] = {}

    def record(self, route: str, sport: str, row_count: Any, batch_size: Any, stage: str, ms: float) -> None:
        key = (route, sport, _bucket(row_count), _bucket(batch_size), stage)
        with self._lock:
            bucket = self._samples.get(key)
            if bucket is None:
                if len(self._samples) >= self._max_keys:
                    return
                bucket = self._samples[key] = []
            bucket.append(float(ms))
            if len(bucket) > self._max_samples:
                del bucket[0]

    def summary(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [(k, sorted(v)) for k, v in self._samples.items()]
        return [
            {
                "route": k[0],
                "sport": k[1],
                "row_count_bucket": k[2],
                "batch_size_bucket": k[3],
                "stage": k[4],
                "count": len(v),
                "p50_ms": round(_percentile(v, 0.50), 3),
                "p95_ms": round(_percentile(v, 0.95), 3),
                "can_execute": False,
            }
            for k, v in items
        ]

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


AGGREGATOR = LatencyAggregator()


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Pure timing wrapper: never alters the wrapped result or exception."""
    state = _REQUEST_STATE.get()
    started = perf_counter()
    try:
        yield
    finally:
        try:
            if state is not None:
                elapsed = (perf_counter() - started) * 1000.0
                stages = state["stages"]
                stages[stage] = stages.get(stage, 0.0) + elapsed
        except Exception:  # observability must never gate scoring
            pass


def annotate_request(sport: Any = None, row_count: Any = None, batch_size: Any = None) -> None:
    """Attach non-secret dimensions to the current request's telemetry record."""
    state = _REQUEST_STATE.get()
    if state is None:
        return
    if isinstance(sport, str) and _SPORT_RE.match(sport):
        state["sport"] = sport.upper()
    if row_count is not None:
        state["row_count"] = row_count
    if batch_size is not None:
        state["batch_size"] = batch_size


def _emit_stage_record(route: str, status_code: int, total_ms: float, state: dict[str, Any]) -> None:
    stages: dict[str, float] = state["stages"]
    sport = state["sport"]
    for stage, ms in stages.items():
        AGGREGATOR.record(route, sport, state["row_count"], state["batch_size"], stage, ms)
    AGGREGATOR.record(route, sport, state["row_count"], state["batch_size"], "total", total_ms)
    LOGGER.warning(
        "WOW_V17_INTERACTIVE_STAGE_TIMING route=%s sport=%s row_count=%s batch_size=%s status_code=%s "
        "stages_ms=%s total_ms=%.3f can_execute=false",
        route,
        sport,
        _bucket(state["row_count"]),
        _bucket(state["batch_size"]),
        status_code,
        ",".join(f"{name}:{stages[name]:.3f}" for name in STAGES if name in stages) or "none",
        total_ms,
    )


def install_interactive_latency_middleware(app: Any) -> None:
    """Install one idempotent total-wall-time probe for interactive V17 routes."""
    if getattr(app.state, "wow_interactive_latency_installed", False):
        return

    @app.middleware("http")
    async def _interactive_latency_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in INTERACTIVE_PATHS:
            return await call_next(request)

        state: dict[str, Any] = {"stages": {}, "sport": "unknown", "row_count": None, "batch_size": None}
        token = _REQUEST_STATE.set(state)
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            total_ms = (perf_counter() - started) * 1000.0
            _REQUEST_STATE.reset(token)
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
            )
            try:
                _emit_stage_record(path, status_code, total_ms, state)
            except Exception:  # observability must never gate the response
                pass

    app.state.wow_interactive_latency_installed = True
