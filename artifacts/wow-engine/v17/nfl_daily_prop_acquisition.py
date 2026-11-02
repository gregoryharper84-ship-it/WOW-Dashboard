"""Adapt the existing NFL forward-evidence producer for bounded V17 Daily discovery.

This layer delegates to the reviewed ESPN pregame/roster/position discovery and
governed hydration. Discovery half-point thresholds are NOT sportsbook lines;
they cannot establish fitted probabilities, qualification, or execution.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from v17.nfl_prop_evidence_control_plane import (
    NFLForwardEvidenceRequest,
    PLATFORM,
    STAT_TYPES,
    acquire_nfl_forward_evidence_batch,
)

CAN_EXECUTE = False


def acquire_daily_nfl_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    candidate_offset: int = 0,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    """Return receipt-backed NFL snapshots with the same Daily producer contract."""
    # Keep the supplied calendar/timezone explicit; never infer a slate from
    # the backend server locale or allow a malformed timezone to select a day.
    ZoneInfo(requested_timezone)
    if not 0 <= int(max_candidates) <= 64:
        raise ValueError("NFL_DAILY_CANDIDATE_BUDGET_INVALID")
    if max_candidates == 0:
        return {
            "status": "COMPLETED", "sport": "NFL", "attempted": 0,
            "hydrated": 0, "persisted": 0, "held": 0, "already_captured": 0,
            "snapshot_write_succeeded": 0, "snapshot_write_failed": 0,
            "explicit_prewrite_exclusions": 0, "receipts": [],
            "blockers": [], "next_offset": None,
            "candidate_source": "NFL_ESPN_PREGAME_ROSTER",
            "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
            "stat_types": list(STAT_TYPES), "can_execute": False,
        }

    result = acquire_nfl_forward_evidence_batch(
        NFLForwardEvidenceRequest(
            requested_date=requested_date,
            candidate_offset=int(candidate_offset),
            max_candidates=int(max_candidates),
        ),
        db=db,
        now=now,
        http_get=http_get,
    )
    normalized = {
        **result,
        "candidate_source": "NFL_ESPN_PREGAME_ROSTER",
        "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
        "platform": PLATFORM,
        "source_type": "AUTONOMOUS_DISCOVERY",
        "snapshot_write_succeeded": int(result.get("persisted") or 0),
        "explicit_prewrite_exclusions": int(result.get("explicit_prewrite_exclusions") or 0),
        "receipts": list(result.get("receipts") or []),
        "can_execute": False,
    }
    return normalized
