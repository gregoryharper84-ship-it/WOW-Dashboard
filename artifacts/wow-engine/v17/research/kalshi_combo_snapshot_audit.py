"""Research-only economics audit for a partially visible Kalshi portfolio history.

No platform-date authentication, underlying-leg outcome inference, model calibration,
recommendation, or live wagering is performed. Zero runtime dependencies.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any


def _cents(row: dict[str, Any], name: str) -> int:
    value = row.get(name)
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer number of cents")
    return value


def _day(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("settlement_day must be ISO YYYY-MM-DD or null")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("settlement_day must be ISO YYYY-MM-DD")
    return value


def audit_snapshot(rows: list[dict[str, Any]], *, target_day: str | None = None) -> dict[str, Any]:
    """Reconcile economics; never infer a dated cohort or leg success from UI payouts.

    Inputs require underlying_market_count, entry_cents and payout_cents. Optional:
    settlement_day, position_id, market_details. Missing facts stay unknown.
    If target_day is given, only explicitly date-tagged matching rows are included;
    missing-date rows remain in the excluded count, not assigned to the target day.
    A date tag alone does not authenticate the platform settlement timestamp.
    """
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if target_day is not None:
        _day(target_day)
    seen_ids: set[str] = set()
    included: list[dict[str, Any]] = []
    missing_date = missing_details = missing_id = cash_return_loss = 0
    excluded_from_target = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be an object")
        count = row.get("underlying_market_count")
        if type(count) is not int or count < 1:
            raise ValueError("underlying_market_count must be a positive integer")
        entry = _cents(row, "entry_cents")
        returned = _cents(row, "payout_cents")
        settled_day = _day(row.get("settlement_day"))
        identifier = row.get("position_id")
        if identifier is None or identifier == "":
            missing_id += 1
        elif not isinstance(identifier, str):
            raise ValueError("position_id must be a string")
        elif identifier in seen_ids:
            raise ValueError("duplicate source position_id: " + identifier)
        else:
            seen_ids.add(identifier)
        details = row.get("market_details")
        if details is None:
            missing_details += 1
        elif not isinstance(details, list) or len(details) != count:
            raise ValueError("market_details must have exactly underlying_market_count entries")
        if settled_day is None:
            missing_date += 1
        if target_day is not None and settled_day != target_day:
            excluded_from_target += 1
            continue
        net = returned - entry
        if returned > 0 and net < 0:
            cash_return_loss += 1
        included.append({"count": count, "entry": entry, "payout": returned, "net": net})

    buckets: dict[int, dict[str, int]] = defaultdict(
        lambda: {"positions": 0, "profitable_positions": 0, "entry_cents": 0,
                 "payout_cents": 0, "net_cents": 0})
    for row in included:
        group = buckets[row["count"]]
        group["positions"] += 1
        group["profitable_positions"] += int(row["net"] > 0)
        group["entry_cents"] += row["entry"]
        group["payout_cents"] += row["payout"]
        group["net_cents"] += row["net"]

    return {
        "analysis_stage": "POSTMORTEM_RESEARCH",
        "scope": "SNAPSHOT" if target_day is None else "SOURCE_REPORTED_DAY_NOT_AUTHENTICATED",
        "target_day": target_day,
        "snapshot_rows": len(rows),
        "included_rows": len(included),
        "excluded_from_target_rows": excluded_from_target,
        "missing_settlement_day_rows": missing_date,
        "missing_market_details_rows": missing_details,
        "missing_stable_position_id_rows": missing_id,
        "positive_payout_negative_net_rows": cash_return_loss,
        "entry_cents": sum(r["entry"] for r in included),
        "payout_cents": sum(r["payout"] for r in included),
        "net_cents": sum(r["net"] for r in included),
        "profitable_positions": sum(r["net"] > 0 for r in included),
        "by_market_count": {str(k): buckets[k] for k in sorted(buckets)},
        "leg_outcomes": "UNVERIFIED_DO_NOT_INFER",
        "joint_probabilities": "NOT_AVAILABLE_DO_NOT_BACKFILL",
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
