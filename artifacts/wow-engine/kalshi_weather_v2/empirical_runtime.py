from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI, Header, HTTPException

from .bounded_cohort import run_bounded_capture_only_cohort_once
from .http_client import ReadOnlyJsonClient
from .operational_cycle import VERIFIED_HOURLY_TARGETS
from .shadow_cohort import (
    HourlyCohortTarget,
    _settle_ready_predictions,
    run_bounded_settlement_backlog_once,
)


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


def _collector_owner_label() -> str:
    """Non-secret deployment identity for diagnostics, never a credential."""
    for name in (
        "WOW_KALSHI_WEATHER_COLLECTOR_OWNER_ID",
        "RENDER_SERVICE_NAME",
        "RENDER_INSTANCE_ID",
    ):
        value = os.getenv(name, "").strip()
        if value:
            return value[:96]
    return "UNSPECIFIED"


def _normalize_utc(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _latest_capture_at(db: object) -> str | None:
    """Newest immutable prediction write, the only proof of usable capture."""
    try:
        response = (
            db.table("wow_kalshi_weather_predictions")
            .select("created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
    except Exception:
        return None
    for row in rows:
        if isinstance(row, Mapping):
            normalized = _normalize_utc(row.get("created_at"))
            if normalized:
                return normalized
    return None


def _collection_staleness_threshold_seconds() -> int:
    try:
        value = int(os.getenv("WOW_KALSHI_WEATHER_COLLECTION_STALENESS_SECONDS", "7200"))
    except ValueError:
        value = 7200
    return max(900, value)


def build_collection_diagnostics(
    *,
    result: Any,
    targets: tuple[HourlyCohortTarget, ...],
    trigger: str,
    elapsed_seconds: float,
    latest_capture_at: str | None,
    memory_ratio: float | None = None,
) -> dict[str, object]:
    """Machine-readable proof of what a trigger actually produced.

    A scheduler receipt, an HTTP 200 or a clean pass is never by itself
    evidence of data acquisition; ``predictions_written`` and the durable
    ``latest_successful_capture_at`` are what an operator must read.
    """
    now = datetime.now(timezone.utc)
    threshold = _collection_staleness_threshold_seconds()
    age_seconds: float | None = None
    if latest_capture_at:
        parsed = _normalize_utc(latest_capture_at)
        if parsed:
            age_seconds = max(
                0.0,
                (now - datetime.fromisoformat(parsed.replace("Z", "+00:00"))).total_seconds(),
            )

    failure_samples = (
        list(result.capture_failures)
        + list(result.supplemental_failures)
        + list(result.settlement_failures)
        + list(result.market_microstructure_failures)
    )
    return {
        "status": result.status,
        "trigger": trigger,
        "captured_at": now.isoformat().replace("+00:00", "Z"),
        "collector_owner": _collector_owner_label(),
        "targets": [target.index_city for target in targets if target.enabled],
        "targets_checked": result.targets_checked,
        "eligible_contracts_discovered": result.contracts_discovered,
        "predictions_written": result.samples_captured,
        "duplicate_samples_skipped": result.samples_skipped_existing,
        "source_snapshots_written": result.supplemental_snapshots_captured,
        "market_snapshots_written": result.market_microstructure_snapshots_captured,
        "low_memory_mode": os.getenv(
            "WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE", "0"
        )
        == "1",
        "low_memory_deferred_buckets": int(
            getattr(result, "low_memory_suppressed", 0) or 0
        ),
        "predictions_settled": result.predictions_settled,
        "failure_counts": {
            "discovery_or_capture": len(result.capture_failures),
            "supplemental_source": len(result.supplemental_failures),
            "settlement": len(result.settlement_failures),
            "market_microstructure": len(result.market_microstructure_failures),
        },
        "failure_samples": failure_samples[:10],
        "usable_capture": result.samples_captured > 0,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "memory_ratio": None if memory_ratio is None else round(float(memory_ratio), 4),
        "latest_successful_capture_at": latest_capture_at,
        "latest_successful_capture_age_seconds": (
            None if age_seconds is None else round(age_seconds, 1)
        ),
        "collection_staleness_threshold_seconds": threshold,
        "collection_fresh": (
            None if age_seconds is None else age_seconds <= threshold
        ),
        "probability_publishable": False,
        "can_execute": False,
    }


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


def _run_bounded_shadow_cycle(
    *,
    db_client_fn: Callable[[], object],
    targets: tuple[HourlyCohortTarget, ...] | None = None,
):
    # Capture and grading remain separate operations. Settlement can run only
    # on the explicitly owned singleton and is capped to avoid replay storms.
    selected = _scheduled_shadow_targets() if targets is None else tuple(targets)
    result = run_bounded_capture_only_cohort_once(
        db_client_fn=db_client_fn,
        targets=selected,
    )
    # The 512-MiB core-only capture cannot safely run the settlement backlog
    # in the same process. Settlement remains an isolated bounded workload.
    if os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE", "0") == "1":
        return result
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


def _require_cohort_key(supplied_header: str | None) -> None:
    """Fail closed. An unconfigured or mismatched trigger never proceeds."""
    expected = os.getenv("WOW_KALSHI_WEATHER_COHORT_KEY", "")
    supplied = str(supplied_header or "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "KALSHI_WEATHER_COHORT_TRIGGER_UNCONFIGURED",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "KALSHI_WEATHER_COHORT_TRIGGER_UNAUTHORIZED",
                "probability_publishable": False,
                "can_execute": False,
            },
        )


def install_empirical_cohort_scheduler(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], object],
) -> None:
    """Install bounded automated Weather calibration collection plus trigger.

    The shared web process captures one representative threshold sibling and a
    small rotating target slice per pass. Historical settlement backlog is kept
    out of this process boundary so old rows cannot recycle the production API.
    Exact sibling contracts remain available through authenticated on-demand
    Weather routes.

    Nothing here can promote model capability, publish an uncertified
    probability, or execute a trade.
    """
    if getattr(app.state, "kalshi_weather_empirical_scheduler_installed", False):
        return
    app.state.kalshi_weather_empirical_scheduler_installed = True

    @app.post(
        "/internal/kalshi-weather/v17/empirical-cohort/cron-run",
        operation_id="runKalshiWeatherV17EmpiricalCohortCron",
        include_in_schema=False,
    )
    def _cron_run(
        x_wow_weather_cohort_key: str | None = Header(
            default=None,
            alias="X-WOW-Weather-Cohort-Key",
        ),
    ):
        _require_cohort_key(x_wow_weather_cohort_key)
        trigger = "EXTERNAL_LEAST_PRIVILEGE_SCHEDULER"
        selected = _scheduled_shadow_targets()
        under_pressure, memory_ratio = _weather_memory_pressure()
        if under_pressure:
            _logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=SKIPPED_MEMORY_PRESSURE trigger=EXTERNAL_LEAST_PRIVILEGE_SCHEDULER memory_ratio=%.4f probability_publishable=false can_execute=false",
                memory_ratio or 0.0,
            )
            return {
                "status": "SKIPPED_MEMORY_PRESSURE",
                "trigger": trigger,
                "collection_mode": "BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW",
                "memory_ratio": memory_ratio,
                "collection_diagnostics": {
                    "status": "SKIPPED_MEMORY_PRESSURE",
                    "trigger": trigger,
                    "collector_owner": _collector_owner_label(),
                    "targets": [target.index_city for target in selected if target.enabled],
                    "predictions_written": 0,
                    "usable_capture": False,
                    "memory_ratio": memory_ratio,
                    "probability_publishable": False,
                    "can_execute": False,
                },
                "probability_publishable": False,
                "can_execute": False,
            }

        started = time.monotonic()
        result = _run_bounded_shadow_cycle(db_client_fn=db_client_fn, targets=selected)
        elapsed_seconds = time.monotonic() - started
        try:
            latest_capture_at = _latest_capture_at(db_client_fn())
        except Exception:
            latest_capture_at = None
        diagnostics = build_collection_diagnostics(
            result=result,
            targets=selected,
            trigger=trigger,
            elapsed_seconds=elapsed_seconds,
            latest_capture_at=latest_capture_at,
            memory_ratio=memory_ratio,
        )
        _logger.warning(
            "WOW_KALSHI_WEATHER_COLLECTION status=%s trigger=%s predictions_written=%s duplicates_skipped=%s latest_successful_capture_at=%s collection_fresh=%s probability_publishable=false can_execute=false",
            diagnostics["status"],
            trigger,
            diagnostics["predictions_written"],
            diagnostics["duplicate_samples_skipped"],
            diagnostics["latest_successful_capture_at"],
            diagnostics["collection_fresh"],
        )
        return {
            **asdict(result),
            "trigger": trigger,
            "collection_mode": "BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW",
            "collection_diagnostics": diagnostics,
            "probability_publishable": False,
            "can_execute": False,
        }

    @app.post(
        "/internal/kalshi-weather/v17/settlement-backlog/run",
        operation_id="runKalshiWeatherV17SettlementBacklog",
        include_in_schema=False,
    )
    def _settlement_backlog_run(
        x_wow_weather_cohort_key: str | None = Header(
            default=None,
            alias="X-WOW-Weather-Cohort-Key",
        ),
    ):
        """Drain a hard-capped settlement slice, separately from capture.

        Settlement re-reads official observations, so it is a distinct memory
        and latency workload. It stays opt-in and disabled by default, and it
        can never advance capability, publish a probability, or execute.
        """
        _require_cohort_key(x_wow_weather_cohort_key)
        if os.getenv("WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG_ENABLED", "0") != "1":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "KALSHI_WEATHER_SETTLEMENT_BACKLOG_DISABLED",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        try:
            max_attempts = int(
                os.getenv("WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG_MAX_ATTEMPTS", "10")
            )
        except ValueError:
            max_attempts = 10
        max_attempts = max(1, min(max_attempts, 25))
        result = run_bounded_settlement_backlog_once(
            client=db_client_fn(),
            max_attempts=max_attempts,
        )
        _logger.warning(
            "WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG status=%s pending=%s attempted=%s settled=%s remaining=%s failures=%s probability_publishable=false can_execute=false",
            result.status,
            result.pending_eligible,
            result.attempted,
            result.settled,
            result.remaining_after_run,
            len(result.failures),
        )
        return {
            **asdict(result),
            "trigger": "EXTERNAL_LEAST_PRIVILEGE_SCHEDULER",
            "probability_publishable": False,
            "can_execute": False,
        }

    @app.on_event("startup")
    async def _schedule_empirical_cohort() -> None:
        if os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED", "0") != "1":
            _logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=DISABLED probability_publishable=false can_execute=false"
            )
            return
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
