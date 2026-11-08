from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI

from .bounded_cohort import run_bounded_capture_only_cohort_once
from .http_client import ReadOnlyJsonClient
from .operational_cycle import VERIFIED_HOURLY_TARGETS
from .shadow_cohort import HourlyCohortTarget, _settle_ready_predictions


_logger = logging.getLogger("wow.kalshi_weather_v2.empirical_cohort")
_tasks: set[asyncio.Task] = set()


def _weather_memory_pressure() -> tuple[bool, float | None]:
    """Shed noncritical shadow collection when the shared web process is near OOM."""
    candidates = (
        (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory.max")),
        (
            Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        ),
    )
    sample: tuple[int, int] | None = None
    for current_path, limit_path in candidates:
        try:
            current_text = current_path.read_text(encoding="utf-8").strip()
            limit_text = limit_path.read_text(encoding="utf-8").strip()
            if not current_text or not limit_text or limit_text.lower() == "max":
                continue
            current = int(current_text)
            limit = int(limit_text)
        except (OSError, ValueError):
            continue
        if current >= 0 and limit > 0:
            sample = (current, limit)
            break

    if sample is None:
        return False, None

    try:
        max_ratio = float(os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_MEMORY_RATIO", "0.85"))
    except ValueError:
        max_ratio = 0.85
    max_ratio = max(0.50, min(max_ratio, 0.95))
    ratio = sample[0] / sample[1]
    return ratio >= max_ratio, ratio


def _automated_shadow_targets() -> tuple[HourlyCohortTarget, ...]:
    """Mirror governed operational targets into the bounded empirical collector.

    Exact settlement identity remains controlled by frozen Kalshi rules. These
    coordinates are forecast-grid references only.
    """
    return tuple(
        HourlyCohortTarget(
            index_city=target.index_city,
            expected_location=target.expected_location,
            forecast_latitude=target.forecast_latitude,
            forecast_longitude=target.forecast_longitude,
            forecast_reference_note=target.forecast_reference_note,
            enabled=target.enabled,
        )
        for target in VERIFIED_HOURLY_TARGETS
    )


def _scheduled_shadow_targets() -> tuple[HourlyCohortTarget, ...]:
    """Rotate a small target slice through the shared low-resource web process.

    The empirical collector is calibration-first, not the full live market
    monitor. Defaulting to one city per 15-minute pass prevents NWS/Open-Meteo
    acquisition plus historical settlement backlog from destabilizing V17. The
    exact on-demand Weather routes remain available for any individual contract.
    """
    enabled = tuple(target for target in _automated_shadow_targets() if target.enabled)
    if not enabled:
        return ()
    pinned_city = os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_TARGET_CITY", "").strip().lower()
    if pinned_city:
        # An unknown explicit target fails closed rather than rotating stations.
        return tuple(t for t in enabled if t.index_city == pinned_city)[:1]
    try:
        max_targets = int(os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", "1"))
    except ValueError:
        max_targets = 1
    max_targets = max(1, min(max_targets, len(enabled)))
    slot = int(datetime.now(timezone.utc).timestamp() // 900)
    start = slot % len(enabled)
    return tuple(enabled[(start + offset) % len(enabled)] for offset in range(max_targets))


def _run_bounded_shadow_cycle(*, db_client_fn: Callable[[], object]):
    # Capture and grading remain separate operations. Settlement can run only
    # on the explicitly owned singleton and is capped to avoid replay storms.
    result = run_bounded_capture_only_cohort_once(
        db_client_fn=db_client_fn,
        targets=_scheduled_shadow_targets(),
    )
    if (
        os.getenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "0") != "1"
        or os.getenv("WOW_KALSHI_WEATHER_ISOLATED_SETTLEMENT_ENABLED", "0") != "1"
    ):
        return result

    failures: list[str] = []
    settled = 0
    http = ReadOnlyJsonClient()
    try:
        settled = _settle_ready_predictions(
            db_client_fn(),
            http,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            failures,
            max_attempts=2,
        )
    except Exception as exc:
        failures.append(f"BOUNDED_SETTLEMENT:{type(exc).__name__}")
    finally:
        http.close()
    return replace(
        result,
        predictions_settled=settled,
        settlement_failures=tuple(failures),
    )



_DISPATCH_QUEUE_TABLE = "wow_kalshi_weather_collection_dispatch_queue"
_DISPATCH_CLAIM_RPC = "claim_wow_kalshi_weather_collection_dispatch"


def _claim_next_dispatch(db_client_fn: Callable[[], object]) -> dict[str, object] | None:
    """Atomically claim one durable scheduled collection dispatch."""
    response = db_client_fn().rpc(_DISPATCH_CLAIM_RPC).execute()
    rows = getattr(response, "data", None)
    if not isinstance(rows, list):
        raise RuntimeError("KALSHI_WEATHER_DISPATCH_CLAIM_INVALID")
    if not rows:
        return None
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise RuntimeError("KALSHI_WEATHER_DISPATCH_CLAIM_INVALID")
    row = dict(rows[0])
    if type(row.get("dispatch_id")) is not int or row["dispatch_id"] <= 0:
        raise RuntimeError("KALSHI_WEATHER_DISPATCH_ID_INVALID")
    return row


def _dispatch_result_summary(result: object) -> dict[str, object]:
    """Persist bounded counts and typed status, never raw provider payloads."""
    return {
        "status": str(getattr(result, "status", "UNKNOWN")),
        "targets_checked": int(getattr(result, "targets_checked", 0)),
        "contracts_discovered": int(getattr(result, "contracts_discovered", 0)),
        "samples_captured": int(getattr(result, "samples_captured", 0)),
        "samples_skipped_existing": int(getattr(result, "samples_skipped_existing", 0)),
        "supplemental_snapshots_captured": int(
            getattr(result, "supplemental_snapshots_captured", 0)
        ),
        "market_microstructure_snapshots_captured": int(
            getattr(result, "market_microstructure_snapshots_captured", 0)
        ),
        "predictions_settled": int(getattr(result, "predictions_settled", 0)),
        "capture_failure_count": len(getattr(result, "capture_failures", ()) or ()),
        "supplemental_failure_count": len(
            getattr(result, "supplemental_failures", ()) or ()
        ),
        "market_microstructure_failure_count": len(
            getattr(result, "market_microstructure_failures", ()) or ()
        ),
        "settlement_failure_count": len(getattr(result, "settlement_failures", ()) or ()),
        "probability_publishable": False,
        "can_execute": False,
    }


def _finish_dispatch(
    db_client_fn: Callable[[], object],
    dispatch_id: int,
    status: str,
    *,
    result: Mapping[str, object] | None = None,
    error_code: str | None = None,
    attempts: int | None = None,
) -> None:
    """Complete or safely requeue a claimed dispatch using compare-and-set."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload: dict[str, object] = {
        "status": status,
        "result": dict(result) if result is not None else None,
        "last_error_code": error_code,
    }
    if status == "PENDING":
        payload["claimed_at"] = None
        payload["completed_at"] = None
        if attempts is not None:
            payload["attempts"] = max(0, int(attempts))
    else:
        payload["completed_at"] = now

    response = (
        db_client_fn()
        .table(_DISPATCH_QUEUE_TABLE)
        .update(payload)
        .eq("id", dispatch_id)
        .eq("status", "CLAIMED")
        .select("id")
        .execute()
    )
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("KALSHI_WEATHER_DISPATCH_FINISH_NOT_CONFIRMED")


async def _process_dispatch_once(db_client_fn: Callable[[], object]) -> bool:
    """Claim and process at most one queued capture pass."""
    dispatch = await asyncio.to_thread(_claim_next_dispatch, db_client_fn)
    if dispatch is None:
        return False
    dispatch_id = int(dispatch["dispatch_id"])
    attempts = int(dispatch.get("attempts") or 1)

    under_pressure, memory_ratio = _weather_memory_pressure()
    if under_pressure:
        await asyncio.to_thread(
            _finish_dispatch,
            db_client_fn,
            dispatch_id,
            "PENDING",
            result={
                "deferred_reason": "MEMORY_PRESSURE",
                "memory_ratio": memory_ratio,
                "probability_publishable": False,
                "can_execute": False,
            },
            error_code="MEMORY_PRESSURE",
            attempts=max(0, attempts - 1),
        )
        _logger.warning(
            "WOW_KALSHI_WEATHER_DISPATCH status=DEFERRED_MEMORY_PRESSURE dispatch_id=%s memory_ratio=%s probability_publishable=false can_execute=false",
            dispatch_id,
            memory_ratio,
        )
        return True

    try:
        result = await asyncio.to_thread(
            _run_bounded_shadow_cycle,
            db_client_fn=db_client_fn,
        )
        summary = _dispatch_result_summary(result)
        await asyncio.to_thread(
            _finish_dispatch, db_client_fn, dispatch_id, "COMPLETED", result=summary
        )
        _logger.warning(
            "WOW_KALSHI_WEATHER_DISPATCH status=COMPLETED dispatch_id=%s capture_status=%s captured=%s failures=%s probability_publishable=false can_execute=false",
            dispatch_id,
            summary["status"],
            summary["samples_captured"],
            summary["capture_failure_count"],
        )
    except Exception as exc:
        await asyncio.to_thread(
            _finish_dispatch,
            db_client_fn,
            dispatch_id,
            "FAILED",
            error_code=f"COLLECTION_{type(exc).__name__}"[:80],
            result={"status": "FAILED", "probability_publishable": False, "can_execute": False},
        )
        raise
    return True


async def _run_dispatch_queue_loop(
    *,
    db_client_fn: Callable[[], object],
    poll_interval_seconds: int,
) -> None:
    """Consume durable database dispatches; no HTTP credential leaves Postgres."""
    _logger.warning(
        "WOW_KALSHI_WEATHER_DISPATCH_QUEUE status=STARTED poll_interval_seconds=%s max_claims_per_poll=1 probability_publishable=false can_execute=false",
        poll_interval_seconds,
    )
    while True:
        try:
            await _process_dispatch_once(db_client_fn)
        except Exception as exc:
            _logger.exception(
                "WOW_KALSHI_WEATHER_DISPATCH_QUEUE status=FAILED error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(poll_interval_seconds)


def install_empirical_cohort_scheduler(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], object],
) -> None:
    """Install one isolated queue consumer or the mutually exclusive legacy loop.

    The preferred scheduler inserts a non-secret durable queue row. The
    isolated Render process claims it atomically with a service-role RPC; no
    cohort key is sent through a redirect-following HTTP transport.
    """
    if getattr(app.state, "kalshi_weather_empirical_scheduler_installed", False):
        return
    app.state.kalshi_weather_empirical_scheduler_installed = True

    @app.on_event("startup")
    async def _schedule_empirical_cohort() -> None:
        queue_enabled = (
            os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED", "0") == "1"
        )
        loop_enabled = (
            os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED", "0") == "1"
        )
        if queue_enabled and loop_enabled:
            _logger.error(
                "WOW_KALSHI_WEATHER_DISPATCH_QUEUE status=BLOCKED_MULTIPLE_WRITERS probability_publishable=false can_execute=false"
            )
            return
        if not queue_enabled and not loop_enabled:
            _logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=DISABLED probability_publishable=false can_execute=false"
            )
            return

        if queue_enabled:
            try:
                interval_seconds = int(
                    os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_POLL_SECONDS", "60")
                )
            except ValueError:
                interval_seconds = 60
            interval_seconds = max(15, min(interval_seconds, 300))
            task = asyncio.create_task(
                _run_dispatch_queue_loop(
                    db_client_fn=db_client_fn,
                    poll_interval_seconds=interval_seconds,
                )
            )
        else:
            try:
                interval_seconds = int(
                    os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_INTERVAL_SECONDS", "900")
                )
            except ValueError:
                interval_seconds = 900
            interval_seconds = max(300, interval_seconds)
            try:
                initial_delay_seconds = int(
                    os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_INITIAL_DELAY_SECONDS", "45")
                )
            except ValueError:
                initial_delay_seconds = 45
            initial_delay_seconds = max(0, min(initial_delay_seconds, 300))
            task = asyncio.create_task(
                _run_bounded_shadow_loop(
                    db_client_fn=db_client_fn,
                    interval_seconds=interval_seconds,
                    initial_delay_seconds=initial_delay_seconds,
                )
            )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _run_bounded_shadow_loop(
    *,
    db_client_fn: Callable[[], object],
    interval_seconds: int,
    initial_delay_seconds: int = 45,
) -> None:
    initial_delay = max(0, min(int(initial_delay_seconds), 300))
    _logger.warning(
        "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=STARTED collection_mode=BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW interval_seconds=%s initial_delay_seconds=%s max_targets=%s probability_publishable=false can_execute=false",
        interval_seconds,
        initial_delay,
        os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", "1"),
    )
    if initial_delay:
        await asyncio.sleep(float(initial_delay))
    while True:
        try:
            under_pressure, memory_ratio = _weather_memory_pressure()
            if under_pressure:
                _logger.warning(
                    "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=SKIPPED_MEMORY_PRESSURE collection_mode=BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW memory_ratio=%.4f probability_publishable=false can_execute=false",
                    memory_ratio or 0.0,
                )
                await asyncio.sleep(interval_seconds)
                continue

            result = await asyncio.to_thread(
                _run_bounded_shadow_cycle,
                db_client_fn=db_client_fn,
            )
            _logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=%s collection_mode=BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW targets=%s discovered=%s captured=%s skipped=%s supplemental=%s market_microstructure=%s settled=%s capture_failures=%s supplemental_failures=%s market_microstructure_failures=%s settlement_failures=%s failure_samples=%s probability_publishable=false can_execute=false",
                result.status,
                result.targets_checked,
                result.contracts_discovered,
                result.samples_captured,
                result.samples_skipped_existing,
                result.supplemental_snapshots_captured,
                result.market_microstructure_snapshots_captured,
                result.predictions_settled,
                len(result.capture_failures),
                len(result.supplemental_failures),
                len(result.market_microstructure_failures),
                len(result.settlement_failures),
                list(result.capture_failures[:6]),
            )
        except Exception as exc:
            _logger.exception(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=FAILED collection_mode=BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)
