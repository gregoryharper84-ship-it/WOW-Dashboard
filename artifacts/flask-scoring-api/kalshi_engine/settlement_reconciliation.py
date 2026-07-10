"""
settlement_reconciliation.py — Kalshi dry-run ledger settlement reconciliation
WOW-PATCH-2026-07-10-KALSHI-SETTLEMENT-CLV-RECONCILIATION

Purpose
-------
Post-market reconciliation layer for dry-run ledger rows created by
contract_execution_gate.py.  Only FILLED_DRY_RUN rows that have settled
and clear all field checks can enter calibration.  Every other path
(NO_FILL, PARTIAL, INVALID_STALE_BOOK, unsettled, data missing) is
explicitly excluded with a labelled reason — it cannot silently promote
a non-fill as a model win or loss.

Settlement status values
------------------------
  OPEN              — market is still trading
  CLOSED_UNSETTLED  — trading has stopped but official result not posted
  SETTLED           — result posted, settlement_value_cents known
  VOID_OR_CANCELLED — market cancelled; treated as PUSH_VOID
  DATA_UNOBTAINABLE — could not retrieve settlement data

Final result values
-------------------
  WIN                   — FILLED, settled, YES/NO side prevailed
  LOSS                  — FILLED, settled, YES/NO side lost
  PUSH_VOID             — market voided/cancelled
  NO_FILL               — fill_status was NO_FILL at decision time
  PARTIAL_FILL_EXCLUDED — fill_status was PARTIAL_FILL_DRY_RUN
  INVALID               — fill_status was INVALID_STALE_BOOK or field error
  UNSETTLED             — market not yet settled

CLV (Closing-price Value)
--------------------------
  clv_cents = closing_price_cents − hypothetical_fill_price_cents
  closing_price_cents must be the price for the SIDE being evaluated:
    YES rows → YES closing price
    NO rows  → NO closing price  (not the complement of the YES price)

Execution guarantee
-------------------
  can_execute  = False  (unconditional)
  dry_run_only = True   (unconditional)
  execution_rule = DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from .contract_execution_gate import (
    FILL_STATUS_FILLED,
    FILL_STATUS_NO_FILL,
    FILL_STATUS_PARTIAL,
    FILL_STATUS_STALE,
    _EXECUTION_RULE,
)

# ── Settlement status constants ───────────────────────────────────────────────
SS_OPEN             = "OPEN"
SS_CLOSED_UNSETTLED = "CLOSED_UNSETTLED"
SS_SETTLED          = "SETTLED"
SS_VOID             = "VOID_OR_CANCELLED"
SS_UNOBTAINABLE     = "DATA_UNOBTAINABLE"

# ── Final result constants ────────────────────────────────────────────────────
FR_WIN              = "WIN"
FR_LOSS             = "LOSS"
FR_PUSH_VOID        = "PUSH_VOID"
FR_NO_FILL          = "NO_FILL"
FR_PARTIAL          = "PARTIAL_FILL_EXCLUDED"
FR_INVALID          = "INVALID"
FR_UNSETTLED        = "UNSETTLED"

# ── Blocker tag constants ─────────────────────────────────────────────────────
BLK_SETTLEMENT_UNOBTAINABLE = "KALSHI_SETTLEMENT_DATA_UNOBTAINABLE"
BLK_NOT_SETTLED             = "KALSHI_MARKET_NOT_SETTLED"
BLK_CLOSING_UNOBTAINABLE    = "KALSHI_CLOSING_PRICE_UNOBTAINABLE"
BLK_PARTIAL_EXCLUDED        = "KALSHI_PARTIAL_FILL_EXCLUDED_FROM_CALIBRATION"
BLK_NO_FILL_EXCLUDED        = "KALSHI_NO_FILL_EXCLUDED_FROM_CALIBRATION"
BLK_INVALID_BOOK_EXCLUDED   = "KALSHI_INVALID_BOOK_EXCLUDED_FROM_CALIBRATION"
BLK_FIELD_MISSING           = "KALSHI_SETTLEMENT_FIELD_MISSING"
BLK_CLV_MISSING             = "KALSHI_CLV_FIELD_MISSING"

# ── Calibration eligibility ───────────────────────────────────────────────────
# A row is calibration_include=True only when ALL four conditions hold.
_CALIBRATION_REQUIRED_FINAL = frozenset({FR_WIN, FR_LOSS})


# ── Internal helper ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _settlement_value(side: str, yes_resolved: bool) -> float:
    """
    Map the YES-contract resolution outcome to a settlement value for the
    side being evaluated.

      YES side wins when yes_resolved=True  → 100 ¢
      NO  side wins when yes_resolved=False → 100 ¢  (YES lost)
    """
    if side == "YES":
        return 100.0 if yes_resolved else 0.0
    return 0.0 if yes_resolved else 100.0


# ── Public API ────────────────────────────────────────────────────────────────

def reconcile(
    market_ticker:                  str,
    event_id:                       Optional[str]   = None,
    side:                           str             = "YES",
    outcome:                        Optional[str]   = None,
    fill_status:                    str             = FILL_STATUS_NO_FILL,
    calibration_eligible:           bool            = False,
    hypothetical_fill_price_cents:  Optional[float] = None,
    effective_quantity_filled:      int             = 0,
    total_fee_cents:                Optional[float] = None,
    settlement_status:              Optional[str]   = None,
    yes_resolved:                   Optional[bool]  = None,
    closing_price_cents:            Optional[float] = None,
    settled_at:                     Optional[str]   = None,
    blockers_in:                    Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Reconcile a dry-run ledger row against market settlement data.

    Parameters
    ----------
    market_ticker                 Kalshi market ticker
    event_id                      Event ticker (informational)
    side                          "YES" or "NO" — the side that was evaluated
    outcome                       Team / outcome label for the side
    fill_status                   From contract_execution_gate (FILL_STATUS_*)
    calibration_eligible          Gate-time eligibility flag from evaluate()
    hypothetical_fill_price_cents Cents paid per contract if the order filled
    effective_quantity_filled     Contracts that filled at decision time
    total_fee_cents               Total taker fees for the filled quantity
    settlement_status             One of SS_* constants; None treated as OPEN
    yes_resolved                  True if the YES side prevailed; False if NO
                                  side prevailed; None if market not settled
    closing_price_cents           Closing price for the SIDE being evaluated:
                                    YES rows → YES closing price
                                    NO rows  → NO closing price (NOT 100−YES)
    settled_at                    ISO-8601 UTC timestamp of official settlement
    blockers_in                   Pre-existing blocker tags to carry forward

    Returns
    -------
    dict with all WOW-PATCH-2026-07-10 required reconciliation fields.
    can_execute and dry_run_only are unconditionally False / True.
    """
    blockers: list[str] = list(blockers_in or [])
    side_upper = (side or "YES").upper()
    eff_settlement_status = settlement_status or SS_OPEN

    # ── Base scaffolding (shared across all paths) ────────────────────────────
    base: dict[str, Any] = {
        "market_ticker":                    market_ticker,
        "event_id":                         event_id,
        "side":                             side_upper,
        "outcome":                          outcome,
        "fill_status":                      fill_status,
        "calibration_eligible":             calibration_eligible,
        "hypothetical_fill_price_cents":    hypothetical_fill_price_cents,
        "effective_quantity_filled":        effective_quantity_filled,
        "total_fee_cents":                  total_fee_cents,
        "settled_at":                       settled_at,
        "reconciled_at":                    _now_iso(),
        # Unconditional safety flags
        "can_execute":                      False,
        "dry_run_only":                     True,
        "execution_rule":                   _EXECUTION_RULE,
    }

    # ── Non-FILLED paths: short-circuit, no P&L math ─────────────────────────

    if fill_status == FILL_STATUS_NO_FILL:
        blockers.append(BLK_NO_FILL_EXCLUDED)
        return {
            **base,
            "settlement_status":            eff_settlement_status,
            "closing_price_cents":          None,
            "settlement_value_cents":       None,
            "gross_pnl_cents":              None,
            "net_pnl_after_fees_cents":     None,
            "clv_cents":                    None,
            "clv_percent":                  None,
            "final_result":                 FR_NO_FILL,
            "calibration_include":          False,
            "calibration_exclusion_reason": "fill_status is NO_FILL",
            "blockers":                     sorted(set(blockers)),
        }

    if fill_status == FILL_STATUS_PARTIAL:
        blockers.append(BLK_PARTIAL_EXCLUDED)
        return {
            **base,
            "settlement_status":            eff_settlement_status,
            "closing_price_cents":          None,
            "settlement_value_cents":       None,
            "gross_pnl_cents":              None,
            "net_pnl_after_fees_cents":     None,
            "clv_cents":                    None,
            "clv_percent":                  None,
            "final_result":                 FR_PARTIAL,
            "calibration_include":          False,
            "calibration_exclusion_reason": "fill_status is PARTIAL_FILL_DRY_RUN",
            "blockers":                     sorted(set(blockers)),
        }

    if fill_status == FILL_STATUS_STALE:
        blockers.append(BLK_INVALID_BOOK_EXCLUDED)
        return {
            **base,
            "settlement_status":            eff_settlement_status,
            "closing_price_cents":          None,
            "settlement_value_cents":       None,
            "gross_pnl_cents":              None,
            "net_pnl_after_fees_cents":     None,
            "clv_cents":                    None,
            "clv_percent":                  None,
            "final_result":                 FR_INVALID,
            "calibration_include":          False,
            "calibration_exclusion_reason": "fill_status is INVALID_STALE_BOOK",
            "blockers":                     sorted(set(blockers)),
        }

    # ── FILLED_DRY_RUN path ───────────────────────────────────────────────────

    # Settlement data unobtainable
    if eff_settlement_status == SS_UNOBTAINABLE:
        blockers.append(BLK_SETTLEMENT_UNOBTAINABLE)
        return {
            **base,
            "settlement_status":            SS_UNOBTAINABLE,
            "closing_price_cents":          closing_price_cents,
            "settlement_value_cents":       None,
            "gross_pnl_cents":              None,
            "net_pnl_after_fees_cents":     None,
            "clv_cents":                    None,
            "clv_percent":                  None,
            "final_result":                 FR_INVALID,
            "calibration_include":          False,
            "calibration_exclusion_reason": "settlement data unobtainable",
            "blockers":                     sorted(set(blockers)),
        }

    # Market not yet settled (open or closed-pending)
    if eff_settlement_status in (SS_OPEN, SS_CLOSED_UNSETTLED):
        blockers.append(BLK_NOT_SETTLED)
        return {
            **base,
            "settlement_status":            eff_settlement_status,
            "closing_price_cents":          closing_price_cents,
            "settlement_value_cents":       None,
            "gross_pnl_cents":              None,
            "net_pnl_after_fees_cents":     None,
            "clv_cents":                    None,
            "clv_percent":                  None,
            "final_result":                 FR_UNSETTLED,
            "calibration_include":          False,
            "calibration_exclusion_reason": "market not yet settled",
            "blockers":                     sorted(set(blockers)),
        }

    # Void / cancelled
    if eff_settlement_status == SS_VOID:
        return {
            **base,
            "settlement_status":            SS_VOID,
            "closing_price_cents":          closing_price_cents,
            "settlement_value_cents":       None,
            "gross_pnl_cents":              None,
            "net_pnl_after_fees_cents":     None,
            "clv_cents":                    None,
            "clv_percent":                  None,
            "final_result":                 FR_PUSH_VOID,
            "calibration_include":          False,
            "calibration_exclusion_reason": "market voided or cancelled",
            "blockers":                     sorted(set(blockers)),
        }

    # ── SETTLED path ──────────────────────────────────────────────────────────

    # Settlement value
    settlement_value_cents: Optional[float] = None
    if yes_resolved is not None:
        settlement_value_cents = _settlement_value(side_upper, yes_resolved)

    # P&L (requires all four price fields)
    price_fields_ok = (
        hypothetical_fill_price_cents is not None
        and effective_quantity_filled > 0
        and total_fee_cents is not None
        and settlement_value_cents is not None
    )
    gross_pnl_cents:          Optional[float] = None
    net_pnl_after_fees_cents: Optional[float] = None

    if not price_fields_ok:
        blockers.append(BLK_FIELD_MISSING)
    else:
        gross_pnl_cents = round(
            (settlement_value_cents - hypothetical_fill_price_cents)   # type: ignore[operator]
            * effective_quantity_filled,
            4,
        )
        net_pnl_after_fees_cents = round(gross_pnl_cents - total_fee_cents, 4)   # type: ignore[operator]

    # Final result
    if not price_fields_ok or settlement_value_cents is None:
        final_result = FR_INVALID
    elif settlement_value_cents == 100.0:
        final_result = FR_WIN
    elif settlement_value_cents == 0.0:
        final_result = FR_LOSS
    else:
        final_result = FR_INVALID
        blockers.append(BLK_FIELD_MISSING)

    # CLV — closing_price_cents must be for the SIDE being evaluated
    clv_cents:    Optional[float] = None
    clv_percent:  Optional[float] = None
    if closing_price_cents is None:
        blockers.append(BLK_CLV_MISSING)
    elif hypothetical_fill_price_cents is not None:
        clv_cents   = round(closing_price_cents - hypothetical_fill_price_cents, 4)
        clv_percent = round(clv_cents / 100.0, 6)

    # Calibration gate — all four conditions must hold
    calibration_include = (
        fill_status        == FILL_STATUS_FILLED
        and eff_settlement_status == SS_SETTLED
        and final_result   in _CALIBRATION_REQUIRED_FINAL
        and price_fields_ok
    )

    if calibration_include:
        calibration_exclusion_reason: Optional[str] = None
    elif not price_fields_ok:
        calibration_exclusion_reason = "required price or fee fields missing"
    elif final_result not in _CALIBRATION_REQUIRED_FINAL:
        calibration_exclusion_reason = f"final_result={final_result} is not WIN or LOSS"
    else:
        calibration_exclusion_reason = "calibration conditions not met"

    return {
        **base,
        "settlement_status":            SS_SETTLED,
        "closing_price_cents":          closing_price_cents,
        "settlement_value_cents":       settlement_value_cents,
        "gross_pnl_cents":              gross_pnl_cents,
        "net_pnl_after_fees_cents":     net_pnl_after_fees_cents,
        "clv_cents":                    clv_cents,
        "clv_percent":                  clv_percent,
        "final_result":                 final_result,
        "calibration_include":          calibration_include,
        "calibration_exclusion_reason": calibration_exclusion_reason,
        "blockers":                     sorted(set(blockers)),
    }
