"""Shared fail-closed pregame eligibility checks for Daily moneyline events."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

can_execute = False

_NON_PREGAME_STATES = frozenset({
    "LIVE", "IN_PROGRESS", "STARTED", "FINAL", "COMPLETED", "CLOSED",
    "CANCELLED", "POSTPONED", "SUSPENDED",
})


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def pregame_exclusion_reason(event: dict[str, Any], *, now: datetime | None = None) -> str | None:
    """Return an explicit exclusion reason unless an event is reliably pregame."""
    if not isinstance(event, dict):
        return "EVENT_NOT_OBJECT"
    now = now or datetime.now(timezone.utc)
    state = str(
        event.get("game_status") or event.get("status") or event.get("event_status") or ""
    ).strip().upper()
    if state in _NON_PREGAME_STATES:
        return f"EVENT_STATUS_NOT_PREGAME:{state}"
    commence = _parse_timestamp(event.get("commence_time") or event.get("game_time"))
    if commence is None:
        return "COMMENCE_TIME_UNAVAILABLE"
    if commence <= now:
        return "EVENT_ALREADY_STARTED"
    return None


def is_pregame_event(event: dict[str, Any], *, now: datetime | None = None) -> bool:
    """True only for a future event without a non-pregame provider state."""
    return pregame_exclusion_reason(event, now=now) is None