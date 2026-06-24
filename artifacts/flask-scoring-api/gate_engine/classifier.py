"""
classifier.py
Assign the terminal PropLabel to every row based on all gate results.
Hard rules enforced exactly per spec.
No row can be FINAL_APPROVED unless ALL gates pass.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel, DataStatus


REQUIRED_FOR_FINAL = [
    "slate_validation",
    "status_role",
    "l5_l10_ledger",
    "market_gate",
    "ev_gate",
    "slip_structure",
    "exposure_gate",
]


def classify(row: dict[str, Any]) -> dict[str, Any]:
    """
    Assign row["terminal_label"] based on gate chain results.
    Once a terminal label is set (e.g. SLATE_PURGE), it is NOT overridden.
    """
    if row.get("terminal_label") is not None:
        return row

    gates    = row.get("gates", {})
    blockers = row.get("blockers", [])

    if _has_data_failure(row):
        row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value
        row["blockers"].append("CLASSIFIER:REJECT_DATA_QUALITY:DATA_STATUS_FAILED")
        return row

    if _source_conflict(row):
        row["terminal_label"] = PropLabel.SOURCE_CONFLICT.value
        return row

    if not _gate_passed(gates, "slate_validation"):
        row["terminal_label"] = PropLabel.SLATE_PURGE.value
        return row

    if not _gate_passed(gates, "slip_structure"):
        row["terminal_label"] = PropLabel.REJECT_BAD_STRUCTURE.value
        return row

    if not _gate_passed(gates, "exposure_gate"):
        row["terminal_label"] = PropLabel.DUPLICATE_EXPOSURE_BLOCK.value
        return row

    if not _gate_passed(gates, "l5_l10_ledger"):
        row["terminal_label"] = PropLabel.RESEARCH_INTEREST.value
        return row

    ev = gates.get("ev_gate", {})
    market = gates.get("market_gate", {})
    mkt_status = market.get("market_status", "")

    no_market = mkt_status == "NO_MARKET_AVAILABLE"

    has_outlier_flags = gates.get("outlier_gate", {}).get("any_flag", False)

    if no_market:
        if has_outlier_flags:
            row["terminal_label"] = PropLabel.RESEARCH_INTEREST.value
        else:
            row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
        return row

    if not ev.get("money_qualified"):
        if ev.get("edge_score") is not None and ev["edge_score"] > 0:
            row["terminal_label"] = PropLabel.RESEARCH_INTEREST.value
        else:
            row["terminal_label"] = PropLabel.REJECT_NO_EDGE.value
        return row

    if mkt_status in ("MARKET_VERIFIED", "MARKET_EDGE_DETECTED"):
        if has_outlier_flags:
            row["terminal_label"] = PropLabel.MARKET_VERIFIED_HOLD.value
            row["blockers"].append("CLASSIFIER:MARKET_VERIFIED_HOLD:OUTLIER_FLAGS")
        elif _all_required_gates_passed(gates):
            row["terminal_label"] = PropLabel.FINAL_APPROVED.value
        else:
            row["terminal_label"] = PropLabel.MONEY_QUALIFIED.value
        return row

    row["terminal_label"] = PropLabel.MONEY_QUALIFIED.value
    return row


def _gate_passed(gates: dict, key: str) -> bool:
    g = gates.get(key, {})
    return bool(g.get("passed", False))


def _has_data_failure(row: dict) -> bool:
    if row.get("data_status") == DataStatus.FAILED.value:
        return True
    if row.get("data_status") == DataStatus.INPUT_FAILURE.value:
        return True
    for gate in row.get("gates", {}).values():
        if isinstance(gate, dict) and gate.get("data_status") == DataStatus.FAILED.value:
            return True
    return False


def _source_conflict(row: dict) -> bool:
    if row.get("data_status") == DataStatus.SOURCE_CONFLICT.value:
        return True
    for gate in row.get("gates", {}).values():
        if isinstance(gate, dict) and gate.get("data_status") == DataStatus.SOURCE_CONFLICT.value:
            return True
    return False


def _all_required_gates_passed(gates: dict) -> bool:
    return all(_gate_passed(gates, g) for g in REQUIRED_FOR_FINAL)
