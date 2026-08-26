"""
gate_engine/universal_agent/model_validation/health_state.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Model Health State Machine.

States (ordered by severity):
  HEALTHY      Normal operation. All checks passing.
  DEGRADED     Some checks failing; model still running with monitoring.
  SUSPENDED    Model halted temporarily; requires manual resume.
  QUARANTINED  Under active investigation; requires manual clearance.

Allowed transitions (explicit only — never automatic):
  HEALTHY    → DEGRADED    (trigger: drift ALERT or Brier threshold exceeded)
  DEGRADED   → SUSPENDED   (trigger: manual or drift ALERT sustained)
  DEGRADED   → HEALTHY     (trigger: manual clearance after investigation)
  SUSPENDED  → DEGRADED    (trigger: manual resume with caveats)
  SUSPENDED  → QUARANTINED (trigger: manual escalation)
  QUARANTINED → SUSPENDED  (trigger: manual partial clearance)
  ANY        → QUARANTINED (trigger: explicit manual escalation)

No automatic state transitions. All transitions require a reason string.
can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


class HealthState:
    HEALTHY      = "HEALTHY"
    DEGRADED     = "DEGRADED"
    SUSPENDED    = "SUSPENDED"
    QUARANTINED  = "QUARANTINED"

    _ALL = frozenset({HEALTHY, DEGRADED, SUSPENDED, QUARANTINED})

    # (from_state, to_state) pairs that are explicitly allowed
    _ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
        (HEALTHY,     DEGRADED),
        (DEGRADED,    HEALTHY),
        (DEGRADED,    SUSPENDED),
        (SUSPENDED,   DEGRADED),
        (SUSPENDED,   QUARANTINED),
        (QUARANTINED, SUSPENDED),
        # any → QUARANTINED is always allowed (escalation)
        (HEALTHY,     QUARANTINED),
        (DEGRADED,    QUARANTINED),
    })


@dataclass
class HealthEvent:
    """One recorded state transition."""
    from_state: str
    to_state:   str
    reason:     str
    timestamp:  str


class ModelHealthStateMachine:
    """
    Per-model health state machine.
    All transitions are explicit and require a reason string.
    can_execute = False.
    """

    def __init__(self, model_id: str) -> None:
        self.model_id     = model_id
        self._state       = HealthState.HEALTHY
        self._history:    list[HealthEvent] = []

    @property
    def state(self) -> str:
        return self._state

    def transition(self, to_state: str, *, reason: str) -> None:
        """
        Transition to a new state. Raises ValueError on invalid transition
        or missing reason.
        """
        if not reason or not reason.strip():
            raise ValueError("Health transition requires a non-empty reason string.")
        if to_state not in HealthState._ALL:
            raise ValueError(f"Unknown health state: {to_state!r}")

        pair = (self._state, to_state)
        if to_state != HealthState.QUARANTINED and pair not in HealthState._ALLOWED_TRANSITIONS:
            raise ValueError(
                f"Invalid health transition {self._state!r} → {to_state!r}. "
                f"Allowed: {sorted(HealthState._ALLOWED_TRANSITIONS)}"
            )

        event = HealthEvent(
            from_state=self._state,
            to_state=to_state,
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._history.append(event)
        self._state = to_state

    def is_operational(self) -> bool:
        """True only when HEALTHY or DEGRADED (model is running)."""
        return self._state in (HealthState.HEALTHY, HealthState.DEGRADED)

    def summary(self) -> dict[str, Any]:
        return {
            "model_id":     self.model_id,
            "state":        self._state,
            "operational":  self.is_operational(),
            "event_count":  len(self._history),
            "last_event":   vars(self._history[-1]) if self._history else None,
            "can_execute":  False,
        }

    def history(self) -> list[dict[str, Any]]:
        return [vars(e) for e in self._history]
