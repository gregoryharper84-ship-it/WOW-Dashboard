"""
contract_execution_gate.py  —  Kalshi contract execution overlay
WOW-PATCH-2026-07-09-KALSHI-CONTRACT-EXECUTION-OVERLAY

Purpose
-------
Prevent sportsbook-style "winner pick" logic from promoting Kalshi candidates
without contract execution math.  A Kalshi sports Game Winner candidate cannot
reach LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN unless it clears ALL of:

  1. kalshi_orderbook_source == "direct_api"
  2. fresh orderbook  (age <= STALE_SECONDS = 600 s)
  3. trading_active == True
  4. non-empty orderbook  (at least one YES or NO bid present)
  5. YES / NO ask reconstruction succeeds  (100 - best_NO_bid / 100 - best_YES_bid)
  6. fee model is computable  (model_probability + executable price known)
  7. executable ask <= max_buy_price
  8. available depth at price >= intended quantity
  9. execution_mode == LIMIT_ONLY_DRY_RUN   (always enforced)
 10. can_execute == False                    (always enforced)

Labels emitted (never LLP_APPROVED or LLP_PLAYABLE)
-----------------------------------------------------
  LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN — all gates pass; can_execute=False unconditionally
  LLP_WATCH                — soft gate failed (source, staleness, fee, depth)
  LLP_REJECT               — hard gate failed (empty book, reconstruction failure,
                             not trading, ask > max_buy_price)

Fee formula (July 2026 Kalshi schedule, M = 1 for KXMLBGAME / KXWNBAGAME)
---------------------------------------------------------------------------
  taker fee per contract (dollars) = M × 0.07  × P × (1 − P)
  maker fee per contract (dollars) = M × 0.0175 × P × (1 − P)

  P = executable price in decimal (0.0 – 1.0).

Edge floors (post-fee, market-type-aware)
-----------------------------------------
  KXMLBGAME  (MLB main winner)  : 1.5 %
  KXWNBAGAME (WNBA main winner) : 2.0 %
  all others (derivative/thin)  : 2.5 %

max_buy_price formula
---------------------
  max_buy_price_cents = model_probability × 100
                      - required_edge_threshold × 100
                      - estimated_fee_per_contract_cents
                      - slippage_buffer × 100

Dry-run fill ledger
-------------------
  fill_status values:
    FILLED_DRY_RUN        — ask ≤ max_buy_price AND depth ≥ quantity
    PARTIAL_FILL_DRY_RUN  — ask ≤ max_buy_price BUT depth < quantity
    NO_FILL               — ask > max_buy_price, hard reject, or fee model missing
    INVALID_STALE_BOOK    — orderbook stale; any fill figure is unreliable

  Settlement-time fields (closing_price_cents, settlement_value_cents,
  gross_pnl_cents, net_pnl_after_fees_cents, clv_cents, final_result) are
  always None at decision time.  A separate settle pass writes them.

  CALIBRATION RULE: only FILLED_DRY_RUN rows may enter model ROI or hit-rate
  calculations after settlement.  NO_FILL and PARTIAL_FILL_DRY_RUN rows must
  be excluded regardless of which side the market settled on.

Execution guarantee
-------------------
  can_execute   = False   (unconditional — no live trading, ever)
  dry_run_only  = True    (unconditional)
  execution_mode = "LIMIT_ONLY_DRY_RUN"
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

# ── Constants ─────────────────────────────────────────────────────────────────

_EDGE_FLOORS: dict[str, float] = {
    "KXMLBGAME":  0.015,   # MLB main winner — 1.5 %
    "KXWNBAGAME": 0.020,   # WNBA main winner — 2.0 %
}
_EDGE_FLOOR_DEFAULT = 0.025   # derivative / thin markets — 2.5 %

STALE_SECONDS: int = 600      # 10 min → KALSHI_ORDERBOOK_STALE

TAKER_FEE_RATE = 0.07         # 7 % of P × (1-P) per contract
MAKER_FEE_RATE = 0.0175       # 1.75 %

_EXECUTION_MODE = "LIMIT_ONLY_DRY_RUN"
_EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ── Fill-status constants ─────────────────────────────────────────────────────
FILL_STATUS_FILLED   = "FILLED_DRY_RUN"        # ask ≤ max_buy AND depth ≥ qty
FILL_STATUS_PARTIAL  = "PARTIAL_FILL_DRY_RUN"   # ask ≤ max_buy BUT depth < qty
FILL_STATUS_NO_FILL  = "NO_FILL"                # ask > max_buy, hard reject, or no fee model
FILL_STATUS_STALE    = "INVALID_STALE_BOOK"     # orderbook stale — figures unreliable

# Only FILLED_DRY_RUN rows may enter calibration after settlement.
CALIBRATION_ELIGIBLE_STATUSES = frozenset({FILL_STATUS_FILLED})


# ── Label constants and normalization ────────────────────────────────────────

# Canonical playable label. Any caller or renderer that produced the bare
# "LLP_PLAYABLE_LIMIT_ONLY" (without _DRY_RUN suffix) must normalize it.
LABEL_PLAYABLE      = "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"
LABEL_WATCH         = "LLP_WATCH"
LABEL_REJECT        = "LLP_REJECT"

# Legacy alias that must never appear in final output
_LABEL_BARE_PLAYABLE = "LLP_PLAYABLE_LIMIT_ONLY"

_NORMALIZATION_MAP: dict[str, str] = {
    _LABEL_BARE_PLAYABLE: LABEL_PLAYABLE,
}


def normalize_label(label: str) -> str:
    """
    Normalize a contract-gate final_label to its canonical form.

    The only normalization currently required is:
      LLP_PLAYABLE_LIMIT_ONLY  →  LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN

    All other labels (LLP_REJECT, LLP_WATCH, GATE_ERROR, …) pass through
    unchanged.  can_execute remains False regardless of the normalized label.
    """
    return _NORMALIZATION_MAP.get(label, label)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _taker_fee_cents(price_decimal: float, M: float = 1.0) -> float:
    """
    Taker fee per contract in cents (unrounded, display quality).

    Formula: M × 0.07 × P × (1 − P) × 100
    Example: M=1, P=0.62  →  0.07 × 0.62 × 0.38 × 100  =  1.6492 ¢
    """
    return M * TAKER_FEE_RATE * price_decimal * (1.0 - price_decimal) * 100.0


def _maker_fee_cents(price_decimal: float, M: float = 1.0) -> float:
    """Maker fee per contract in cents (unrounded)."""
    return M * MAKER_FEE_RATE * price_decimal * (1.0 - price_decimal) * 100.0


def _age_seconds(fetched_at_iso: str) -> Optional[float]:
    """Seconds since fetched_at_iso (UTC).  Returns None on parse failure."""
    try:
        ts = datetime.datetime.fromisoformat(fetched_at_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(tz=datetime.timezone.utc) - ts).total_seconds()
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(
    series_ticker:                str,
    market_ticker:                str,
    event_id:                     Optional[str]   = None,
    side:                         str             = "YES",
    outcome:                      Optional[str]   = None,
    model_probability:            Optional[float] = None,
    consensus_no_vig_probability: Optional[float] = None,
    normalized_book:              Optional[dict]  = None,
    orderbook_fetched_at:         Optional[str]   = None,
    trading_active:               Optional[bool]  = None,
    kalshi_orderbook_source:      str             = "no_ticker",
    quantity:                     int             = 1,
    slippage_buffer:              float           = 0.005,
    fee_multiplier:               float           = 1.0,
) -> dict[str, Any]:
    """
    Evaluate a Kalshi contract candidate through the execution overlay gate.

    Parameters
    ----------
    series_ticker               e.g. "KXMLBGAME", "KXWNBAGAME" — selects edge floor
    market_ticker               full Kalshi market ticker
    event_id                    event_ticker from mapper (informational)
    side                        "YES" or "NO" — which contract side to price
    outcome                     team / outcome label for the chosen side
    model_probability           LLP model probability  0.0 – 1.0
    consensus_no_vig_probability  sportsbook no-vig consensus probability
    normalized_book             output of orderbook_normalizer.normalize(); keys used:
                                  best_yes_bid, best_no_bid (decimal 0-1),
                                  depth_at_price (int)
    orderbook_fetched_at        ISO-8601 UTC timestamp of the orderbook snapshot
    trading_active              True = market open/trading; False = closed
    kalshi_orderbook_source     "direct_api" required for gate passage
    quantity                    intended dry-run contracts for depth check (default 1)
    slippage_buffer             decimal slippage buffer  (default 0.005 = 0.5 %)
    fee_multiplier              Kalshi multiplier M in fee formula  (default 1.0)

    Returns
    -------
    dict with all WOW-PATCH-2026-07-09 required output fields.
    can_execute and dry_run_only are unconditionally False / True.
    """
    blockers: list[str] = []
    _reject = False   # hard-cap → LLP_REJECT
    _watch  = False   # soft-cap → LLP_WATCH  (overridden by _reject)

    book = normalized_book or {}

    # ── Edge floor by series ──────────────────────────────────────────────────
    required_edge = _EDGE_FLOORS.get((series_ticker or "").upper(), _EDGE_FLOOR_DEFAULT)

    # ── Orderbook age ─────────────────────────────────────────────────────────
    ob_age: Optional[float] = None
    if orderbook_fetched_at:
        ob_age = _age_seconds(orderbook_fetched_at)

    # ── Gate 1: orderbook source ──────────────────────────────────────────────
    # Only server-side direct_api fetches carry executable-price freshness.
    # Web-UI / caller-supplied / screenshot prices are display-only → LLP_WATCH.
    if kalshi_orderbook_source != "direct_api":
        blockers.append("KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API")
        _watch = True

    # ── Gate 2: empty orderbook ───────────────────────────────────────────────
    has_yes_bid = book.get("best_yes_bid") is not None
    has_no_bid  = book.get("best_no_bid")  is not None
    if not (has_yes_bid or has_no_bid):
        blockers.append("KALSHI_EMPTY_ORDERBOOK")
        _reject = True

    # ── Gate 3: YES / NO ask reconstruction ──────────────────────────────────
    # Kalshi API returns YES bids and NO bids only; asks are derived by
    # the "100 - complement bid" identity:
    #   best_YES_ask_cents = 100 − best_NO_bid_cents
    #   best_NO_ask_cents  = 100 − best_YES_bid_cents
    raw_yes_bid: Optional[float] = book.get("best_yes_bid")   # decimal 0-1
    raw_no_bid:  Optional[float] = book.get("best_no_bid")    # decimal 0-1

    best_yes_bid_cents: Optional[float] = round(raw_yes_bid * 100, 4) if raw_yes_bid is not None else None
    best_no_bid_cents:  Optional[float] = round(raw_no_bid  * 100, 4) if raw_no_bid  is not None else None

    best_yes_ask_cents: Optional[float] = (
        round(100.0 - best_no_bid_cents,  4) if best_no_bid_cents  is not None else None
    )
    best_no_ask_cents: Optional[float] = (
        round(100.0 - best_yes_bid_cents, 4) if best_yes_bid_cents is not None else None
    )

    ask_ok = best_yes_ask_cents is not None and best_no_ask_cents is not None
    if not ask_ok and not _reject:
        # Only add if we haven't already rejected for empty book (same root cause)
        blockers.append("KALSHI_ASK_RECONSTRUCTION_FAILED")
        _reject = True

    spread_cents: Optional[float] = (
        round(best_yes_ask_cents - best_yes_bid_cents, 4)
        if best_yes_ask_cents is not None and best_yes_bid_cents is not None
        else None
    )

    # ── Gate 4: market trading status ─────────────────────────────────────────
    if trading_active is False:
        blockers.append("KALSHI_MARKET_NOT_TRADING")
        _reject = True

    # ── Gate 5: staleness ─────────────────────────────────────────────────────
    if ob_age is None or ob_age > STALE_SECONDS:
        blockers.append("KALSHI_ORDERBOOK_STALE")
        _watch = True

    # ── Gate 6: fee model + derived quantities ────────────────────────────────
    side_upper = (side or "YES").upper()
    executable_price_cents: Optional[float] = (
        best_yes_ask_cents if side_upper == "YES" else best_no_ask_cents
    )

    estimated_fee_per_contract_cents: Optional[float]   = None
    fee_adjusted_breakeven_probability: Optional[float] = None
    max_buy_price_cents: Optional[float]                = None
    contract_ev_per_contract_cents: Optional[float]     = None
    fee_ok = False

    if executable_price_cents is not None and model_probability is not None:
        ep_dec     = executable_price_cents / 100.0
        raw_fee_c  = _taker_fee_cents(ep_dec, M=fee_multiplier)

        estimated_fee_per_contract_cents   = round(raw_fee_c, 4)
        fee_adjusted_breakeven_probability = round(ep_dec + raw_fee_c / 100.0, 6)

        # max_buy_price = model_prob × 100
        #               − edge_floor × 100
        #               − fee_per_contract_cents
        #               − slippage × 100
        max_buy_price_cents = round(
            model_probability * 100.0
            - required_edge * 100.0
            - raw_fee_c
            - slippage_buffer * 100.0,
            4,
        )

        # EV per contract (in cents): model_prob − ask − fee  (all in cent-space)
        contract_ev_per_contract_cents = round(
            model_probability * 100.0
            - executable_price_cents
            - raw_fee_c,
            4,
        )
        fee_ok = True
    else:
        blockers.append("KALSHI_FEE_MODEL_MISSING")
        _watch = True

    # ── Gate 7: max buy price ─────────────────────────────────────────────────
    # The executable ask must be AT OR BELOW max_buy_price for a limit-order
    # to be model-justified.  Above that threshold the trade is unprofitable
    # after fees and required edge — hard reject.
    would_fill = False
    would_place_limit_at_cents: Optional[float] = None
    if fee_ok and executable_price_cents is not None and max_buy_price_cents is not None:
        if executable_price_cents <= max_buy_price_cents:
            would_fill                 = True
            would_place_limit_at_cents = executable_price_cents
        else:
            blockers.append("KALSHI_MAX_BUY_PRICE_FAIL")
            _reject = True

    # ── Gate 8: depth ─────────────────────────────────────────────────────────
    available_depth = int(book.get("depth_at_price") or 0)
    if available_depth < quantity:
        blockers.append("KALSHI_DEPTH_INSUFFICIENT")
        _watch = True

    # ── Gate 8b: dry-run fill status ──────────────────────────────────────────
    # Derived from the results of Gates 5–8; no new hard/soft flags added here.
    # Priority: INVALID_STALE_BOOK > FILLED/PARTIAL (price ok) > NO_FILL
    #
    # Both KALSHI_ORDERBOOK_STALE and KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API
    # make the fill price non-executable: a display-only / screenshot price
    # carries the same reliability risk as a stale timestamp.
    _unreliable_book = (
        "KALSHI_ORDERBOOK_STALE"                in blockers
        or "KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API" in blockers
    )
    if _unreliable_book:
        fill_status = FILL_STATUS_STALE
    elif would_fill:
        fill_status = FILL_STATUS_FILLED if available_depth >= quantity else FILL_STATUS_PARTIAL
    else:
        fill_status = FILL_STATUS_NO_FILL

    # Ledger: quantities and fees at decision time
    # effective_quantity_filled — the contracts that would theoretically fill
    if fill_status == FILL_STATUS_FILLED:
        effective_quantity_filled: int = quantity
    elif fill_status == FILL_STATUS_PARTIAL:
        effective_quantity_filled = min(available_depth, quantity)
    else:
        effective_quantity_filled = 0

    hypothetical_fill_price_cents: Optional[float] = (
        executable_price_cents
        if fill_status in (FILL_STATUS_FILLED, FILL_STATUS_PARTIAL)
        else None
    )

    total_fee_cents: Optional[float] = None
    if (
        estimated_fee_per_contract_cents is not None
        and effective_quantity_filled > 0
    ):
        total_fee_cents = round(estimated_fee_per_contract_cents * effective_quantity_filled, 4)

    # Settlement-time fields — always None at decision time; written by settle pass
    closing_price_cents:      Optional[float] = None
    settlement_value_cents:   Optional[float] = None   # 100 for win, 0 for loss
    gross_pnl_cents:          Optional[float] = None
    net_pnl_after_fees_cents: Optional[float] = None
    clv_cents:                Optional[float] = None   # closing_price − fill_price
    final_result:             Optional[str]   = None   # WIN_DRY_RUN / LOSS_DRY_RUN / NO_FILL

    # ── Gate 9: market-order policy (permanent) ───────────────────────────────
    # KALSHI_MARKET_ORDER_BANNED is always present to signal that market orders
    # are unconditionally prohibited.  It does not affect label rank — the
    # engine only ever emits LIMIT_ONLY_DRY_RUN instructions regardless.
    blockers.append("KALSHI_MARKET_ORDER_BANNED")

    # ── Final label ───────────────────────────────────────────────────────────
    # Priority: REJECT > WATCH > PLAYABLE_LIMIT_ONLY
    if _reject:
        final_label = "LLP_REJECT"
    elif _watch:
        final_label = "LLP_WATCH"
    else:
        final_label = "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"

    # ── Informational: raw model edge vs sportsbook ───────────────────────────
    raw_model_edge: Optional[float] = None
    if model_probability is not None and consensus_no_vig_probability is not None:
        raw_model_edge = round(model_probability - consensus_no_vig_probability, 6)

    return {
        # ── Identity ──────────────────────────────────────────────────────────
        "series_ticker":                        series_ticker,
        "market_ticker":                        market_ticker,
        "event_id":                             event_id,
        "side":                                 side,
        "outcome":                              outcome,
        # ── Model inputs ──────────────────────────────────────────────────────
        "model_probability":                    model_probability,
        "consensus_no_vig_probability":         consensus_no_vig_probability,
        "raw_model_edge":                       raw_model_edge,
        # ── Orderbook provenance ──────────────────────────────────────────────
        "kalshi_orderbook_source":              kalshi_orderbook_source,
        "orderbook_fetched_at":                 orderbook_fetched_at,
        "orderbook_age_seconds":                round(ob_age, 1) if ob_age is not None else None,
        "trading_active":                       trading_active,
        # ── Bid / ask (in cents) ──────────────────────────────────────────────
        "best_yes_bid_cents":                   best_yes_bid_cents,
        "best_no_bid_cents":                    best_no_bid_cents,
        "best_yes_ask_cents":                   best_yes_ask_cents,
        "best_no_ask_cents":                    best_no_ask_cents,
        "spread_cents":                         spread_cents,
        "available_depth_at_price":             available_depth,
        # ── Execution math (all in cents) ─────────────────────────────────────
        "executable_price_cents":               executable_price_cents,
        "estimated_fee_per_contract_cents":     estimated_fee_per_contract_cents,
        "fee_adjusted_breakeven_probability":   fee_adjusted_breakeven_probability,
        "required_edge_threshold":              required_edge,
        "slippage_buffer":                      slippage_buffer,
        "max_buy_price_cents":                  max_buy_price_cents,
        "contract_ev_per_contract_cents":       contract_ev_per_contract_cents,
        # ── Execution verdict ─────────────────────────────────────────────────
        "execution_mode":                       _EXECUTION_MODE,
        "would_place_limit_at_cents":           would_place_limit_at_cents,
        "would_fill":                           would_fill,
        "final_label":                          final_label,
        "blockers":                             sorted(set(blockers)),
        # ── Unconditional safety flags ────────────────────────────────────────
        "can_execute":                          False,
        "dry_run_only":                         True,
        "execution_rule":                       _EXECUTION_RULE,
        # ── Dry-run fill ledger (decision-time) ───────────────────────────────
        # fill_status drives calibration eligibility.  Only FILLED_DRY_RUN rows
        # may enter model ROI / hit-rate calculations after settlement.
        "intended_quantity":                    quantity,
        "executable_ask_at_decision_cents":     executable_price_cents,
        "recommended_limit_price_cents":        would_place_limit_at_cents,
        "fill_status":                          fill_status,
        "effective_quantity_filled":            effective_quantity_filled,
        "hypothetical_fill_price_cents":        hypothetical_fill_price_cents,
        "fee_per_contract_cents":               estimated_fee_per_contract_cents,
        "total_fee_cents":                      total_fee_cents,
        "calibration_eligible":                 fill_status in CALIBRATION_ELIGIBLE_STATUSES,
        # Settlement-time fields — always None at decision time; written by settle pass
        "closing_price_cents":                  closing_price_cents,
        "settlement_value_cents":               settlement_value_cents,
        "gross_pnl_cents":                      gross_pnl_cents,
        "net_pnl_after_fees_cents":             net_pnl_after_fees_cents,
        "clv_cents":                            clv_cents,
        "final_result":                         final_result,
    }
