"""
status_role.py
Track player status, role confidence, and role-split requirements.
Does NOT approve or reject — only flags for downstream gates.
"""
from __future__ import annotations

from typing import Any

from .labels import DataStatus


STATUS_VERIFIED   = "STATUS_VERIFIED"
PROBABLE_ONLY     = "PROBABLE_ONLY"
LINEUP_UNCONFIRMED = "LINEUP_UNCONFIRMED"
SOURCE_CONFLICT   = "SOURCE_CONFLICT"
STALE_STATUS      = "STALE_STATUS"
DNP_RISK          = "DNP_RISK"
MINUTES_RESTRICTED = "MINUTES_RESTRICTED"
ROLE_SPLIT_NEEDED = "ROLE_SPLIT_NEEDED"

CONFIDENCE_MAP = {
    STATUS_VERIFIED:    1.0,
    PROBABLE_ONLY:      0.7,
    LINEUP_UNCONFIRMED: 0.5,
    STALE_STATUS:       0.3,
    SOURCE_CONFLICT:    0.2,
    DNP_RISK:           0.1,
    MINUTES_RESTRICTED: 0.6,
}


def run(row: dict[str, Any], status_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Evaluate player status.

    status_payload (provided by caller from upstream data pull):
      status        str   — raw status string from source
      source        str   — source name
      confirmed_at  str   — ISO timestamp of last confirmation
      dnp_risk      bool
      minutes_restriction bool
      role_depends_on  str | None  — name of player this role depends on
      role_split_active bool

    Gate result at row["gates"]["status_role"].
    """
    if status_payload is None:
        result = {
            "passed": True,
            "status_label": LINEUP_UNCONFIRMED,
            "confidence": CONFIDENCE_MAP[LINEUP_UNCONFIRMED],
            "dnp_risk": False,
            "minutes_restricted": False,
            "role_split_needed": False,
            "role_depends_on": None,
            "source": None,
            "data_status": DataStatus.NOT_CALLED.value,
            "note": "No status payload provided — treated as LINEUP_UNCONFIRMED",
        }
        row["gates"]["status_role"] = result
        return row

    raw_status   = str(status_payload.get("status", "")).upper()
    source       = status_payload.get("source")
    dnp_risk     = bool(status_payload.get("dnp_risk", False))
    mins_rest    = bool(status_payload.get("minutes_restriction", False))
    role_depends = status_payload.get("role_depends_on")
    role_split   = bool(status_payload.get("role_split_active", False))
    stale        = bool(status_payload.get("stale", False))
    conflict     = bool(status_payload.get("source_conflict", False))

    if conflict:
        label = SOURCE_CONFLICT
    elif stale:
        label = STALE_STATUS
    elif dnp_risk:
        label = DNP_RISK
    elif mins_rest:
        label = MINUTES_RESTRICTED
    elif "OUT" in raw_status or "DOUBTFUL" in raw_status:
        label = DNP_RISK
        dnp_risk = True
    elif "QUESTIONABLE" in raw_status or "PROBABLE" in raw_status:
        label = PROBABLE_ONLY
    elif "CONFIRMED" in raw_status or "ACTIVE" in raw_status or "IN" in raw_status:
        label = STATUS_VERIFIED
    else:
        label = LINEUP_UNCONFIRMED

    if role_depends or role_split:
        role_split = True

    result = {
        "passed": True,
        "status_label":       label,
        "confidence":         CONFIDENCE_MAP.get(label, 0.5),
        "dnp_risk":           dnp_risk,
        "minutes_restricted": mins_rest,
        "role_split_needed":  role_split,
        "role_depends_on":    role_depends,
        "source":             source,
        "data_status":        DataStatus.RETRIEVED.value,
    }

    if dnp_risk:
        row["blockers"].append(f"STATUS:{label}:DNP_RISK")

    row["gates"]["status_role"] = result
    return row
