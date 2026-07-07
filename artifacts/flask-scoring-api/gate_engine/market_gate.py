"""
market_gate.py
Compare PrizePicks line to sportsbook / projection market.
Outputs: market edge status, no-vig implied probability, board-vs-book delta.
Never approves — only labels market data quality.

CLV formula (v16.1-RC1):
  CLV = closing_implied_probability − entry_implied_probability
  Positive CLV = market confirmed our side after entry = Beat Close.

  Moneyline / American odds:
    entry +140 (41.7% implied), close +120 (45.5% implied) → CLV = +3.8% → CLV_BEAT

  Total (OVER): closing_line > entry_line → CLV_BEAT
  UNDER: closing_line < entry_line → CLV_BEAT
  Spread (bettor's perspective): entry_line > closing_line → CLV_BEAT

Opener vs closing:
  Opener unavailable → OPENER_UNAVAILABLE (caps confidence; does NOT block CLV grading)
  Closing line unavailable → NO_CLOSE_AVAILABLE (blocks CLV grading)

Phase 2 — Cash Threshold Validation:
  PrizePicks whole-number MORE/LESS lines require clearing a cash_threshold that differs
  from the displayed line by 1 (e.g. MORE 5 → cash 6, LESS 20 → cash 19). A sportsbook
  market at the displayed line or below (e.g. 4.5 OVER for MORE 5) does NOT validate the
  cash threshold — it is classified as CASH_THRESHOLD_NOT_VALIDATED and caps the row at
  MODEL_QUALIFIED_HOLD.  Half-point lines cash at displayed+0.5 so the sportsbook market
  exactly at the displayed line always validates (EXACT_VERIFIED).
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
MARKET_STATUS_OPENER_UNAVAILABLE = "OPENER_UNAVAILABLE"
MARKET_STATUS_NO_CLOSE           = "NO_CLOSE_AVAILABLE"

# Phase 2 — cash threshold validation statuses
CASH_STATUS_EXACT_VERIFIED        = "EXACT_VERIFIED"
CASH_STATUS_ADJACENT_CONTEXT_ONLY = "ADJACENT_CONTEXT_ONLY"
CASH_STATUS_NOT_VALIDATED         = "CASH_THRESHOLD_NOT_VALIDATED"
CASH_STATUS_MARKET_UNVERIFIED     = "MARKET_UNVERIFIED_EXACT"
CASH_STATUS_SOURCE_CONFLICT       = "SOURCE_CONFLICT"
CASH_STATUS_NO_THRESHOLDS         = "NO_PP_THRESHOLDS"

DRIFT_THRESHOLD = 0.5
EDGE_THRESHOLD  = 0.04

# Tolerance for "sportsbook line is close to cash_threshold / displayed_line"
_CASH_TOLERANCE = 0.5


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

    Phase 2: also validates sportsbook_line against row["pp_thresholds"]["cash_threshold"].
    Gate result at row["gates"]["market_gate"].
    """
    pp_line = row.get("line")
    row_market = row.get("market_line") or sportsbook_line
    row_consensus = row.get("consensus_line") or consensus_line

    if row_market is None and row_consensus is None and best_available is None:
        # No market data — compute cash threshold status from pp_thresholds alone
        cash_val = _validate_cash_threshold(
            pp_line, row.get("direction"), row.get("pp_thresholds"), None
        )
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
        result.update(cash_val)
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

    # Phase 2: cash threshold validation
    cash_val = _validate_cash_threshold(
        pp_line, row.get("direction"), row.get("pp_thresholds"), reference_line
    )
    # Override cash_threshold_status to SOURCE_CONFLICT when market itself contradicts
    if market_status == MARKET_STATUS_CONTRADICTION:
        cash_val["cash_threshold_status"] = CASH_STATUS_SOURCE_CONFLICT
        cash_val["substitution_allowed"]  = False
        cash_val["confidence_cap"]        = "MODEL_QUALIFIED_HOLD"
        cash_val["exact_market_found"]    = False

    _apply_cash_threshold_blockers(row, cash_val)

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
    result.update(cash_val)
    row["gates"]["market_gate"] = result
    return row


