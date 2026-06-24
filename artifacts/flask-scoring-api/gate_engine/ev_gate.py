"""
ev_gate.py
Classify whether a prop is MONEY_QUALIFIED based on edge, hit rate, and market data.
Hard rules enforced — no override for missing market.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

MIN_HIT_RATE_FOR_MONEY = 0.60
MIN_L10_HIT_RATE       = 0.55
MIN_NO_VIG_EDGE        = 0.03


def run(row: dict[str, Any]) -> dict[str, Any]:
    """
    Reads l5_l10_ledger, market_gate, and outlier_gate results.
    Assigns ev classification — does NOT set terminal_label (classifier owns that).

    Gate result at row["gates"]["ev_gate"]:
      money_qualified  bool
      edge_score       float | None
      reasons          list[str]
      blockers         list[str]
    """
    ledger  = row.get("gates", {}).get("l5_l10_ledger", {})
    market  = row.get("gates", {}).get("market_gate", {})
    outlier = row.get("gates", {}).get("outlier_gate", {})

    reasons:  list[str] = []
    blockers: list[str] = []

    l10_hit  = ledger.get("l10_hit_rate")
    l5_hit   = ledger.get("l5_hit_rate")
    no_vig   = market.get("no_vig_prob")
    mkt_stat = market.get("market_status", "")

    if not ledger.get("passed"):
        blockers.append("EV:NO_LEDGER_DATA")
        result = _build(False, None, reasons, blockers)
        row["gates"]["ev_gate"] = result
        return row

    edge_components: list[float] = []

    if l10_hit is not None:
        if l10_hit >= MIN_L10_HIT_RATE:
            reasons.append(f"L10_HIT_RATE:{l10_hit}")
            edge_components.append(l10_hit - 0.5)
        else:
            blockers.append(f"EV:LOW_L10_HIT_RATE:{l10_hit}")

    if l5_hit is not None and l5_hit >= MIN_HIT_RATE_FOR_MONEY:
        reasons.append(f"L5_HIT_RATE:{l5_hit}")
        edge_components.append((l5_hit - 0.5) * 0.5)
    elif l5_hit is not None:
        blockers.append(f"EV:LOW_L5_HIT_RATE:{l5_hit}")

    if no_vig is not None:
        direction = row.get("direction", "MORE").upper()
        if direction in ("MORE", "OVER") and no_vig < 0.5 - MIN_NO_VIG_EDGE:
            reasons.append(f"NO_VIG_EDGE:{no_vig}")
            edge_components.append(0.5 - no_vig)
        elif direction in ("LESS", "UNDER") and no_vig > 0.5 + MIN_NO_VIG_EDGE:
            reasons.append(f"NO_VIG_EDGE_UNDER:{no_vig}")
            edge_components.append(no_vig - 0.5)

    if mkt_stat == "NO_MARKET_AVAILABLE":
        blockers.append("EV:NO_MARKET:MAX_LABEL=MODEL_QUALIFIED_HOLD")
        result = _build(False, _edge(edge_components), reasons, blockers)
        row["gates"]["ev_gate"] = result
        return row

    if outlier.get("any_flag"):
        blockers.append("EV:OUTLIER_FLAGS_PRESENT")

    edge_score  = _edge(edge_components)
    money_qual  = (
        not blockers
        and edge_score is not None
        and edge_score >= MIN_L10_HIT_RATE - 0.5
    )

    result = _build(money_qual, edge_score, reasons, blockers)
    row["gates"]["ev_gate"] = result
    return row


def _edge(components: list[float]) -> float | None:
    if not components:
        return None
    return round(sum(components) / len(components), 4)


def _build(qualified: bool, edge: float | None,
           reasons: list[str], blockers: list[str]) -> dict[str, Any]:
    return {
        "passed":          True,
        "money_qualified": qualified,
        "edge_score":      edge,
        "reasons":         reasons,
        "ev_blockers":     blockers,
    }
