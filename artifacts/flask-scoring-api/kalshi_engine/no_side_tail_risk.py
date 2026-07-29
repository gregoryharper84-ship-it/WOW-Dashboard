"""
no_side_tail_risk.py  —  Kalshi NO-side tail-risk analysis and calibration
WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION

Patch status:      CANDIDATE_FORWARD_TEST_ONLY
framework:         WOW_v16_CLEAN_CORE
can_execute:       False
capital_allocation: False

Five reviewer-mandated additions:
  1. COMPLEMENT_SIDE_SCAN          — bidirectional YES/NO scoring on every contract
  2. HIGH_PRICE_TAIL_RISK_GATE     — tail-loss ratio + wins-required for price ≥ threshold
  3. HISTORICAL_ZERO_FALLACY_BLOCK — blocks probability=0 or 1 without logical proof
  4. VOLUME_IS_NOT_DEPTH_GATE      — prohibit cumulative volume as executable liquidity proxy
  5. NO-side calibration entry     — build_calibration_entry() for DB insertion

Patch rules:
  FREE_MONEY_LANGUAGE_PROHIBITED=True
  NO_SIDE_AUTO_EDGE=False
  HISTORICAL_ZERO_IS_NOT_ZERO_PROBABILITY=True
  VOLUME_IS_NOT_EXECUTABLE_DEPTH=True
  HIGH_WIN_RATE_IS_NOT_POSITIVE_EV=True
  TAIL_LOSS_RATIO_REQUIRED=True
  PRICE_BUCKET_CALIBRATION_REQUIRED=True
  FIELD_NORMALIZATION_REQUIRED_FOR_OUTRIGHTS=True

Lane ceilings applied to `patch_label`:
  Uncalibrated NO scan (no calibrated_lb)              → KALSHI_WATCH
  Missing depth / Calibrated but no depth evidence     → KALSHI_DATA_UNOBTAINABLE
  Fresh price + positive point edge, non-positive LB   → KALSHI_REJECT_NO_EDGE
  Historical zero fallacy (p=0 or p=1 without proof)  → KALSHI_REJECT_NO_EDGE
  All individual gates pass                            → KALSHI_SINGLE_RESEARCH_ELIGIBLE
"""
from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Patch identity
# ---------------------------------------------------------------------------

PATCH_ID           = "WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION"
PATCH_STATUS       = "CANDIDATE_FORWARD_TEST_ONLY"
ENGINE_VERSION     = "WOW_v16_CLEAN_CORE"
can_execute        = False
capital_allocation = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Contracts at or above this price trigger HIGH_PRICE_TAIL_RISK_GATE
HIGH_PRICE_THRESHOLD: float = 0.85

# Contracts in this tier require separate calibration (reviewer 95–99¢ bucket)
EXTREME_PRICE_THRESHOLD: float = 0.95

# Fee rate — must match fee_model.FEE_RATE (isolated here to avoid module-level import)
_FEE_RATE: float = 0.07

# Price buckets from reviewer spec
_PRICE_BUCKETS: list[tuple[float, float, str]] = [
    (0.50, 0.70, "50-69c"),
    (0.70, 0.85, "70-84c"),
    (0.85, 0.90, "85-89c"),
    (0.90, 0.95, "90-94c"),
    (0.95, 1.00, "95-99c"),
]

# Lane ceiling label strings (mirror edge_engine labels — kept as strings, not PropLabel)
_LABEL_WATCH             = "KALSHI_WATCH"
_LABEL_DATA_UNOBTAINABLE = "KALSHI_DATA_UNOBTAINABLE"
_LABEL_REJECT_NO_EDGE    = "KALSHI_REJECT_NO_EDGE"
_LABEL_RESEARCH_ELIGIBLE = "KALSHI_SINGLE_RESEARCH_ELIGIBLE"

# PropLabel string registered in gate_engine/labels.py
LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED = "HISTORICAL_NON_OCCURRENCE_MISUSED"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _price_bucket(price: float) -> str:
    """Map a decimal entry price to the reviewer-specified price bucket label."""
    for lo, hi, label in _PRICE_BUCKETS:
        if lo <= price < hi:
            return label
    # Above 1.00 or exactly 1.00 — shouldn't happen in practice
    return "95-99c"


def _fee_for_price(price: float) -> float:
    """Kalshi fee per contract: FEE_RATE * min(price, 1 - price)."""
    return round(_FEE_RATE * min(price, 1.0 - price), 6)