def _validate_cash_threshold(
    pp_line: float | None,
    direction: str | None,
    pp_thresholds: dict | None,
    sportsbook_line: float | None,
) -> dict[str, Any]:
    """
    Phase 2 cash threshold validation.

    Returns a dict of exact_market_found, adjacent_market_used, cash_threshold_status,
    substitution_allowed, confidence_cap, and related market detail fields.

    Whole-number MORE n (cash = n+1):
      - Sportsbook ≥ n+0.5   → EXACT_VERIFIED        (within 0.5 of cash threshold)
      - Sportsbook ≈ n−0.5   → CASH_THRESHOLD_NOT_VALIDATED  (adjacent to displayed, below cash)
      - No sportsbook         → MARKET_UNVERIFIED_EXACT

    Half-point MORE n.5 (cash = n+1):
      - Sportsbook ≈ n.5     → EXACT_VERIFIED         (displayed IS within 0.5 of cash)
      - Sportsbook ≈ n       → ADJACENT_CONTEXT_ONLY   (softer cap)
      - No sportsbook         → MARKET_UNVERIFIED_EXACT
    """
    _empty = {
        "exact_market_found":       None,
        "exact_market_line":        None,
        "exact_market_side":        None,
        "exact_market_price":       None,
        "exact_market_no_vig_prob": None,
        "adjacent_market_used":     None,
        "adjacent_market_line":     None,
        "adjacent_market_side":     None,
        "substitution_allowed":     True,
        "confidence_cap":           None,
        "cash_threshold_status":    CASH_STATUS_NO_THRESHOLDS,
    }

    if not pp_thresholds:
        return _empty

    cash_thr   = pp_thresholds.get("cash_threshold")
    whole_line = bool(pp_thresholds.get("whole_number_line", False))
    direction_upper = (direction or "").upper()

    if cash_thr is None:
        return dict(_empty, substitution_allowed=False,
                    confidence_cap="MODEL_QUALIFIED_HOLD",
                    cash_threshold_status=CASH_STATUS_MARKET_UNVERIFIED)

    if sportsbook_line is None:
        return {
            "exact_market_found":       False,
            "exact_market_line":        None,
            "exact_market_side":        None,
            "exact_market_price":       None,
            "exact_market_no_vig_prob": None,
            "adjacent_market_used":     False,
            "adjacent_market_line":     None,
            "adjacent_market_side":     None,
            "substitution_allowed":     False,
            "confidence_cap":           "MODEL_QUALIFIED_HOLD",
            "cash_threshold_status":    CASH_STATUS_MARKET_UNVERIFIED,
        }

    cash_delta    = abs(sportsbook_line - cash_thr)
    display_delta = abs(sportsbook_line - pp_line) if pp_line is not None else None

    # --- EXACT_VERIFIED: sportsbook within tolerance of cash_threshold ---
    if cash_delta <= _CASH_TOLERANCE:
        return {
            "exact_market_found":       True,
            "exact_market_line":        sportsbook_line,
            "exact_market_side":        direction_upper,
            "exact_market_price":       None,
            "exact_market_no_vig_prob": None,
            "adjacent_market_used":     False,
            "adjacent_market_line":     None,
            "adjacent_market_side":     None,
            "substitution_allowed":     True,
            "confidence_cap":           None,
            "cash_threshold_status":    CASH_STATUS_EXACT_VERIFIED,
        }

    # --- ADJACENT: sportsbook near displayed_line but not cash_threshold ---
    if display_delta is not None and display_delta <= _CASH_TOLERANCE:
        if whole_line:
            # Whole-number: sportsbook at displayed ≈ cash_threshold − 1 → hard fail
            return {
                "exact_market_found":       False,
                "exact_market_line":        None,
                "exact_market_side":        None,
                "exact_market_price":       None,
                "exact_market_no_vig_prob": None,
                "adjacent_market_used":     True,
                "adjacent_market_line":     sportsbook_line,
                "adjacent_market_side":     direction_upper,
                "substitution_allowed":     False,
                "confidence_cap":           "MODEL_QUALIFIED_HOLD",
                "cash_threshold_status":    CASH_STATUS_NOT_VALIDATED,
            }
        else:
            # Half-point: sportsbook 1 unit below displayed (and 1 below cash) → soft cap
            return {
                "exact_market_found":       False,
                "exact_market_line":        None,
                "exact_market_side":        None,
                "exact_market_price":       None,
                "exact_market_no_vig_prob": None,
                "adjacent_market_used":     True,
                "adjacent_market_line":     sportsbook_line,
                "adjacent_market_side":     direction_upper,
                "substitution_allowed":     False,
                "confidence_cap":           "MONEY_QUALIFIED_MAX",
                "cash_threshold_status":    CASH_STATUS_ADJACENT_CONTEXT_ONLY,
            }

    # --- Neither exact nor adjacent: no useful market ---
    return {
        "exact_market_found":       False,
        "exact_market_line":        None,
        "exact_market_side":        None,
        "exact_market_price":       None,
        "exact_market_no_vig_prob": None,
        "adjacent_market_used":     False,
        "adjacent_market_line":     None,
        "adjacent_market_side":     None,
        "substitution_allowed":     False,
        "confidence_cap":           "MODEL_QUALIFIED_HOLD",
        "cash_threshold_status":    CASH_STATUS_MARKET_UNVERIFIED,
    }


