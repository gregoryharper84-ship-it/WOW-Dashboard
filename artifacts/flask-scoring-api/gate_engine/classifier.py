"""
classifier.py
Assign the terminal PropLabel to every row based on all gate results.
Hard rules enforced exactly per spec.
No row can be FINAL_APPROVED unless ALL gates pass.

Phase 2 addition: cash threshold enforcement via market_gate.confidence_cap.
  confidence_cap == "MODEL_QUALIFIED_HOLD" → hard cap to MODEL_QUALIFIED_HOLD
  confidence_cap == "MONEY_QUALIFIED_MAX"  → soft cap to MONEY_QUALIFIED (not FINAL_APPROVED)
  confidence_cap == None / "NO_PP_THRESHOLDS" → no additional cap (legacy / exact-verified)

Phase 3 addition: injury decision tree enforcement via injury_decision_tree.injury_tree_status.
  DEPENDENCY_CONFLICT   → hard cap to MODEL_QUALIFIED_HOLD
  DEPENDENCY_UNRESOLVED → soft cap to MONEY_QUALIFIED (blocks FINAL_APPROVED)
  ROLE_STATE_STALE      → soft cap to MONEY_QUALIFIED (blocks FINAL_APPROVED)
  All other statuses    → no additional cap

Phase 2 and Phase 3 caps are applied together after the EV gate check, before
market routing. This ensures they fire regardless of market_status (including
MARKET_CONTRADICTION which would otherwise fall through to the fallback).
MODEL_QUALIFIED_HOLD is the most restrictive; it wins over MONEY_QUALIFIED_MAX.
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

    gates = row.get("gates", {})

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

    ev         = gates.get("ev_gate", {})
    market     = gates.get("market_gate", {})
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

    # ------------------------------------------------------------------
    # Phase 2 + Phase 3 caps — applied unconditionally after the EV check.
    #
    # This ensures confidence_cap (Phase 2, from market_gate) and
    # injury_tree_status (Phase 3) are enforced regardless of market_status.
    # MARKET_CONTRADICTION, SEVERE_DRIFT, etc. all route through here before
    # reaching the market-routing section below.
    #
    # Priority (most restrictive wins):
    #   MODEL_QUALIFIED_HOLD (Phase 2 or Phase 3) > MONEY_QUALIFIED_MAX (Phase 2) or
    #   DEPENDENCY_UNRESOLVED/ROLE_STATE_STALE (Phase 3)
    # ------------------------------------------------------------------
    confidence_cap = market.get("confidence_cap")
    cash_status    = market.get("cash_threshold_status", "")
    inj            = gates.get("injury_decision_tree", {})
    inj_status     = inj.get("injury_tree_status", "")

    hard_cap = (
        confidence_cap == "MODEL_QUALIFIED_HOLD"
        or inj_status == "DEPENDENCY_CONFLICT"
    )
    soft_cap = (
        confidence_cap == "MONEY_QUALIFIED_MAX"
        or inj_status in ("DEPENDENCY_UNRESOLVED", "ROLE_STATE_STALE")
    )

    if hard_cap:
        row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
        if confidence_cap == "MODEL_QUALIFIED_HOLD":
            row["blockers"].append(f"CLASSIFIER:MARKET_CASH_CAP:{cash_status}")
        if inj_status == "DEPENDENCY_CONFLICT":
            row["blockers"].append(f"CLASSIFIER:INJURY_TREE_CAP:{inj_status}")
        return row

    if soft_cap:
        row["terminal_label"] = PropLabel.MONEY_QUALIFIED.value
        if confidence_cap == "MONEY_QUALIFIED_MAX":
            row["blockers"].append(f"CLASSIFIER:MARKET_CASH_CAP:{cash_status}")
        if inj_status in ("DEPENDENCY_UNRESOLVED", "ROLE_STATE_STALE"):
            row["blockers"].append(f"CLASSIFIER:INJURY_TREE_CAP:{inj_status}")
        return row

    # ------------------------------------------------------------------
    # No Phase 2 or Phase 3 cap — normal market routing
    # ------------------------------------------------------------------
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
