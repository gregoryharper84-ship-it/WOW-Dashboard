"""Candidate-to-market identity audit for governed prop scoring.

Exact-line and adjacent-line evidence are distinct contracts. Adjacent quotes may
be retained as reference/distribution context, but they can never enter the
operative two-way no-vig prior or satisfy exact-line market confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from market import MarketQuote
from prop_settlement import LINE_MISMATCH, NO_VIG_UNAVAILABLE, SettlementRule, audit_exact_line

MARKET_IDENTITY_MISMATCH = "WOW_HOLD_MARKET_IDENTITY_MISMATCH"
MARKET_SIDE_INVALID = "WOW_HOLD_MARKET_SIDE_INVALID"
EXACT_LINE = "EXACT_LINE"
ADJACENT_LINE = "ADJACENT_LINE"
NO_MARKET = "NO_MARKET"
# Adjacent prices are descriptive context only. The haircut is emitted for
# downstream research/ranking telemetry; it is deliberately not applied to the
# sporting model probability here.
ADJACENT_LINE_REFERENCE_WEIGHT = 0.75

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
    evidence_class: str = NO_MARKET
    line_distance: Optional[float] = None
    reference_side_a: Optional[MarketQuote] = None
    reference_side_b: Optional[MarketQuote] = None
    reference_weight: float = 0.0
    exact_line_market_confirmed: bool = False
    approval_ceiling: Optional[str] = None
    can_execute: bool = False


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def _canonical_quote(quote: MarketQuote) -> Optional[MarketQuote]:
    canonical_side = _SIDE_ALIASES.get(_norm(quote.side))
    if canonical_side is None:
        return None
    return replace(quote, side=canonical_side)


def _identity_matches_candidate(
    quote: MarketQuote,
    *,
    event_id: str,
    participant: str,
    stat: str,
    period: str,
    settlement_basis: Optional[str],
) -> bool:
    if (
        str(quote.event_id) != str(event_id)
        or _norm(quote.participant) != _norm(participant)
        or _norm(quote.stat) != _norm(stat)
        or _norm(quote.period) != _norm(period)
    ):
        return False
    if settlement_basis is not None and _norm(quote.settlement_basis) != _norm(settlement_basis):
        return False
    return True


def _quote_matches_candidate(
    quote: MarketQuote,
    *,
    event_id: str,
    participant: str,
    stat: str,
    period: str,
    line: float,
    settlement_basis: Optional[str],
    line_tolerance: float,
) -> tuple[bool, Optional[str]]:
    if not _identity_matches_candidate(
        quote,
        event_id=event_id,
        participant=participant,
        stat=stat,
        period=period,
        settlement_basis=settlement_basis,
    ):
        return False, MARKET_IDENTITY_MISMATCH
    if not audit_exact_line(candidate_line=line, quote_line=quote.line, tolerance=line_tolerance):
        return False, LINE_MISMATCH
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
    supplied = [quote for quote in (side_a, side_b) if quote is not None]
    if not supplied:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=NO_VIG_UNAVAILABLE,
            side_a=None,
            side_b=None,
            can_use_two_way_no_vig=False,
            evidence_class=NO_MARKET,
        )

    expected_settlement_basis = settlement_rule.settlement_basis if settlement_rule is not None else None

    # First lock non-line identity. A wrong event/player/stat/period is not
    # adjacent evidence; it is unrelated evidence and is quarantined entirely.
    if any(
        not _identity_matches_candidate(
            quote,
            event_id=event_id,
            participant=participant,
            stat=stat,
            period=period,
            settlement_basis=expected_settlement_basis,
        )
        for quote in supplied
    ):
        return CandidateMarketAudit(
            status="HOLD",
            blocker=MARKET_IDENTITY_MISMATCH,
            side_a=None,
            side_b=None,
            can_use_two_way_no_vig=False,
            evidence_class=NO_MARKET,
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
            evidence_class=NO_MARKET,
        )

    line_distances = [abs(float(quote.line) - float(line)) for quote in supplied]
    max_line_distance = max(line_distances) if line_distances else None
    all_exact = all(
        audit_exact_line(candidate_line=line, quote_line=quote.line, tolerance=line_tolerance)
        for quote in supplied
    )

    if not all_exact:
        # Preserve same-market adjacent quotes only as reference context. Most
        # importantly, operative side_a/side_b are None, so the discrete fitted
        # provider cannot ingest the adjacent sportsbook line as an exact-line
        # no-vig prior.
        return CandidateMarketAudit(
            status="HOLD",
            blocker=LINE_MISMATCH,
            side_a=None,
            side_b=None,
            can_use_two_way_no_vig=False,
            evidence_class=ADJACENT_LINE,
            line_distance=max_line_distance,
            reference_side_a=canonical_a,
            reference_side_b=canonical_b,
            reference_weight=ADJACENT_LINE_REFERENCE_WEIGHT,
            exact_line_market_confirmed=False,
            approval_ceiling="MODEL_QUALIFIED_HOLD",
        )

    # Preserve the explicit match helper as a second fail-closed assertion for
    # exact evidence, including future line-tolerance changes.
    for quote in supplied:
        matched, blocker = _quote_matches_candidate(
            quote,
            event_id=event_id,
            participant=participant,
            stat=stat,
            period=period,
            line=line,
            settlement_basis=expected_settlement_basis,
            line_tolerance=line_tolerance,
        )
        if not matched:
            return CandidateMarketAudit(
                status="HOLD",
                blocker=blocker,
                side_a=None,
                side_b=None,
                can_use_two_way_no_vig=False,
                evidence_class=NO_MARKET,
            )

    if canonical_a is None or canonical_b is None:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=NO_VIG_UNAVAILABLE,
            side_a=canonical_a,
            side_b=canonical_b,
            can_use_two_way_no_vig=False,
            evidence_class=EXACT_LINE,
            line_distance=0.0,
        )

    if canonical_a.side == canonical_b.side:
        return CandidateMarketAudit(
            status="HOLD",
            blocker=NO_VIG_UNAVAILABLE,
            side_a=canonical_a,
            side_b=canonical_b,
            can_use_two_way_no_vig=False,
            evidence_class=EXACT_LINE,
            line_distance=0.0,
        )

    return CandidateMarketAudit(
        status="PASS",
        blocker=None,
        side_a=canonical_a,
        side_b=canonical_b,
        can_use_two_way_no_vig=True,
        evidence_class=EXACT_LINE,
        line_distance=0.0,
        reference_side_a=canonical_a,
        reference_side_b=canonical_b,
        reference_weight=1.0,
        exact_line_market_confirmed=True,
    )
