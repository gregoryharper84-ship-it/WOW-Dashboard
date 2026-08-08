"""
invalidation.py — Material-change detection and invalidation tracking.

InvalidationTracker maintains a per-(player, event_id) snapshot of the
last-seen projected minutes mode, lineup status, event status, and board line.

When any material change is detected, needs_rerun=True is set and the row's
acquisition report is stamped with an invalidation_reason.

Material changes:
  - projected_minutes_mode changes >15% from last snapshot
  - lineup_status changes (any transition)
  - event_status changes (especially status → cancelled/postponed)
  - board_line changes by any amount (exact identity check)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import OpportunityState, LineupStatus


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class InvalidationResult:
    needs_rerun:        bool
    invalidation_reason: str | None  = None
    change_description:  str | None  = None
    prior_snapshot:      dict[str, Any] | None = None
    can_execute:         bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute":          False,
            "needs_rerun":          self.needs_rerun,
            "invalidation_reason":  self.invalidation_reason,
            "change_description":   self.change_description,
            "prior_snapshot":       self.prior_snapshot,
        }


# ---------------------------------------------------------------------------
# Per-row snapshot (stored in memory)
# ---------------------------------------------------------------------------

@dataclass
class _Snapshot:
    projected_minutes_mode: float | None
    lineup_status:          str
    event_status:           str | None
    board_line:             float | None


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class InvalidationTracker:
    """
    Maintains in-memory snapshots for (player, event_id) pairs.
    Thread safety: not guaranteed (same worker only).
    Stateless across gunicorn worker boundaries — each worker tracks independently.
    """

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], _Snapshot] = {}
        self.can_execute: bool = False   # unconditional

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------

    def check_and_invalidate(
        self,
        row: dict[str, Any],
        new_opportunity_state: OpportunityState,
        new_board_line: float | None = None,
    ) -> InvalidationResult:
        """
        Compare the current opportunity state to the last-seen snapshot.

        Returns InvalidationResult.  If needs_rerun=True the caller must
        reacquire, rerun composite simulation, rerun market comparison,
        rerun dynamic calibration, and stamp the row's acquisition report.
        """
        player   = (row.get("player") or row.get("team") or "").lower().strip()
        event_id = (row.get("event_id") or row.get("game_id") or "").lower().strip()

        if not player or not event_id:
            return InvalidationResult(
                needs_rerun=False,
                invalidation_reason=None,
                change_description="NO_KEY: player or event_id missing; cannot track",
            )

        key = (player, event_id)

        new_minutes_mode = (
            new_opportunity_state.minutes_distribution.mode
            if new_opportunity_state.minutes_distribution else None
        )
        new_lineup  = new_opportunity_state.lineup_status.value
        new_event   = row.get("event_status") or "unknown"
        new_line    = new_board_line

        if key not in self._snapshots:
            # First time seeing this row — store snapshot, no invalidation
            self._snapshots[key] = _Snapshot(
                projected_minutes_mode = new_minutes_mode,
                lineup_status          = new_lineup,
                event_status           = new_event,
                board_line             = new_line,
            )
            return InvalidationResult(
                needs_rerun=False,
                invalidation_reason=None,
                change_description="FIRST_SEEN: no prior snapshot; recording baseline",
            )

        prior = self._snapshots[key]
        changes: list[str] = []

        # Check minutes change
        if prior.projected_minutes_mode is not None and new_minutes_mode is not None:
            ref = prior.projected_minutes_mode
            if ref > 0:
                rel_change = abs(new_minutes_mode - ref) / ref
                if rel_change > 0.15:
                    changes.append(
                        f"MINUTES_CHANGE: {ref:.1f}→{new_minutes_mode:.1f} "
                        f"({rel_change*100:.1f}% relative)"
                    )

        # Check lineup status change
        if prior.lineup_status != new_lineup:
            changes.append(f"LINEUP_STATUS_CHANGE: {prior.lineup_status}→{new_lineup}")

        # Check event status change
        if prior.event_status != new_event:
            changes.append(f"EVENT_STATUS_CHANGE: {prior.event_status}→{new_event}")

        # Check board line change
        if prior.board_line is not None and new_line is not None:
            if abs(prior.board_line - new_line) > 1e-6:
                changes.append(f"BOARD_LINE_CHANGE: {prior.board_line}→{new_line}")
        elif prior.board_line is None and new_line is not None:
            changes.append(f"BOARD_LINE_APPEARED: {new_line}")
        elif prior.board_line is not None and new_line is None:
            changes.append(f"BOARD_LINE_DISAPPEARED: was {prior.board_line}")

        # -----------------------------------------------------------------------
        # Determine if change is material
        # -----------------------------------------------------------------------
        if changes:
            # Update snapshot with new values
            self._snapshots[key] = _Snapshot(
                projected_minutes_mode = new_minutes_mode,
                lineup_status          = new_lineup,
                event_status           = new_event,
                board_line             = new_line,
            )
            prior_snap = {
                "projected_minutes_mode": prior.projected_minutes_mode,
                "lineup_status":          prior.lineup_status,
                "event_status":           prior.event_status,
                "board_line":             prior.board_line,
            }
            reason = "MATERIAL_CHANGE:" + "|".join(changes[:3])
            return InvalidationResult(
                needs_rerun=True,
                invalidation_reason=reason,
                change_description=" | ".join(changes),
                prior_snapshot=prior_snap,
            )

        return InvalidationResult(
            needs_rerun=False,
            invalidation_reason=None,
            change_description="CLEAN: no material change detected",
        )

    # -----------------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------------

    def clear(self, player: str = "", event_id: str = "") -> None:
        """Manually evict a key from the snapshot store."""
        key = (player.lower().strip(), event_id.lower().strip())
        self._snapshots.pop(key, None)

    def snapshot_count(self) -> int:
        return len(self._snapshots)
