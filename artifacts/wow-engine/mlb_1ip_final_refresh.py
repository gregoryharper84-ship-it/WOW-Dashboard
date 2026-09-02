"""Automatic final-refresh runner for provisional MLB 1IP rows.

The runner is side-effect-safe and idempotent. It only refreshes rows that
were explicitly queued as provisional, never starts after event_start_time,
and never changes can_execute from false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from mlb_1ip_live_acquisition import hydrate_mlb_1ip_evidence

CAN_EXECUTE = False


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def refresh_queue_row(row: dict[str, Any], *, hydrator: Callable[..., dict[str, Any]] = hydrate_mlb_1ip_evidence, now: datetime | None = None) -> dict[str, Any]:
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(str(row["event_start_time"]))
    if ts >= event_start:
        return {
            "queue_id": row.get("queue_id"),
            "status": "EXPIRED_PREGAME_WINDOW",
            "rerun_required": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    hydrated = hydrator(player=str(row["player"]), event_start_time=str(row["event_start_time"]), now=ts)
    official = str(hydrated.get("official_lineup_status") or "").upper() == "CONFIRMED"
    starter_changed = str(hydrated.get("starter_name") or "").strip().casefold() != str(row.get("starter_name_at_capture") or "").strip().casefold()

    if starter_changed:
        return {
            "queue_id": row.get("queue_id"),
            "status": "SLATE_PURGE",
            "reason": "STARTER_CHANGED",
            "rerun_required": False,
            "terminal_label": "SLATE_PURGE",
            "probability_publishable": False,
            "can_execute": False,
        }
    if not official:
        return {
            "queue_id": row.get("queue_id"),
            "status": "WAITING_FOR_OFFICIAL_LINEUP",
            "rerun_required": False,
            "next_refresh_after_seconds": 300,
            "probability_publishable": False,
            "can_execute": False,
        }

    return {
        "queue_id": row.get("queue_id"),
        "status": "READY_TO_RERUN",
        "rerun_required": True,
        "refreshed_lineup_evidence": hydrated,
        "probability_publishable": False,
        "can_execute": False,
    }


def process_pending(rows: list[dict[str, Any]], *, hydrator: Callable[..., dict[str, Any]] = hydrate_mlb_1ip_evidence, now: datetime | None = None) -> list[dict[str, Any]]:
    return [refresh_queue_row(row, hydrator=hydrator, now=now) for row in rows]