def _side_edge(
    model_prob_for_side: float,
    entry_price:         float | None,
) -> dict[str, Any]:
    """Compute raw and fee-adjusted edge for one side."""
    if entry_price is None:
        return {"edge": None, "entry_price": None, "fee": None, "raw_edge": None}
    fee = _fee_for_price(entry_price)
    raw = round(model_prob_for_side - entry_price, 6)
    adj = round(raw - fee, 6)
    return {"edge": adj, "entry_price": entry_price, "fee": fee, "raw_edge": raw}


# ---------------------------------------------------------------------------
# Gate 1 — COMPLEMENT_SIDE_SCAN
# ---------------------------------------------------------------------------

def _run_complement_side_scan(
    model_probability:        float,
    normalized_book:          dict[str, Any],
    third_state_probability:  float | None,
    is_outright_market:       bool,
) -> dict[str, Any]:
    """
    Bidirectional YES/NO scoring.

    P(NO) = 1 - P(YES) only when settlement is genuinely binary.
    If a third settlement state exists (void/cancel/push/dead-heat/shortened),
    that state must be preserved and complement is flagged as approximate.
    Outright markets require full-field normalization (flagged).
    """
    yes_prob = round(model_probability, 4)
    no_prob  = round(1.0 - model_probability, 4)

    yes = _side_edge(yes_prob, normalized_book.get("best_yes_ask"))
    no  = _side_edge(no_prob,  normalized_book.get("best_no_ask"))

    has_third_state    = third_state_probability is not None and third_state_probability > 0
    complement_valid   = not has_third_state

    yes_e = yes["edge"]
    no_e  = no["edge"]
    if yes_e is not None and no_e is not None:
        best_side = "YES" if yes_e >= no_e else "NO"
        best_edge = max(yes_e, no_e)
    elif yes_e is not None:
        best_side, best_edge = "YES", yes_e
    elif no_e is not None:
        best_side, best_edge = "NO", no_e
    else:
        best_side, best_edge = None, None

    warnings: list[str] = []
    if has_third_state:
        warnings.append(
            f"THIRD_STATE_PRESENT: third_state_probability={third_state_probability:.4f}; "
            "P(NO) ≠ 1−P(YES); bidirectional complement is approximate only."
        )
    if is_outright_market:
        warnings.append(
            "FIELD_NORMALIZATION_REQUIRED: outright market — "
            "all-participant probabilities must sum to ≈1.00."
        )

    return {
        # Per-side edge
        "yes_entry_price":         yes["entry_price"],
        "yes_raw_edge":            yes["raw_edge"],
        "yes_edge":                yes_e,
        "yes_fee":                 yes["fee"],
        "no_entry_price":          no["entry_price"],
        "no_raw_edge":             no["raw_edge"],
        "no_edge":                 no_e,
        "no_fee":                  no["fee"],
        # Breakeven = entry price (binary contract; win pays $1)
        "yes_breakeven":           yes["entry_price"],
        "no_breakeven":            no["entry_price"],
        # Model probabilities
        "model_prob_yes":          yes_prob,
        "model_prob_no":           no_prob,
        # Best qualified side
        "best_qualified_side":     best_side,
        "best_qualified_edge":     best_edge,
        # Validity flags
        "void_state_preserved":    has_third_state,
        "complement_valid":        complement_valid,
        "is_outright_market":      is_outright_market,
        "warnings":                warnings,
    }


# ---------------------------------------------------------------------------
# Gate 2 — HIGH_PRICE_TAIL_RISK_GATE
# ---------------------------------------------------------------------------

