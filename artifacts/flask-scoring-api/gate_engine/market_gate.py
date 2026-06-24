"""
market_gate.py
Compare PrizePicks line to sportsbook / projection market.
Outputs: market edge status, no-vig implied probability, board-vs-book delta.
Never approves — only labels market data quality.
"""
from __future__ import annotations

from typing import Any

from .labels import DataStatus


MARKET_STATUS_EDGE          = "MARKET_EDGE_DETECTED"
MARKET_STATUS_CONTRADICTION = "MARKET_CONTRADICTION"
MARKET_STATUS_NONE          = "NO_MARKET_AVAILABLE"
MARKET_STATUS_DRIFT         = "SEVERE_BOARD_VS_BOOK_DRIFT"
MARKET_STATUS_CLV_PENDING   = "CLV_PENDING"
MARKET_STATUS_VERIFIED      = "MARKET_VERIFIED"

DRIFT_THRESHOLD = 0.5
EDGE_THRESHOLD  = 0.04


def run(row: dict[str, Any],
        sportsbook_line: float | None = None,
        best_available: float | None = None,
        consensus_line: float | None = None,
        clv_entry_price: float | None = None,
        closing_price: float | None = None) -> dict[str, Any]:
    """
    Evaluate market data for this prop row.

    sportsbook_line   — main sportsbook equivalent line
    best_available    — best market line across books
    consensus_line    — consensus across sources
    clv_entry_price   — odds at entry (American format)
    closing_price     — line at close (for CLV calc)

    Gate result at row["gates"]["market_gate"].
    """
    pp_line = row.get("line")
    row_market = row.get("market_line") or sportsbook_line
    row_consensus = row.get("consensus_line") or consensus_line

    if row_market is None and row_consensus is None and best_available is None:
        result = {
            "passed":        True,
            "market_status": MARKET_STATUS_NONE,
            "data_status":   DataStatus.DATA_UNOBTAINABLE.value,
            "delta":         None,
            "no_vig_prob":   None,
            "clv_entry":     clv_entry_price,
            "closing_price": closing_price,
            "clv_status":    MARKET_STATUS_CLV_PENDING if clv_entry_price else None,
            "note":          "No market data available — max label is MODEL_QUALIFIED_HOLD",
        }
        row["gates"]["market_gate"] = result
        row["blockers"].append("MARKET:NO_MARKET_AVAILABLE:MAX_LABEL=MODEL_QUALIFIED_HOLD")
        return row

    reference_line = row_market or row_consensus or best_available
    delta = None
    if pp_line is not None and reference_line is not None:
        delta = round(pp_line - reference_line, 3)

    market_status = _classify_market(pp_line, reference_line, delta)
    no_vig = _no_vig_prob(clv_entry_price)

    clv_status = None
    if closing_price is not None and clv_entry_price is not None:
        clv_status = "CLV_BEAT" if _clv_beat(clv_entry_price, closing_price) else "CLV_MISS"
    elif clv_entry_price is not None:
        clv_status = MARKET_STATUS_CLV_PENDING

    data_status = DataStatus.RETRIEVED.value
    if row_market is None and row_consensus is None:
        data_status = DataStatus.PROXY_ONLY.value

    if market_status == MARKET_STATUS_DRIFT:
        row["blockers"].append(f"MARKET:SEVERE_DRIFT:delta={delta}")

    result = {
        "passed":          market_status != MARKET_STATUS_CONTRADICTION,
        "market_status":   market_status,
        "data_status":     data_status,
        "pp_line":         pp_line,
        "sportsbook_line": row_market,
        "best_available":  best_available,
        "consensus_line":  row_consensus,
        "delta":           delta,
        "no_vig_prob":     no_vig,
        "clv_entry":       clv_entry_price,
        "closing_price":   closing_price,
        "clv_status":      clv_status,
    }
    row["gates"]["market_gate"] = result
    return row


def _classify_market(pp_line: float | None, ref_line: float | None,
                     delta: float | None) -> str:
    if delta is None:
        return MARKET_STATUS_NONE
    abs_delta = abs(delta)
    if abs_delta >= DRIFT_THRESHOLD:
        return MARKET_STATUS_DRIFT
    if abs_delta <= EDGE_THRESHOLD:
        return MARKET_STATUS_VERIFIED
    if delta > 0:
        return MARKET_STATUS_EDGE
    return MARKET_STATUS_CONTRADICTION


def _no_vig_prob(american_odds: float | None) -> float | None:
    if american_odds is None:
        return None
    try:
        o = float(american_odds)
        if o > 0:
            return round(100 / (o + 100), 4)
        return round(abs(o) / (abs(o) + 100), 4)
    except (TypeError, ValueError):
        return None


def _clv_beat(entry: float, closing: float) -> bool:
    return entry < closing
