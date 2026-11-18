"""Evidence-only Phase 1 restoration signals from genuine Daily reconciliation.

This module never scores, certifies, publishes or authorizes sporting probabilities.
A completed monitoring job is not a production restoration receipt.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STATUS_KEY = {
    "COMPLETED": "rows_completed",
    "HELD": "rows_held",
    "REJECTED": "rows_rejected",
    "PURGED": "rows_purged",
    "INVALID": "rows_invalid",
}
COUNT_KEYS = (
    "rows_completed", "rows_held", "rows_rejected",
    "rows_purged", "rows_invalid", "rows_unclassified",
)


def classify_refresh_run(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Describe observed scoring progress without self-certifying restoration."""
    if not isinstance(receipt, Mapping):
        raise ValueError("RESTORATION_RECEIPT_INVALID")
    if receipt.get("can_execute") is not False:
        raise ValueError("RESTORATION_EXECUTION_LOCK_INVALID")
    if receipt.get("global_terminal_authority") != "V17_TERMINAL_REDUCER":
        raise ValueError("RESTORATION_TERMINAL_AUTHORITY_INVALID")
    if receipt.get("run_status") not in {"COMPLETED", "COMPLETED_WITH_ACQUISITION_BLOCKERS"}:
        raise ValueError("RESTORATION_RUN_STATUS_INVALID")
    reconciliation = receipt.get("reconciliation")
    if not isinstance(reconciliation, Mapping) or reconciliation.get("balanced") is not True:
        raise ValueError("RESTORATION_RECONCILIATION_INVALID")

    values: dict[str, int] = {}
    for key in ("rows_in", *COUNT_KEYS):
        value = reconciliation.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("RESTORATION_COUNT_INVALID")
        values[key] = value
    if values["rows_in"] != sum(values[key] for key in COUNT_KEYS):
        raise ValueError("RESTORATION_COUNTS_UNBALANCED")

    # The real COMPACT Daily response includes the full terminal row manifest.
    # Reconcile per-sport observations against the exact global buckets rather
    # than letting a successful sport conceal held rows in another sport.
    breakdown: dict[str, dict[str, int | str]] = {}
    rows = receipt.get("rows")
    if rows is None and "rows" not in receipt:
        breakdown_status = "ROW_DETAILS_NOT_AVAILABLE"
    else:
        if not isinstance(rows, list) or len(rows) != values["rows_in"]:
            raise ValueError("RESTORATION_SPORT_RECONCILIATION_INVALID")
        observed = {key: 0 for key in COUNT_KEYS}
        for row in rows:
            if not isinstance(row, Mapping) or row.get("can_execute") is not False:
                raise ValueError("RESTORATION_SPORT_RECONCILIATION_INVALID")
            identity = row.get("identity")
            if not isinstance(identity, Mapping):
                raise ValueError("RESTORATION_SPORT_RECONCILIATION_INVALID")
            sport = str(identity.get("sport") or "").strip().upper() or "UNRESOLVED_SPORT"
            raw_status = row.get("row_status")
            if not isinstance(raw_status, str) or not raw_status.strip():
                raise ValueError("RESTORATION_SPORT_RECONCILIATION_INVALID")
            key = STATUS_KEY.get(raw_status.strip().upper(), "rows_unclassified")
            observed[key] += 1
            if sport not in breakdown:
                breakdown[sport] = {item: 0 for item in COUNT_KEYS}
            breakdown[sport][key] += 1
        if any(observed[key] != values[key] for key in COUNT_KEYS):
            raise ValueError("RESTORATION_SPORT_RECONCILIATION_INVALID")
        for stats in breakdown.values():
            n = sum(stats[key] for key in COUNT_KEYS)
            done = stats["rows_completed"]
            stats["rows_in"] = n
            stats["signal"] = (
                "ZERO_COMPLETED_ROWS" if done == 0 else
                "SCORING_OBSERVED_NOT_ACCEPTED" if done == n else
                "PARTIAL_SCORING"
            )
        breakdown_status = "AVAILABLE"

    completed = values["rows_completed"]
    total = values["rows_in"]
    if total == 0:
        signal = "NO_ROWS_OBSERVED"
    elif completed == 0:
        signal = "ZERO_COMPLETED_ROWS"
    elif completed < total:
        signal = "PARTIAL_SCORING"
    else:
        signal = "SCORING_OBSERVED_NOT_ACCEPTED"

    return {
        "phase": "PHASE_1_RESTORATION",
        "run_id": receipt.get("run_id"),
        "signal": signal,
        "rows_in": total,
        "run_status": receipt["run_status"],
        "acquisition_blocked": receipt["run_status"] == "COMPLETED_WITH_ACQUISITION_BLOCKERS",
        "sport_breakdown_status": breakdown_status,
        "sport_breakdown": dict(sorted(breakdown.items())),
        **{key: values[key] for key in COUNT_KEYS},
        "action_required": total > 0 and completed < total,
        "restoration_status": "WORKING_NOT_COMPLETE",
        "model_certification_claimed": False,
        "independent_acceptance_claimed": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