def _run_high_price_tail_risk(
    entry_price:      float,
    fee_per_contract: float | None = None,
) -> dict[str, Any] | None:
    """
    For contracts at or above HIGH_PRICE_THRESHOLD (85¢), publish tail-loss metrics.

    Returns None when entry_price < HIGH_PRICE_THRESHOLD.

    A $20,000 position at 95¢ earns ~$1,052 before fees on a win but loses
    the full $20,000 on a miss. That is not free money — it is a high-win-rate
    negatively skewed exposure.
    """
    if entry_price < HIGH_PRICE_THRESHOLD:
        return None

    if fee_per_contract is None:
        fee_per_contract = _fee_for_price(entry_price)

    fee_adj_breakeven = round(entry_price + fee_per_contract, 6)
    net_profit_if_win = round(1.0 - entry_price - fee_per_contract, 6)
    maximum_loss      = round(entry_price, 4)

    if net_profit_if_win > 0:
        loss_to_win_ratio        = round(maximum_loss / net_profit_if_win, 4)
        wins_required_to_recover = math.ceil(maximum_loss / net_profit_if_win)
    else:
        loss_to_win_ratio        = float("inf")
        wins_required_to_recover = None   # no number of wins recovers

    is_extreme   = entry_price >= EXTREME_PRICE_THRESHOLD
    price_bucket = _price_bucket(entry_price)

    warnings: list[str] = []
    if wins_required_to_recover is not None:
        warnings.append(
            f"HIGH_PRICE_CONTRACT: entry_price={entry_price:.4f}; "
            f"one loss erases {wins_required_to_recover} wins."
        )
    else:
        warnings.append(
            f"HIGH_PRICE_CONTRACT: entry_price={entry_price:.4f}; "
            "net_profit_if_win ≤ 0 after fees — no number of wins recovers this position."
        )
    warnings.append(
        "HIGH_WIN_RATE_IS_NOT_POSITIVE_EV: frequent small wins do not offset "
        "rare large losses at this price level."
    )
    if is_extreme:
        warnings.append(
            f"EXTREME_PRICE_BUCKET ({price_bucket}): must be reviewed separately — "
            "tail calibration is hardest to obtain at 95–99¢."
        )

    return {
        "triggered":                         True,
        "entry_cost":                        round(entry_price, 4),
        "fee_per_contract":                  round(fee_per_contract, 6),
        "fee_adjusted_breakeven":            fee_adj_breakeven,
        "net_profit_if_win":                 net_profit_if_win,
        "maximum_loss":                      maximum_loss,
        "loss_to_win_ratio":                 loss_to_win_ratio,
        "wins_required_to_recover_one_loss": wins_required_to_recover,
        "price_bucket":                      price_bucket,
        "is_extreme_price_bucket":           is_extreme,
        "warnings":                          warnings,
    }


# ---------------------------------------------------------------------------
# Gate 3 — HISTORICAL_ZERO_FALLACY_BLOCK
# ---------------------------------------------------------------------------

def _run_historical_zero_fallacy(
    side_model_probability:      float,
    historical_occurrence_count: int | None,
    historical_sample_size:      int | None,
    logically_impossible:        bool = False,
    logically_certain:           bool = False,
) -> dict[str, Any]:
    """
    Guard against the historical-non-occurrence fallacy.

    Rules:
      probability == 0.0  without logically_impossible=True → BLOCKED
      probability == 1.0  without logically_certain=True    → BLOCKED
      occurrence_count == 0 with p > 0.0                   → WARNING only
      occurrence_count == 0 with p == 0.0                  → BLOCKED (covered above)

    "Never won before ≠ 100% NO" — historical non-occurrence is evidence, not a
    probability model. The replacement requires: current event model,
    comparable-condition sample, Bayesian smoothing or calibrated prior,
    market-family base rate, full-field normalization for outrights, and
    an uncertainty interval.
    """
    blocked  = False
    reasons: list[str] = []
    label:   str | None = None

    p = round(float(side_model_probability), 8)

    if p == 0.0 and not logically_impossible:
        blocked = True
        label   = LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED
        reasons.append(
            "PROBABILITY_ZERO_WITHOUT_LOGICAL_PROOF: model returned exactly 0.0. "
            "This is only valid when the outcome is logically impossible "
            "(e.g. eliminated bracket, deceased player, already-decided result). "
            "Required replacement: Bayesian smoothing or calibrated prior."
        )

    if p == 1.0 and not logically_certain:
        blocked = True
        label   = LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED
        reasons.append(
            "PROBABILITY_ONE_WITHOUT_LOGICAL_PROOF: model returned exactly 1.0. "
            "This is only valid when the outcome is already confirmed. "
            "Required replacement: calibrated confidence interval."
        )

    # Historical zero: warning when p > 0; block contributed above when p == 0
    if (
        historical_occurrence_count is not None
        and historical_occurrence_count == 0
        and historical_sample_size is not None
        and historical_sample_size > 0
        and not logically_impossible
    ):
        msg = (
            f"HISTORICAL_ZERO_OCCURRENCES: {historical_occurrence_count}/{historical_sample_size} "
            "observations — zero prior occurrences is evidence, not a probability model. "
            "Required: current event model, comparable-condition sample, "
            "Bayesian smoothing, market-family base rate, and uncertainty interval."
        )
        if p == 0.0:
            # Exact-zero + historical-zero: block already set; add detail
            reasons.append(msg)
        else:
            reasons.append("[WARNING] " + msg)

    return {
        "blocked":                      blocked,
        "label":                        label,
        "side_model_probability":       p,
        "historical_occurrence_count":  historical_occurrence_count,
        "historical_sample_size":       historical_sample_size,
        "logically_impossible":         logically_impossible,
        "logically_certain":            logically_certain,
        "reasons":                      reasons,
    }