def _apply_cash_threshold_blockers(row: dict[str, Any], cash_val: dict[str, Any]) -> None:
    """
    Add blockers to row based on cash threshold validation result.
    Only adds blockers for actionable failures — not for EXACT_VERIFIED or legacy NO_PP_THRESHOLDS.
    """
    cash_status = cash_val.get("cash_threshold_status")
    if cash_status in (CASH_STATUS_NOT_VALIDATED, CASH_STATUS_MARKET_UNVERIFIED,
                       CASH_STATUS_SOURCE_CONFLICT):
        row["blockers"].append(
            f"MARKET:{cash_status}:MAX_LABEL=MODEL_QUALIFIED_HOLD"
        )
    elif cash_status == CASH_STATUS_ADJACENT_CONTEXT_ONLY:
        row["blockers"].append(
            f"MARKET:{cash_status}:MAX_LABEL=MONEY_QUALIFIED"
        )


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


def _american_to_implied(american_odds: float) -> float:
    """Convert American odds to decimal implied probability."""
    o = float(american_odds)
    if o > 0:
        return 100.0 / (o + 100.0)
    return abs(o) / (abs(o) + 100.0)


def _clv_beat(entry_odds: float, closing_odds: float) -> bool:
    """
    CLV beat using implied-probability comparison (v16.1-RC1).

    CLV = closing_implied_probability − entry_implied_probability
    Positive CLV = closing line priced our side at higher probability = Beat Close.

    Example:
      Entry +140 (41.7% implied), Close +120 (45.5% implied)
      CLV = 45.5% − 41.7% = +3.8%  →  CLV_BEAT

      Entry −120 (54.5% implied), Close −110 (52.4% implied)
      CLV = 52.4% − 54.5% = −2.1%  →  CLV_MISS

    Both entry_odds and closing_odds are American format.
    """
    try:
        return _american_to_implied(closing_odds) > _american_to_implied(entry_odds)
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def _clv_beat_line(entry_line: float, closing_line: float,
                   side: str | None) -> bool | None:
    """
    Line-aware CLV for spreads and totals (point-line format, not American odds).

    Total:
      OVER  — closing_line > entry_line → CLV_BEAT
              e.g. Bet Over 160.5, closes 162.5 → Beat Close
      UNDER — closing_line < entry_line → CLV_BEAT
              e.g. Bet Under 162.5, closes 160.5 → Beat Close

    Spread (line from bettor's side):
      All sides — entry_line > closing_line → CLV_BEAT
              Favorite: Bet −2.5, closes −3.5 → entry(−2.5) > closing(−3.5) → Beat Close
              Underdog: Bet +4.5, closes +3.5 → entry(+4.5) > closing(+3.5) → Beat Close
    """
    side_lc = (side or "").lower().strip()
    try:
        if side_lc == "over":
            return float(closing_line) > float(entry_line)
        if side_lc == "under":
            return float(closing_line) < float(entry_line)
        return float(entry_line) > float(closing_line)
    except (TypeError, ValueError):
        return None
