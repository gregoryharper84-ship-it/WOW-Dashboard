"""
gate_engine/universal_agent/model_validation/learning_schedule.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Two-Speed Learning Schedule.

Fast channel:  daily feature refresh (statistical summaries, new game log data).
Slow channel:  weekly / monthly model-weight update (pending explicit governance).

This module reports the schedule status and records update events.
It does NOT trigger any updates automatically, does NOT change production
probability formulas, and has no network I/O.

No weight update fires without an explicit governance_pin.
can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


class ChannelType:
    FAST = "FAST"   # daily feature refresh
    SLOW = "SLOW"   # weekly/monthly weight update (governance required)


@dataclass(frozen=True)
class ScheduleEvent:
    """One recorded schedule event (advisory log only)."""
    channel:        str        # ChannelType constant
    model_id:       str
    event_type:     str        # "FEATURE_REFRESH" | "WEIGHT_UPDATE_REQUESTED" | "WEIGHT_UPDATE_BLOCKED"
    triggered_at:   str        # ISO-8601
    governance_pin: str | None # set only for SLOW channel events
    notes:          str | None


class TwoSpeedLearningSchedule:
    """
    Advisory-only learning schedule tracker.
    can_execute = False.

    FAST channel: caller records a feature refresh; no governance required.
    SLOW channel: caller records a weight-update request; governance_pin required
                  to log as WEIGHT_UPDATE_REQUESTED (else logs WEIGHT_UPDATE_BLOCKED).
    """

    def __init__(self, model_id: str) -> None:
        self.model_id   = model_id
        self._events:   list[ScheduleEvent] = []
        self._last_fast: str | None = None   # date of last fast refresh
        self._last_slow: str | None = None   # date of last slow update

    def record_fast_refresh(
        self,
        *,
        features_updated: list[str] | None = None,
        notes:            str | None = None,
    ) -> ScheduleEvent:
        """Record a daily feature refresh (FAST channel). No governance required."""
        ts = datetime.now(timezone.utc).isoformat()
        ev = ScheduleEvent(
            channel=ChannelType.FAST,
            model_id=self.model_id,
            event_type="FEATURE_REFRESH",
            triggered_at=ts,
            governance_pin=None,
            notes=notes or f"features={features_updated or 'all'}",
        )
        self._events.append(ev)
        self._last_fast = ts[:10]
        return ev

    def record_weight_update_request(
        self,
        *,
        governance_pin: str | None = None,
        notes:          str | None = None,
    ) -> ScheduleEvent:
        """
        Record a SLOW-channel weight update request.
        Without a governance_pin, logs WEIGHT_UPDATE_BLOCKED.
        With a governance_pin, logs WEIGHT_UPDATE_REQUESTED.
        Neither actually updates any weights.
        """
        ts = datetime.now(timezone.utc).isoformat()
        if governance_pin and governance_pin.strip():
            event_type = "WEIGHT_UPDATE_REQUESTED"
            self._last_slow = ts[:10]
        else:
            event_type = "WEIGHT_UPDATE_BLOCKED"
            notes = f"[blocked: no governance_pin] {notes or ''}".strip()

        ev = ScheduleEvent(
            channel=ChannelType.SLOW,
            model_id=self.model_id,
            event_type=event_type,
            triggered_at=ts,
            governance_pin=governance_pin,
            notes=notes,
        )
        self._events.append(ev)
        return ev

    def summary(self) -> dict[str, Any]:
        fast_events  = [e for e in self._events if e.channel == ChannelType.FAST]
        slow_events  = [e for e in self._events if e.channel == ChannelType.SLOW]
        blocked      = [e for e in slow_events if e.event_type == "WEIGHT_UPDATE_BLOCKED"]
        requested    = [e for e in slow_events if e.event_type == "WEIGHT_UPDATE_REQUESTED"]
        return {
            "model_id":              self.model_id,
            "fast_refreshes":        len(fast_events),
            "slow_requested":        len(requested),
            "slow_blocked":          len(blocked),
            "last_fast_refresh":     self._last_fast,
            "last_slow_update":      self._last_slow,
            "can_execute":           False,
            "no_auto_weight_update": True,
        }

    def events(self) -> list[dict[str, Any]]:
        return [
            {
                "channel": e.channel, "event_type": e.event_type,
                "triggered_at": e.triggered_at, "notes": e.notes,
            }
            for e in self._events
        ]
