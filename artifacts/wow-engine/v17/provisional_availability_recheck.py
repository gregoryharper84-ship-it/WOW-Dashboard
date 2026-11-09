"""Final pregame recheck for provisional official participation.

An MLB probable-starter listing is official but provisional: the club has named
the player for the game without confirming he will start. The gate therefore
admits the row to research scoring only and records
``PLAYER_AVAILABILITY_RECHECK_REQUIRED``.

This module decides what a fresh official observation means for that
obligation. It is deliberately fail-closed and small:

- only a typed ``CONFIRMED_STARTER`` observation can discharge the obligation;
- a re-read that is stale, incomplete, unretrievable, identity-mismatched, or
  still ``PROBABLE_STARTER`` leaves the recheck blocker in place;
- an explicit rule-out becomes a normal ``PLAYER_RULED_OUT`` ruling.

No probability, line, or calibration value is read or changed here, and
``can_execute`` remains false.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_UNVERIFIED,
    AvailabilityDecision,
    PLAYER_AVAILABILITY_RECHECK_REQUIRED,
    evaluate_player_availability,
)

CAN_EXECUTE = False

# The final refresh rechecks at most this long before the scheduled start. The
# window is deliberately short: a recheck days early re-reads the same
# provisional listing and proves nothing new.
RECHECK_WINDOW = timedelta(hours=6)

RECHECK_CONFIRMED = "CONFIRMED"
RECHECK_RULED_OUT = "RULED_OUT"
RECHECK_INCONCLUSIVE = "INCONCLUSIVE"
RECHECK_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
RECHECK_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
RECHECK_NOT_DUE = "NOT_DUE"

CONFIRMED_STARTER_BASIS = "CONFIRMED_STARTER"


def recheck_is_due(event_start: datetime, now: datetime) -> bool:
    """True when the event is still pregame and inside the recheck window."""
    remaining = event_start - now
    return timedelta(0) < remaining <= RECHECK_WINDOW


def _identity(block: Mapping[str, Any]) -> dict[str, str]:
    provenance = block.get("provenance")
    nested = provenance if isinstance(provenance, Mapping) else {}
    return {
        field: str(block.get(field) or nested.get(field) or "").strip()
        for field in ("athlete_id", "event_id", "team")
    }


def _receipt(
    *,
    status: str,
    observed: Mapping[str, Any],
    reason: str,
    mismatched: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "checked_at": now.isoformat() if now is not None else None,
        "source": str(observed.get("source") or "").strip() or None,
        "captured_at": str(observed.get("captured_at") or "").strip() or None,
        "basis": str(observed.get("basis") or "").strip() or None,
        "confirmation": str(observed.get("confirmation") or "").strip() or None,
        "evidence_status": str(observed.get("evidence_status") or "").strip() or None,
        "mismatched_fields": list(mismatched or []),
        "recheck_window_hours": RECHECK_WINDOW.total_seconds() / 3600,
        "can_execute": CAN_EXECUTE,
    }


def _hold(
    observed: Mapping[str, Any],
    *,
    status: str,
    reason: str,
    mismatched: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[AvailabilityDecision, dict[str, Any]]:
    """Keep the recheck obligation in place; never read as clearance."""
    decision = AvailabilityDecision(
        status=AVAILABILITY_UNVERIFIED,
        designation=str(observed.get("designation") or "").strip() or None,
        source=str(observed.get("source") or "").strip() or None,
        captured_at=str(observed.get("captured_at") or "").strip() or None,
        evidence_status=str(observed.get("evidence_status") or "").strip().upper() or "NOT_SUPPLIED",
        risk=False,
        blocker=PLAYER_AVAILABILITY_RECHECK_REQUIRED,
        reason=reason,
        basis=str(observed.get("basis") or "").strip() or None,
        confirmation=str(observed.get("confirmation") or "").strip() or None,
        fresh=observed.get("fresh"),
        report_complete=observed.get("report_complete"),
        provenance={**_identity(observed), "mismatched_fields": list(mismatched or [])},
        requires_revalidation=True,
    )
    return decision, _receipt(
        status=status,
        observed=observed,
        reason=reason,
        mismatched=mismatched,
        now=now,
    )


def resolve_provisional_recheck(
    *,
    provisional: Mapping[str, Any],
    observed: Mapping[str, Any],
    now: datetime | None = None,
) -> tuple[AvailabilityDecision, dict[str, Any]]:
    """Decide whether a fresh official observation discharges the obligation.

    ``provisional`` is the gate receipt that produced
    ``requires_revalidation``; ``observed`` is a fresh availability block from
    the same official source. Identity must match on every field both sides
    supply, so a recheck of a different player, game, or club can never clear
    this row.
    """
    provisional_identity = _identity(provisional)
    observed_identity = _identity(observed)
    mismatched = [
        field
        for field in ("athlete_id", "event_id", "team")
        if provisional_identity[field]
        and observed_identity[field]
        and provisional_identity[field] != observed_identity[field]
    ]
    if mismatched:
        return _hold(
            observed,
            status=RECHECK_IDENTITY_MISMATCH,
            reason="PROVISIONAL_RECHECK_IDENTITY_MISMATCH",
            mismatched=mismatched,
            now=now,
        )

    decision = evaluate_player_availability({"availability": dict(observed)})
    if decision.status == AVAILABILITY_UNAVAILABLE:
        return decision, _receipt(
            status=RECHECK_RULED_OUT,
            observed=observed,
            reason=decision.reason,
            now=now,
        )
    if (
        decision.status == AVAILABILITY_AVAILABLE
        and decision.basis == CONFIRMED_STARTER_BASIS
        and decision.requires_revalidation is False
    ):
        return decision, _receipt(
            status=RECHECK_CONFIRMED,
            observed=observed,
            reason="OFFICIAL_BASIS_CONFIRMED_STARTER",
            now=now,
        )
    return _hold(
        observed,
        status=RECHECK_INCONCLUSIVE,
        reason=f"PROVISIONAL_RECHECK_{decision.reason or 'NOT_CONFIRMED'}",
        now=now,
    )


__all__ = [
    "CAN_EXECUTE",
    "CONFIRMED_STARTER_BASIS",
    "RECHECK_CONFIRMED",
    "RECHECK_IDENTITY_MISMATCH",
    "RECHECK_INCONCLUSIVE",
    "RECHECK_NOT_DUE",
    "RECHECK_RULED_OUT",
    "RECHECK_SOURCE_UNAVAILABLE",
    "RECHECK_WINDOW",
    "recheck_is_due",
    "resolve_provisional_recheck",
]
