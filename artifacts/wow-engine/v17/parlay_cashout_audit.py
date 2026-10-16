"""WOW V17 experimental sportsbook parlay/cash-out arithmetic audit.

Price-derived diagnostics only. NEVER a sporting probability model, and no
betting execution, model qualification, or terminal-reducer authority.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Sequence

CAN_EXECUTE = False
CENT = Decimal("0.01")


def money(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("amount must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("amount must be finite and nonnegative")
    return result


def american_decimal(odds: int) -> Decimal:
    if isinstance(odds, bool) or not isinstance(odds, int) or -100 < odds < 100:
        raise ValueError("American odds must be an integer >=+100 or <=-100")
    if odds > 0:
        return Decimal(1) + Decimal(odds) / Decimal(100)
    return Decimal(1) + Decimal(100) / Decimal(-odds)


def audit_parlay(
    *,
    stake: object,
    ticket_american_odds: int,
    cashout: object,
    leg_american_odds: Optional[Sequence[Optional[int]]] = None,
    all_other_legs_won: bool = False,
    unresolved_legs: int = 1,
) -> dict:
    """Compute MARKET arithmetic. Conditional ratio assumes one remaining leg.

    Differences in odds can result from correlated/boosted pricing, timing, or
    partially obscured input; mismatches call for a source audit, not blame.
    """
    s, c = money(stake), money(cashout)
    if s <= 0:
        raise ValueError("stake must be positive")
    if isinstance(unresolved_legs, bool) or not isinstance(unresolved_legs, int) or unresolved_legs < 0:
        raise ValueError("unresolved_legs must be a nonnegative integer")
    ticket_decimal = american_decimal(ticket_american_odds)
    gross = (s * ticket_decimal).quantize(CENT, rounding=ROUND_HALF_UP)
    out = {
        "status": "RESEARCH_ONLY_MARKET_ARITHMETIC",
        "can_execute": False,
        "stake": str(s.quantize(CENT, rounding=ROUND_HALF_UP)),
        "ticket_decimal_odds": str(ticket_decimal),
        "gross_full_return": str(gross),
        "net_full_profit": str((gross - s).quantize(CENT, rounding=ROUND_HALF_UP)),
        "reported_cashout": str(c.quantize(CENT, rounding=ROUND_HALF_UP)),
        "net_cashout_profit": str((c - s).quantize(CENT, rounding=ROUND_HALF_UP)),
        "cashout_fraction_of_full_return": str(c / gross),
        "price_implied_joint_break_even": str(Decimal(1) / ticket_decimal),
        "governed_joint_probability": None,
        "governed_lower_bound": None,
        "cashout_evaluation": "NO_CERTIFIED_CONDITIONAL_MODEL_PROBABILITY",
        "book_price_reconciliation": "NOT_EVALUATED",
        "conditional_remaining_leg_break_even": None,
    }
    if all_other_legs_won and unresolved_legs == 1:
        out["conditional_remaining_leg_break_even"] = str(c / gross)
    if leg_american_odds is not None:
        if not leg_american_odds or any(x is None for x in leg_american_odds):
            out["book_price_reconciliation"] = "PRICE_EVIDENCE_INCOMPLETE"
        else:
            p = Decimal(1)
            for value in leg_american_odds:
                p *= american_decimal(value)
            out["leg_price_product"] = str(p)
            out["book_price_reconciliation"] = (
                "MULTIPLIER_MATCH" if abs(p - ticket_decimal) <= Decimal("0.01")
                else "MULTIPLIER_MISMATCH_REQUIRES_SOURCE_AUDIT"
            )
    return out
