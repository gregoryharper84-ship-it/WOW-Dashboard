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
    leg_results: Optional[Sequence[str]] = None,
    externally_verified_receipt: bool = False,
    authenticated_receipt_ref: Optional[str] = None,
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
    if not isinstance(all_other_legs_won, bool):
        raise ValueError("all_other_legs_won must be boolean")
    if not isinstance(externally_verified_receipt, bool):
        raise ValueError("externally_verified_receipt must be boolean")
    if externally_verified_receipt and not authenticated_receipt_ref:
        raise ValueError("authenticated receipt reference required when externally verified")
    if leg_results is not None:
        if not leg_results or any(not isinstance(v, str) or v.upper() not in
                                  {"WIN", "PENDING", "LOSS", "VOID", "PUSH"} for v in leg_results):
            raise ValueError("leg_results must contain only WIN/PENDING/LOSS/VOID/PUSH")
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
        "attested_conditional_break_even": None,
        "hypothetical_single_remaining_leg_break_even": None,
        "conditional_evidence_status": "NOT_EVALUATED",
        "cashout_quote_integrity": (
            "OFFER_EXCEEDS_FULL_PAYOUT_REQUIRES_AUDIT" if c > gross else "ARITHMETIC_OK"
        ),
    }
    # The caller's boolean/screenshot observation is NOT authoritative settlement.
    # It can support a labeled thought experiment, never a verified conditional price.
    scenario_ready = all_other_legs_won and unresolved_legs == 1
    if leg_results is not None:
        outcomes = [v.upper() for v in leg_results]
        out["leg_outcome_counts"] = {v: outcomes.count(v) for v in ("WIN", "PENDING", "LOSS", "VOID", "PUSH")}
        scenario_ready = outcomes.count("PENDING") == 1 and outcomes.count("WIN") == len(outcomes) - 1
    if scenario_ready:
        out["hypothetical_single_remaining_leg_break_even"] = str(c / gross)
        out["conditional_evidence_status"] = "SCENARIO_ONLY_SOURCE_UNVERIFIED"
    if externally_verified_receipt:
        # An authenticated upstream verifier must provide exact leg states and
        # receipt identity. Presence of a string alone must not unlock a result.
        if leg_results is None:
            out["conditional_evidence_status"] = "VERIFIED_RECEIPT_MISSING_EXACT_LEG_RESULTS"
        elif scenario_ready:
            # No independent verifier lives in this arithmetic-only module.
            # Caller attestation cannot upgrade a scenario to verified fact.
            out["attested_conditional_break_even"] = str(c / gross)
            out["conditional_evidence_status"] = "CALLER_ATTESTED_NOT_AUTHENTICATED"
        else:
            out["conditional_evidence_status"] = "NONWIN_VOID_OR_MULTIPLE_UNRESOLVED"
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
