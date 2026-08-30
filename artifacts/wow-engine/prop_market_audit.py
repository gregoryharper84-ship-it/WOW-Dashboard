"""Candidate-to-market identity audit for governed prop scoring.

Two quotes matching each other is insufficient. Before a quote can enter the
market-prior/no-vig path it must also match the exact model candidate contract.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from market import MarketQuote
from prop_settlement import LINE_MISMATCH, NO_VIG_UNAVAILABLE, SETTLEMENT_RULE_UNRESOLVED, SettlementRule, audit_exact_line

MARKET_IDENTITY_MISMATCH = "WOW_HOLD_MARKET_IDENTITY_MISMATCH"
MARKET_SIDE_INVALID = "WOW_HOLD_MARKET_SIDE_INVALID"

_SIDE_ALIASES = {
    "MORE": "MORE",
    "OVER": "MORE",
    "LESS": "LESS",
    "UNDER": "LESS",
}


@dataclass(frozen=True)
class CandidateMarketAudit:
    status: str
    blocker: Optional[str]
    side_a: Optional[MarketQuote]
    side_b: Optional[MarketQuote]
    can_use_two_way_no_vig: bool
    can_execute: bool = False


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def _canonical_quote(quote: MarketQuote) -> Optional[MarketQuote]:
    canonical_side = _SIDE_ALIASES.get(_norm(quote.side))
    if canonical_side is None:
        return None
    return replace(quote, side=canonical_side)


def _quote_matches_candidate(
    quote: MarketQuote,
    *,
    event_id: str,
    participant: str,
    stat: str,
    period: str,
    line: float,
    settlement_basis: str,
    line_tolerance: float,
) -> tuple[bool, Optional[str]]:
    if not audit_exact_line(candidate_line=line, quote_line=quote.line, tolerance=line_tolerance):
        return False, LINE_MISMATCH
    if (
        str(quote.event_id) != str(event_id)
        or _norm(quote.participant) != _norm(participant)
        or _norm(quote.stat) != _norm(stat)
        or _norm(quote.period) != _norm(period)
        or _norm(quote.settlement_basis) != _norm(settlement_basis)
    ):
        return False, MARKET_IDENTITY_MISMATCH
    return True, None


def audit_candidate_market(
    *,
    event_id: str,
    participant: str,
    stat: str,
    period: str,
    line: float,
    settlement_rule: Optional[SettlementRule],
    side_a: Optional[MarketQuote],
    side_b: Optional[MarketQuote],
    line_tolerance: float = 0.0,
) -> CandidateMarketAudit:
    if settlement_rule is None:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=SETTLEMENT_RULE_UNRESOLVED,
            side_a=None,
            side_b=None,
            can_use_two_way_no_vig=False,
        )

    supplied = [quote for quote in (side_a, side_b) if quote is not None]
    if not supplied:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=NO_VIG_UNAVAILABLE,
            side_a=None,
            side_b=None,
            can_use_two_way_no_vig=False,
        )

    for quote in supplied:
        matched, blocker = _quote_matches_candidate(
            quote,
            event_id=event_id,
            participant=participant,
            stat=stat,
            period=period,
            line=line,
            settlement_basis=settlement_rule.settlement_basis,
            line_tolerance=line_tolerance,
        )
        if not matched:
            # Quarantine the entire quote set. A mismatched quote cannot remain
            # even as a reference input to a governed candidate.
            return CandidateMarketAudit(
                status="HOLD",
                blocker=blocker,
                side_a=None,
                side_b=None,
                can_use_two_way_no_vig=False,
            )

    canonical_a = _canonical_quote(side_a) if side_a is not None else None
    canonical_b = _canonical_quote(side_b) if side_b is not None else None
    if (side_a is not None and canonical_a is None) or (side_b is not None and canonical_b is None):
        return CandidateMarketAudit(
            status="HOLD",
            blocker=MARKET_SIDE_INVALID,
            side_a=None,
            side_b=None,
            can_use_two_way_no_vig=False,
        )

    if canonical_a is None or canonical_b is None:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=NO_VIG_UNAVAILABLE,
            side_a=canonical_a,
            side_b=canonical_b,
            can_use_two_way_no_vig=False,
        )

    if canonical_a.side == canonical_b.side:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=NO_VIG_UNAVAILABLE,
            side_a=canonical_a,
            side_b=canonical_b,
            can_use_two_way_no_vig=False,
        )

    return CandidateMarketAudit(
        status="PASS",
        blocker=None,
        side_a=canonical_a,
        side_b=canonical_b,
        can_use_two_way_no_vig=True,
    )
