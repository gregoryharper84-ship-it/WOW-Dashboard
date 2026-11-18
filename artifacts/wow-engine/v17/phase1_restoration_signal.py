"""Evidence-only Phase 1 restoration signals from genuine Daily reconciliation.

This module never scores, certifies, publishes or authorizes sporting probabilities.
A completed monitoring job is not a production restoration receipt.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
        **{key: values[key] for key in COUNT_KEYS},
        "action_required": total > 0 and completed < total,
        "restoration_status": "WORKING_NOT_COMPLETE",
        "model_certification_claimed": False,
        "independent_acceptance_claimed": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
