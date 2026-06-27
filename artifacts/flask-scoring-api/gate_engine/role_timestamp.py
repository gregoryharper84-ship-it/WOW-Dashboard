"""
role_timestamp.py  —  Module E: Role Timestamp Enforcement
WOW v16 / Section 8.1

Role/status check and data timestamp are SEPARATE required fields.
A single data_timestamp does NOT satisfy the role_timestamp requirement.

Required fields:
  status_timestamp               — when official injury/status was last confirmed
  role_timestamp                 — when role/minutes/deployment was last confirmed
  primary_teammate_status_timestamp — when key teammate status was last confirmed
  role_confirmation_age_minutes  — calculated: game_time minus role_timestamp
  tip_or_first_pitch_time        — scheduled start time

Staleness rules:
  ≤ 90 min   → FRESH      — passes, no penalty
  91–120 min → RECHECK    — Watch / recheck required before approval
  > 120 min  → STALE      — cap at MODEL_QUALIFIED_HOLD; live final-lock required
  Cannot calculate → UNKNOWN — role check FAILED, cap at MODEL_QUALIFIED_HOLD
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Staleness thresholds (minutes)
# ---------------------------------------------------------------------------
FRESH_THRESHOLD   = 90    # ≤ 90 min → FRESH
RECHECK_THRESHOLD = 120   # 91–120 → RECHECK
# > 120 → STALE

STALENESS_LABELS = {
    "FRESH":   "FRESH",
    "RECHECK": "RECHECK",
    "STALE":   "STALE",
    "UNKNOWN": "UNKNOWN",
}

# Label applied to row when role check fails/is stale
ROLE_STALE_CAP = PropLabel.MODEL_QUALIFIED_HOLD.value


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string to a UTC datetime, or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    s = str(value).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _age_minutes(ts: datetime | None, now: datetime | None = None) -> float | None:
    """Return age in minutes of a timestamp relative to now (UTC)."""
    if ts is None:
        return None
    ref = now or datetime.now(timezone.utc)
    diff = ref - ts
    return diff.total_seconds() / 60


def _staleness_grade(age_minutes: float | None) -> str:
    """Classify age into FRESH / RECHECK / STALE / UNKNOWN."""
    if age_minutes is None:
        return "UNKNOWN"
    if age_minutes <= FRESH_THRESHOLD:
        return "FRESH"
    if age_minutes <= RECHECK_THRESHOLD:
        return "RECHECK"
    return "STALE"


def run(row: dict[str, Any], enrichment: dict[str, Any] | None = None,
        now: datetime | None = None) -> dict[str, Any]:
    """
    Check role and status timestamps for staleness.
    Modifies row in-place (blockers, gates["role_timestamp"]).
    Does NOT set terminal_label — staleness caps are advisory unless the
    pipeline classifier respects the ceiling field.

    Returns:
        {
          passed:                        bool
          role_staleness:                "FRESH"|"RECHECK"|"STALE"|"UNKNOWN"
          status_staleness:              "FRESH"|"RECHECK"|"STALE"|"UNKNOWN"
          teammate_staleness:            "FRESH"|"RECHECK"|"STALE"|"UNKNOWN"|"N/A"
          role_confirmation_age_minutes: float | None
          status_age_minutes:            float | None
          teammate_age_minutes:          float | None
          tip_time:                      str | None
          ceiling:                       str | None   (PropLabel value when cap applies)
          code:                          str
          detail:                        str
        }
    """
    enr = enrichment or {}
    ref_now = now or datetime.now(timezone.utc)

    role_ts_raw      = row.get("role_timestamp") or enr.get("role_timestamp")
    status_ts_raw    = row.get("status_timestamp") or enr.get("status_timestamp")
    teammate_ts_raw  = row.get("primary_teammate_status_timestamp") or \
                       enr.get("primary_teammate_status_timestamp")
    tip_raw          = row.get("tip_or_first_pitch_time") or \
                       enr.get("tip_or_first_pitch_time")
    age_override     = row.get("role_confirmation_age_minutes") or \
                       enr.get("role_confirmation_age_minutes")

    role_ts     = _parse_ts(role_ts_raw)
    status_ts   = _parse_ts(status_ts_raw)
    teammate_ts = _parse_ts(teammate_ts_raw)

    # role_confirmation_age_minutes: override wins if explicitly supplied
    if age_override is not None:
        try:
            role_age = float(age_override)
        except (TypeError, ValueError):
            role_age = _age_minutes(role_ts, ref_now)
    else:
        role_age = _age_minutes(role_ts, ref_now)

    status_age   = _age_minutes(status_ts, ref_now)
    teammate_age = _age_minutes(teammate_ts, ref_now) if teammate_ts else None

    role_grade     = _staleness_grade(role_age)
    status_grade   = _staleness_grade(status_age)
    teammate_grade = (
        _staleness_grade(teammate_age)
        if teammate_ts is not None
        else "N/A"
    )

    # Determine worst grade across role + status
    grade_rank = {"FRESH": 0, "RECHECK": 1, "STALE": 2, "UNKNOWN": 2, "N/A": -1}
    worst_role_status = max(role_grade, status_grade,
                            key=lambda g: grade_rank.get(g, 0))

    ceiling: str | None = None
    code = "ROLE_TIMESTAMP_FRESH"
    blockers: list[str] = []

    if worst_role_status == "UNKNOWN":
        ceiling = ROLE_STALE_CAP
        code    = "ROLE_TIMESTAMP_UNKNOWN"
        blockers.append(
            f"ROLE_TIMESTAMP:UNKNOWN:cannot_calculate_age "
            f"(role_ts={'missing' if role_ts is None else 'present'}, "
            f"status_ts={'missing' if status_ts is None else 'present'})"
        )
    elif worst_role_status == "STALE":
        ceiling = ROLE_STALE_CAP
        code    = "ROLE_TIMESTAMP_STALE"
        blockers.append(
            f"ROLE_TIMESTAMP:STALE:role_age={role_age:.0f}min "
            f"status_age={status_age:.0f}min (>{RECHECK_THRESHOLD}min cap)"
        )
    elif worst_role_status == "RECHECK":
        code = "ROLE_TIMESTAMP_RECHECK"
        blockers.append(
            f"ROLE_TIMESTAMP:RECHECK:role_age={role_age:.0f}min "
            f"(>{FRESH_THRESHOLD}min — recheck required before approval)"
        )

    passed = worst_role_status in ("FRESH", "RECHECK") and ceiling is None

    result: dict[str, Any] = {
        "passed":                        passed,
        "role_staleness":                role_grade,
        "status_staleness":              status_grade,
        "teammate_staleness":            teammate_grade,
        "role_confirmation_age_minutes": round(role_age, 1) if role_age is not None else None,
        "status_age_minutes":            round(status_age, 1) if status_age is not None else None,
        "teammate_age_minutes":          round(teammate_age, 1) if teammate_age is not None else None,
        "tip_time":                      str(tip_raw) if tip_raw else None,
        "ceiling":                       ceiling,
        "code":                          code,
        "detail":                        _build_detail(role_grade, status_grade,
                                                        teammate_grade, role_age,
                                                        status_age, ceiling),
    }

    row.setdefault("gates", {})["role_timestamp"] = result
    for b in blockers:
        row["blockers"].append(b)

    # Apply ceiling cap to row's terminal label if not already terminal
    if ceiling and not row.get("terminal_label"):
        row["label_ceiling"] = ceiling

    return result


def _build_detail(role_g: str, status_g: str, teammate_g: str,
                  role_age: float | None, status_age: float | None,
                  ceiling: str | None) -> str:
    parts = []
    if role_age is not None:
        parts.append(f"role={role_age:.0f}min({role_g})")
    else:
        parts.append(f"role=unknown({role_g})")
    if status_age is not None:
        parts.append(f"status={status_age:.0f}min({status_g})")
    else:
        parts.append(f"status=unknown({status_g})")
    if teammate_g != "N/A":
        parts.append(f"teammate={teammate_g}")
    if ceiling:
        parts.append(f"cap={ceiling}")
    return "role_timestamp: " + " | ".join(parts)