# ---------------------------------------------------------------------------
# Gate 4 — VOLUME_IS_NOT_DEPTH
# ---------------------------------------------------------------------------

def _run_volume_is_not_depth(
    normalized_book: dict[str, Any],
    market_volume:   float | None,
) -> dict[str, Any]:
    """
    Enforce that cumulative market volume ≠ executable orderbook depth.

    Kalshi's order-book documentation says the current executable quantity is
    represented by resting orders at each price level. Cumulative trading volume
    does not tell you how much can be filled at the displayed price.

    A $53M market may have only $200 of depth at the best price.

    Required fields: depth_within_1c, depth_within_2c from orderbook_normalizer.
    """
    has_1c = normalized_book.get("depth_within_1c") is not None
    has_2c = normalized_book.get("depth_within_2c") is not None
    depth_validated = has_1c and has_2c

    violations: list[str] = []
    if not depth_validated:
        violations.append(
            "MISSING_ORDERBOOK_DEPTH: depth_within_1c and depth_within_2c are required; "
            "cumulative market_volume cannot substitute for live orderbook depth."
        )

    if market_volume is not None and not depth_validated:
        violations.append(
            f"VOLUME_PRESENTED_WITHOUT_DEPTH: market_volume={market_volume:,.0f} was provided "
            "but orderbook depth fields are absent. "
            "Volume ≠ executable quantity; a high-volume market may have thin resting depth."
        )

    return {
        "depth_validated":          depth_validated,
        "has_depth_within_1c":      has_1c,
        "has_depth_within_2c":      has_2c,
        "depth_within_1c":          normalized_book.get("depth_within_1c"),
        "depth_within_2c":          normalized_book.get("depth_within_2c"),
        "market_volume_provided":   market_volume is not None,
        "market_volume":            market_volume,
        "volume_is_not_depth_rule": True,   # permanently stamped; this rule never turns off
        "violations":               violations,
    }


# ---------------------------------------------------------------------------
# Lane ceiling logic
# ---------------------------------------------------------------------------

def _determine_patch_label(
    complement_scan: dict[str, Any],
    fallacy_result:  dict[str, Any],
    depth_result:    dict[str, Any],
    calibrated_lb:   float | None,
    side:            str,
) -> tuple[str, list[str]]:
    """
    Apply reviewer-specified lane ceilings in priority order.

    Priority:
      1. Historical zero fallacy (hard block regardless of other gates)
      2. Missing orderbook depth
      3. Uncalibrated model (no calibrated lower bound)
      4. Lower-bound edge test
      5. All pass → KALSHI_SINGLE_RESEARCH_ELIGIBLE
    """
    reasons: list[str] = []

    # Priority 1: historical zero fallacy hard block
    if fallacy_result["blocked"]:
        reasons.extend(fallacy_result["reasons"])
        return _LABEL_REJECT_NO_EDGE, reasons

    # Priority 2: missing orderbook depth
    if not depth_result["depth_validated"]:
        reasons.extend(depth_result["violations"])
        if calibrated_lb is not None:
            # Calibrated model but missing depth → UNOBTAINABLE
            return _LABEL_DATA_UNOBTAINABLE, reasons
        # Uncalibrated + missing depth → WATCH
        return _LABEL_WATCH, reasons

    # Priority 3: uncalibrated (no lower bound provided)
    if calibrated_lb is None:
        reasons.append(
            "UNCALIBRATED_NO_SCAN: calibrated_probability_lower_bound not provided. "
            "Cannot verify fee-adjusted edge — capped at KALSHI_WATCH."
        )
        return _LABEL_WATCH, reasons

    # Priority 4: lower-bound edge test
    if side.upper() == "YES":
        side_entry = complement_scan.get("yes_entry_price")
    else:
        side_entry = complement_scan.get("no_entry_price")

    side_adj_edge = complement_scan.get("yes_edge") if side.upper() == "YES" else complement_scan.get("no_edge")

    if side_entry is not None:
        fee = _fee_for_price(side_entry)
        fee_adj_breakeven = round(side_entry + fee, 6)
        lb_edge = round(calibrated_lb - fee_adj_breakeven, 6)
        if lb_edge <= 0:
            reasons.append(
                f"LOWER_BOUND_BELOW_FEE_ADJUSTED_BREAKEVEN: "
                f"calibrated_lb={calibrated_lb:.4f} ≤ fee_adj_breakeven={fee_adj_breakeven:.4f} "
                f"(entry={side_entry:.4f} + fee={fee:.4f})."
            )
            # Positive point edge but non-positive LB → REJECT
            if side_adj_edge is not None and side_adj_edge > 0:
                return _LABEL_REJECT_NO_EDGE, reasons
            return _LABEL_WATCH, reasons

    # Priority 5: all gates pass
    return _LABEL_RESEARCH_ELIGIBLE, reasons


