"""TheRundown market-data bootstrap and bounded history installer for V17.

The bootstrap remains idempotent and one-shot. An independent history task may
also be installed when ``WOW_RUNDOWN_MARKET_HISTORY_ENABLED=true``; that task is
moneyline-only, multi-book, quota-bounded, and evidence-only.

Bootstrap modes:
- OFF: no one-shot bootstrap (default)
- CATALOGS_ONLY: refresh provider reference catalogs once for a bootstrap token
- SNAPSHOT_ONCE: execute exactly one filtered snapshot for a bootstrap token

Neither path changes sporting probability, ranking, terminal governance, or
execution authority. ``can_execute`` remains false.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from v17 import rundown_market_history as history
from v17 import rundown_market_ingestor as ingestor
from v17 import rundown_market_ledger as ledger

CAN_EXECUTE = False
LOGGER = logging.getLogger("wow.v17.rundown_market_bootstrap")
_ALLOWED_MODES = {"OFF", "CATALOGS_ONLY", "SNAPSHOT_ONCE"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mode() -> str:
    return os.getenv("WOW_RUNDOWN_MARKET_STARTUP_MODE", "OFF").strip().upper()


def _token() -> str:
    return os.getenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_TOKEN", "").strip()


def _csv(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _marker_key(mode: str, token: str) -> str:
    return f"RUNDOWN:BOOTSTRAP:{mode}:{token}"


def _already_completed(client: Any, marker_key: str) -> bool:
    try:
        result = (
            client.table(ledger.SYNC_TABLE)
            .select("feed_key,last_success_at")
            .eq("feed_key", marker_key)
            .limit(1)
            .execute()
        )
    except Exception:  # noqa: BLE001 - startup validation must not affect liveness
        return False
    rows = getattr(result, "data", None) or []
    return bool(rows and rows[0].get("last_success_at"))


def _persist_marker(client: Any, marker_key: str, *, mode: str, result: dict[str, Any]) -> None:
    success = str(result.get("status") or "").upper() == "COMPLETE"
    state = {
        "feed_key": marker_key,
        "acquisition_mode": "SNAPSHOT",
        "last_rows_written": int(result.get("rows_written") or 0),
        "metadata": {
            "bootstrap_mode": mode,
            "bootstrap_status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "data_delay_seconds": result.get("data_delay_seconds"),
            "datapoints": result.get("datapoints"),
            "events": result.get("events"),
            "delta_eligible": result.get("delta_eligible"),
            "prediction_authority": False,
        },
    }
    if success:
        state["last_success_at"] = _now_iso()
        state["last_error_code"] = None
    else:
        state["last_failure_at"] = _now_iso()
        state["last_error_code"] = str(result.get("reason_code") or result.get("status") or "BOOTSTRAP_FAILED")
    ledger.persist_sync_state(client, state)


def run_bootstrap(client: Any) -> dict[str, Any]:
    """Run the configured one-shot bootstrap synchronously."""
    mode = _mode()
    if mode not in _ALLOWED_MODES:
        return {
            "status": "BLOCKED",
            "reason_code": "RUNDOWN_BOOTSTRAP_MODE_INVALID",
            "mode": mode,
            "prediction_authority": False,
            "can_execute": False,
        }
    if mode == "OFF":
        return {
            "status": "DISABLED",
            "mode": mode,
            "prediction_authority": False,
            "can_execute": False,
        }

    token = _token()
    if not token:
        return {
            "status": "BLOCKED",
            "reason_code": "RUNDOWN_BOOTSTRAP_TOKEN_REQUIRED",
            "mode": mode,
            "prediction_authority": False,
            "can_execute": False,
        }
    marker_key = _marker_key(mode, token)
    if _already_completed(client, marker_key):
        return {
            "status": "ALREADY_COMPLETE",
            "mode": mode,
            "marker_key": marker_key,
            "prediction_authority": False,
            "can_execute": False,
        }

    if mode == "CATALOGS_ONLY":
        result = ingestor.refresh_catalogs(client)
        normalized = {
            **dict(result),
            "mode": mode,
            "rows_written": sum((result.get("rows_written") or {}).values()),
            "prediction_authority": False,
            "can_execute": False,
        }
    else:
        sport_key = os.getenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_SPORT_KEY", "").strip()
        slate_date = os.getenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_SLATE_DATE", "").strip()
        market_ids = _csv("WOW_RUNDOWN_MARKET_BOOTSTRAP_MARKET_IDS")
        affiliate_ids = _csv("WOW_RUNDOWN_MARKET_BOOTSTRAP_AFFILIATE_IDS")
        if not sport_key or not slate_date or not market_ids or not affiliate_ids:
            return {
                "status": "BLOCKED",
                "reason_code": "RUNDOWN_BOOTSTRAP_FILTERS_REQUIRED",
                "mode": mode,
                "prediction_authority": False,
                "can_execute": False,
            }
        normalized = {
            **ingestor.collect_snapshot(
                client,
                sport_key=sport_key,
                slate_date=slate_date,
                market_ids=market_ids,
                affiliate_ids=affiliate_ids,
                allow_delta=True,
                include_all_periods=False,
            ),
            "mode": mode,
            "prediction_authority": False,
            "can_execute": False,
        }

    _persist_marker(client, marker_key, mode=mode, result=normalized)
    normalized["marker_key"] = marker_key
    return normalized


def install_rundown_market_startup_bootstrap(
    app: Any,
    *,
    db_client_fn: Callable[[], Any] | None,
) -> bool:
    """Install enabled one-shot bootstrap and/or recurring history tasks."""
    mode = _mode()
    history_enabled = history.enabled()
    if mode == "OFF" and not history_enabled:
        return False
    if mode not in _ALLOWED_MODES or not callable(db_client_fn):
        return False
    if getattr(app.state, "v17_rundown_market_bootstrap_installed", False):
        return True

    task_set: set[asyncio.Task] = set()
    app.state.v17_rundown_market_bootstrap_tasks = task_set

    @app.on_event("startup")
    async def _run_rundown_market_bootstrap_after_startup():
        async def _run_bootstrap_task():
            await asyncio.sleep(5.0)
            try:
                result = await asyncio.to_thread(lambda: run_bootstrap(db_client_fn()))
            except Exception as exc:  # noqa: BLE001 - bootstrap must never break service liveness
                LOGGER.exception(
                    "RUNDOWN_MARKET_BOOTSTRAP=FAIL mode=%s error_type=%s can_execute=false",
                    mode,
                    type(exc).__name__,
                )
                return
            LOGGER.info(
                "RUNDOWN_MARKET_BOOTSTRAP=%s mode=%s reason=%s rows=%s datapoints=%s can_execute=false",
                result.get("status"),
                mode,
                result.get("reason_code"),
                result.get("rows_written"),
                result.get("datapoints"),
            )

        if mode != "OFF":
            task = asyncio.create_task(_run_bootstrap_task())
            task_set.add(task)
            task.add_done_callback(task_set.discard)

        if history_enabled:
            history_task = asyncio.create_task(history.run_history_loop(db_client_fn, logger=LOGGER))
            task_set.add(history_task)
            history_task.add_done_callback(task_set.discard)
            LOGGER.info(
                "RUNDOWN_MARKET_HISTORY=INSTALLED interval=%s max_calls=%s max_datapoints=%s books=%s sports=%s can_execute=false",
                history.interval_seconds(),
                history.max_calls_per_day(),
                history.max_datapoints_per_day(),
                len(history.configured_books()),
                len(history.configured_sports()),
            )

    app.state.v17_rundown_market_bootstrap_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "install_rundown_market_startup_bootstrap",
    "run_bootstrap",
]
