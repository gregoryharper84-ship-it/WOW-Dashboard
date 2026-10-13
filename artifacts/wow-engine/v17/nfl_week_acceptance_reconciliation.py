"""Non-publishing, date-partitioned NFL week acceptance evidence gate.

This module never authenticates schedule data, certifies a model, calls a
production Action, or grants publication/execution authority. Caller must
supply verified expected event IDs and independently authenticated receipts.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

TERMINAL = "V17_TERMINAL_REDUCER"


def _held(code: str) -> dict[str, Any]:
    return {"status": "WEEK_ACCEPTANCE_HELD", "blockers": [code],
            "can_execute": False, "probability_publishable": False,
            "global_terminal_authority": TERMINAL}


def reconcile_week(
    expected_by_date: Mapping[str, Sequence[str]],
    daily_receipts: Mapping[str, Mapping[str, Any]],
    *,
    minimum_unique_events: int = 13,
) -> dict[str, Any]:
    """Validate exact-once daily coverage, not model or Action certification."""
    if type(minimum_unique_events) is not int or minimum_unique_events < 13:
        return _held("NFL_WEEK_THRESHOLD_INVALID")
    if not isinstance(expected_by_date, Mapping) or not expected_by_date:
        return _held("NFL_WEEK_EXPECTED_DATES_MISSING")
    if not isinstance(daily_receipts, Mapping) or set(daily_receipts) != set(expected_by_date):
        return _held("NFL_WEEK_DAILY_RECEIPT_COVERAGE_MISMATCH")
    expected_all: list[str] = []
    for day, ids in expected_by_date.items():
        if not isinstance(day, str) or not isinstance(ids, (tuple, list)) or not ids:
            return _held("NFL_WEEK_DATE_OR_EVENTS_INVALID")
        if any(not isinstance(event_id, str) or not event_id.strip() for event_id in ids):
            return _held("NFL_WEEK_EVENT_ID_INVALID")
        expected_all.extend(ids)
    if len(expected_all) < minimum_unique_events or len(set(expected_all)) != len(expected_all):
        return _held("NFL_WEEK_INCOMPLETE_OR_DUPLICATE_EXPECTED")
    observed_all: list[str] = []
    for day, ids in expected_by_date.items():
        receipt = daily_receipts[day]
        if not isinstance(receipt, Mapping) or receipt.get("requested_slate_date") != day:
            return _held("NFL_WEEK_RESPONSE_DATE_MISMATCH")
        if receipt.get("run_status") != "COMPLETE":
            return _held("NFL_WEEK_RESPONSE_INCOMPLETE")
        if receipt.get("can_execute") is not False or receipt.get("global_terminal_authority") != TERMINAL:
            return _held("NFL_WEEK_RESPONSE_GOVERNANCE_INVALID")
        reconciliation = receipt.get("reconciliation")
        cross = receipt.get("cross_sport_discovery_audit")
        cross = cross.get("reconciliation") if isinstance(cross, Mapping) else None
        if (not isinstance(reconciliation, Mapping) or reconciliation.get("balanced") is not True
                or not isinstance(cross, Mapping) or cross.get("row_reconciliation") != "PASS"
                or type(cross.get("events_discovered")) is not int
                or cross.get("events_discovered") != len(ids)
                or cross.get("events_accounted") != cross.get("events_discovered")):
            return _held("NFL_WEEK_DAILY_RECONCILIATION_INVALID")
        rows = receipt.get("rows")
        if not isinstance(rows, list):
            return _held("NFL_WEEK_ROWS_MISSING")
        observed: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                return _held("NFL_WEEK_ROW_MALFORMED")
            identity = row.get("identity")
            if not isinstance(identity, Mapping):
                return _held("NFL_WEEK_IDENTITY_MALFORMED")
            if row.get("lane") != "MONEYLINE" or identity.get("sport") != "NFL":
                continue
            event_id = identity.get("official_event_id")
            if not isinstance(event_id, str) or not event_id.strip() or row.get("can_execute") is not False:
                return _held("NFL_WEEK_ROW_IDENTITY_OR_GOVERNANCE_INVALID")
            if "MODEL_INVOCATION_BUDGET_REACHED" in str(row.get("result")):
                return _held("NFL_WEEK_MODEL_BUDGET_EXHAUSTED")
            observed.append(event_id)
        if len(observed) != len(ids) or set(observed) != set(ids):
            return _held("NFL_WEEK_DAILY_EXACT_ONCE_FAILED")
        observed_all.extend(observed)
    if len(observed_all) != len(expected_all) or set(observed_all) != set(expected_all):
        return _held("NFL_WEEK_EXACT_ONCE_FAILED")
    return {"status": "WEEK_COVERAGE_RECONCILED_NOT_ACTION_CERTIFIED",
            "blockers": [], "unique_event_count": len(observed_all),
            "can_execute": False, "probability_publishable": False,
            "global_terminal_authority": TERMINAL}
