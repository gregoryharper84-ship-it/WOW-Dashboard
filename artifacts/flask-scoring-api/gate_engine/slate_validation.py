"""
slate_validation.py
Confirm the player/team is on today's (or the target) slate.
Rows that fail slate validation get SLATE_PURGE and are terminal.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .labels import DataStatus, PropLabel


def run(row: dict[str, Any], target_date: date | None = None) -> dict[str, Any]:
    """
    Validates that slate_date on the row matches target_date.
    If target_date is None, uses today (UTC).

    Gate result stored at row["gates"]["slate_validation"]:
      passed   bool
      reason   str
      target   str  (ISO date)
      found    str | None
    """
    td = target_date or datetime.now(timezone.utc).date()
    target_iso = td.isoformat()

    slate_raw = row.get("slate_date")

    if not slate_raw:
        result = {
            "passed": False,
            "reason": "NO_SLATE_DATE",
            "target": target_iso,
            "found":  None,
        }
        row["data_status"] = DataStatus.FAILED.value
        _apply_failure(row, result, "SLATE_PURGE:NO_SLATE_DATE")
        return row

    try:
        found_date = _parse_date(slate_raw)
    except ValueError:
        result = {
            "passed": False,
            "reason": "UNPARSEABLE_SLATE_DATE",
            "target": target_iso,
            "found":  slate_raw,
        }
        row["data_status"] = DataStatus.FAILED.value
        _apply_failure(row, result, f"SLATE_PURGE:UNPARSEABLE_DATE:{slate_raw}")
        return row

    if found_date != td:
        result = {
            "passed": False,
            "reason": "DATE_MISMATCH",
            "target": target_iso,
            "found":  found_date.isoformat(),
        }
        _apply_failure(row, result, f"SLATE_PURGE:DATE_MISMATCH:{found_date.isoformat()}")
        return row

    result = {
        "passed": True,
        "reason": "DATE_CONFIRMED",
        "target": target_iso,
        "found":  found_date.isoformat(),
    }
    row["gates"]["slate_validation"] = result
    return row


def _apply_failure(row: dict[str, Any], result: dict, blocker: str) -> None:
    row["gates"]["slate_validation"] = result
    row["blockers"].append(blocker)
    row["terminal_label"] = PropLabel.SLATE_PURGE.value


def _parse_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw}")