# ---------------------------------------------------------------------------
# Calibration entry builder (Gate 5)
# ---------------------------------------------------------------------------

def build_calibration_entry(
    market_ticker:     str,
    event_ticker:      str                 = "",
    side_yes_no:       str                 = "NO",
    model_probability: float | None        = None,
    calibrated_lb:     float | None        = None,
    entry_price:       float | None        = None,
    patch_label:       str                = "",
    category:          str                = "",
    tail_risk:         dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a calibration entry dict for kalshi_no_side_calibration_ledger.log_entry().

    Not a DB write — caller decides whether to persist.
    """
    price_bucket = _price_bucket(entry_price) if entry_price is not None else None
    return {
        "market_ticker":          market_ticker,
        "event_ticker":           event_ticker,
        "side_yes_no":            side_yes_no.upper(),
        "model_probability":      model_probability,
        "calibrated_lb":          calibrated_lb,
        "entry_price":            entry_price,
        "price_bucket":           price_bucket,
        "patch_label":            patch_label,
        "category":               category,
        # Tail-risk fields flattened for easy DB insertion
        "loss_to_win_ratio":      tail_risk.get("loss_to_win_ratio")                  if tail_risk else None,
        "wins_required":          tail_risk.get("wins_required_to_recover_one_loss")  if tail_risk else None,
        "fee_adjusted_breakeven": tail_risk.get("fee_adjusted_breakeven")             if tail_risk else None,
        "is_high_price_contract": tail_risk is not None,
        "patch_id":               PATCH_ID,
        "mode":                   "paper",   # always paper during CANDIDATE status
    }


# ---------------------------------------------------------------------------
# Public API — run()
# ---------------------------------------------------------------------------

def run(
    model_probability:                  float | None,
    normalized_book:                    dict[str, Any],
    side:                               str             = "NO",
    category:                           str             = "sports",
    fee_detail:                         dict[str, Any] | None = None,
    calibrated_probability_lower_bound: float | None    = None,
    market_volume:                      float | None    = None,
    is_outright_market:                 bool            = False,
    historical_occurrence_count:        int | None      = None,
    historical_sample_size:             int | None      = None,
    third_state_probability:            float | None    = None,
    logically_impossible:               bool            = False,
    logically_certain:                  bool            = False,
    market_ticker:                      str             = "",
    event_ticker:                       str             = "",
) -> dict[str, Any]:
    """
    Run all five NO-side tail-risk gates and return a unified analysis block.

    Parameters
    ----------
    model_probability                   P(YES winning), 0–1. Required.
    normalized_book                     Output of orderbook_normalizer.normalize().
    side                                Intended trade side: YES or NO.
    category                            Market category (sports/weather/macro/…).
    fee_detail                          Output of fee_model.adjusted_edge() — optional.
    calibrated_probability_lower_bound  Model P(side) lower bound. Required for
                                        KALSHI_SINGLE_RESEARCH_ELIGIBLE.
    market_volume                       Cumulative trading volume (NOT depth).
                                        Included only to trigger VOLUME_IS_NOT_DEPTH check.
    is_outright_market                  True for tournament winner, MVP, etc.
    historical_occurrence_count         Past occurrences of this outcome (0 = never happened).
    historical_sample_size              Total observations window.
    third_state_probability             P(void/cancel/push/dead-heat).
    logically_impossible                Caller asserts P=0 is logically justified.
    logically_certain                   Caller asserts P=1 is already confirmed.
    market_ticker                       Used in calibration entry.
    event_ticker                        Used in calibration entry.

    Returns
    -------
    Dict with:
      patch_id, patch_status, can_execute (False), capital_allocation (False)
      patch_rules                — policy flags (all True/False constants)
      complement_side_scan       — bidirectional YES/NO scoring
      high_price_tail_risk       — tail metrics (None if price < HIGH_PRICE_THRESHOLD)
      historical_zero_analysis   — fallacy gate result
      depth_liquidity            — VOLUME_IS_NOT_DEPTH gate result
      patch_label                — lane-ceiling label string
      patch_blocking_reasons     — list of blocking reason strings
      no_side_calibration_entry  — dict for ledger insertion (Gate 5)
    """
    if model_probability is None:
        return {
            "patch_id":               PATCH_ID,
            "patch_status":           PATCH_STATUS,
            "can_execute":            False,
            "capital_allocation":     False,
            "patch_label":            _LABEL_DATA_UNOBTAINABLE,
            "patch_blocking_reasons": ["model_probability is None — cannot run patch gates"],
        }

    side_upper = (side or "NO").upper()

    # ── Gate 1: Complement side scan ─────────────────────────────────────────
    complement = _run_complement_side_scan(
        model_probability, normalized_book, third_state_probability, is_outright_market
    )

    # ── Gate 2: High-price tail risk ─────────────────────────────────────────
    if side_upper == "YES":
        side_entry_price = normalized_book.get("best_yes_ask")
    else:
        side_entry_price = normalized_book.get("best_no_ask")

    fee_per_contract = fee_detail.get("fee_per_contract") if fee_detail else None
    tail_risk = None
    if side_entry_price is not None:
        tail_risk = _run_high_price_tail_risk(side_entry_price, fee_per_contract)

    # ── Gate 3: Historical zero fallacy ──────────────────────────────────────
    side_model_prob = (
        round(1.0 - model_probability, 4)
        if side_upper == "NO"
        else round(model_probability, 4)
    )
    fallacy = _run_historical_zero_fallacy(
        side_model_prob,
        historical_occurrence_count,
        historical_sample_size,
        logically_impossible,
        logically_certain,
    )

    # ── Gate 4: Volume-is-not-depth ──────────────────────────────────────────
    depth_result = _run_volume_is_not_depth(normalized_book, market_volume)

    # ── Lane ceiling ─────────────────────────────────────────────────────────
    patch_label, blocking_reasons = _determine_patch_label(
        complement, fallacy, depth_result,
        calibrated_probability_lower_bound, side_upper
    )

    # ── Gate 5: Calibration entry ────────────────────────────────────────────
    cal_entry = build_calibration_entry(
        market_ticker     = market_ticker,
        event_ticker      = event_ticker,
        side_yes_no       = side_upper,
        model_probability = side_model_prob,
        calibrated_lb     = calibrated_probability_lower_bound,
        entry_price       = side_entry_price,
        patch_label       = patch_label,
        category          = category,
        tail_risk         = tail_risk,
    )

    return {
        "patch_id":                   PATCH_ID,
        "patch_status":               PATCH_STATUS,
        "can_execute":                False,
        "capital_allocation":         False,
        "patch_rules": {
            "FREE_MONEY_LANGUAGE_PROHIBITED":              True,
            "NO_SIDE_AUTO_EDGE":                           False,
            "HISTORICAL_ZERO_IS_NOT_ZERO_PROBABILITY":     True,
            "VOLUME_IS_NOT_EXECUTABLE_DEPTH":              True,
            "HIGH_WIN_RATE_IS_NOT_POSITIVE_EV":            True,
            "TAIL_LOSS_RATIO_REQUIRED":                    True,
            "PRICE_BUCKET_CALIBRATION_REQUIRED":           True,
            "FIELD_NORMALIZATION_REQUIRED_FOR_OUTRIGHTS":  True,
        },
        "complement_side_scan":        complement,
        "high_price_tail_risk":        tail_risk,
        "historical_zero_analysis":    fallacy,
        "depth_liquidity":             depth_result,
        "patch_label":                 patch_label,
        "patch_blocking_reasons":      blocking_reasons,
        "no_side_calibration_entry":   cal_entry,
    }
