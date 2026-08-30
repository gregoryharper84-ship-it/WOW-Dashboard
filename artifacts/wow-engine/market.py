"""
market.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.3

Exact two-way no-vig verification and market-prior blending.
market_prior_weight = 0.00 at launch, even when a valid two-way no-vig
market exists. A one-sided alternate price is disclosed as reference
data only and can NEVER populate market_prior_probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import math

W_MIN, W_MAX = 0.00, 0.35
LEARNING_GATE_MIN_SETTLED = 200


OPPOSING_SIDES = {
    "OVER": "UNDER", "UNDER": "OVER",
    "MORE": "LESS", "LESS": "MORE",
}

DEFAULT_MAX_STALENESS_SECONDS = 300      # pairwise: how far apart the two quotes may be
DEFAULT_MAX_QUOTE_AGE_SECONDS = 300      # absolute: how old a quote may be relative to as_of


@dataclass
class MarketQuote:
    side: str            # "OVER" / "UNDER" or "MORE" / "LESS"
    american_odds: float
    line: float
    settlement_basis: str
    retrieved_at: str    # ISO 8601
    participant: str
    stat: str
    period: str
    event_id: str
    provider: Optional[str] = None


def american_to_implied_prob(odds: float) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def _parse_ts(ts: str):
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def is_fresh_pair(a: MarketQuote, b: MarketQuote, max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS) -> bool:
    try:
        delta = abs((_parse_ts(a.retrieved_at) - _parse_ts(b.retrieved_at)).total_seconds())
    except (ValueError, TypeError):
        return False
    return delta <= max_staleness_seconds


def is_quote_fresh_as_of(quote: MarketQuote, as_of: str, max_quote_age_seconds: int = DEFAULT_MAX_QUOTE_AGE_SECONDS) -> bool:
    """Absolute freshness: how old the quote is relative to the scoring
    time (`as_of`), independent of how close it is to its paired quote.
    Two quotes retrieved seconds apart in January 2020 are "fresh relative
    to each other" but not fresh relative to a 2026 scoring run — this is
    the check `is_fresh_pair` alone cannot make."""
    try:
        delta = abs((_parse_ts(as_of) - _parse_ts(quote.retrieved_at)).total_seconds())
    except (ValueError, TypeError):
        return False
    return delta <= max_quote_age_seconds


def exact_match(
    a: MarketQuote,
    b: MarketQuote,
    max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS,
) -> bool:
    """Per 8A.3.2 / 8B.3: participant, stat, period, line, event,
    settlement basis must all match, the two sides must be genuinely
    opposing (not the same side quoted twice), and both quotes must be
    fresh relative to each other within the caller's requested window —
    NOT a hardcoded default the caller cannot tighten."""
    same_identity = (
        a.participant == b.participant
        and a.stat == b.stat
        and a.period == b.period
        and a.line == b.line
        and a.event_id == b.event_id
        and a.settlement_basis == b.settlement_basis
    )
    truly_opposing = OPPOSING_SIDES.get(a.side) == b.side
    return same_identity and truly_opposing and is_fresh_pair(a, b, max_staleness_seconds)


@dataclass
class MarketPriorResult:
    market_prior_available: bool
    market_prior_probability: Optional[float]
    market_prior_quality: str
    reference_market_probability_raw: Optional[float]
    reference_market_side: Optional[str]
    reference_market_price: Optional[float]


def resolve_market_prior(
    candidate_direction: str,     # "MORE"/"OVER" or "LESS"/"UNDER" — the side WOW's candidate is on
    side_a: Optional[MarketQuote],
    side_b: Optional[MarketQuote],
    as_of: Optional[str] = None,  # ISO 8601 scoring time — required for a governed market_prior
    max_staleness_seconds: int = DEFAULT_MAX_STALENESS_SECONDS,
    max_quote_age_seconds: int = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> MarketPriorResult:
    """
    Two matching, genuinely opposing, fresh quotes -> exact two-way no-vig
    market_prior, explicitly mapped to candidate_direction (never assumed
    to be whichever quote happened to be passed first). Anything less ->
    reference-only fields, market_prior_probability stays NULL.

    Freshness is enforced two ways, and both are required:
      1. pairwise (`max_staleness_seconds`, via exact_match/is_fresh_pair)
         — the two quotes must be close to each other in time.
      2. absolute (`max_quote_age_seconds`, via is_quote_fresh_as_of against
         `as_of`) — the quotes must also be close to the actual scoring
         time. Without this, two quotes seconds apart from years ago would
         pass check 1 and be treated as a live market. `as_of` is not
         invented here (no silent `now()` default) — a caller that omits
         it gets a blocked market_prior, same fail-closed pattern as a
         missing regime or a missing resampler.
    """
    if side_a is not None and side_b is None:
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality="SINGLE_SIDED_REFERENCE_ONLY",
            reference_market_probability_raw=american_to_implied_prob(side_a.american_odds),
            reference_market_side=side_a.side,
            reference_market_price=side_a.american_odds,
        )
    if side_a is None or side_b is None:
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality="NO_QUALIFYING_MARKET",
            reference_market_probability_raw=None,
            reference_market_side=None,
            reference_market_price=None,
        )
    if side_a.side == side_b.side:
        # Same side quoted twice is not a two-way market under any
        # circumstance, regardless of matching identity fields.
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality="INVALID_SAME_SIDE_PAIR",
            reference_market_probability_raw=american_to_implied_prob(side_a.american_odds),
            reference_market_side=side_a.side,
            reference_market_price=side_a.american_odds,
        )
    if not exact_match(side_a, side_b, max_staleness_seconds):
        quality = "STALE_MISMATCH" if not is_fresh_pair(side_a, side_b, max_staleness_seconds) else "SETTLEMENT_MISMATCH"
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality=quality,
            reference_market_probability_raw=american_to_implied_prob(side_a.american_odds),
            reference_market_side=side_a.side,
            reference_market_price=side_a.american_odds,
        )

    if as_of is None:
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality="MISSING_AS_OF_SCORING_TIME",
            reference_market_probability_raw=american_to_implied_prob(side_a.american_odds),
            reference_market_side=side_a.side,
            reference_market_price=side_a.american_odds,
        )
    if not is_quote_fresh_as_of(side_a, as_of, max_quote_age_seconds) or not is_quote_fresh_as_of(side_b, as_of, max_quote_age_seconds):
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality="STALE_RELATIVE_TO_SCORING_TIME",
            reference_market_probability_raw=american_to_implied_prob(side_a.american_odds),
            reference_market_side=side_a.side,
            reference_market_price=side_a.american_odds,
        )

    raw_a = american_to_implied_prob(side_a.american_odds)
    raw_b = american_to_implied_prob(side_b.american_odds)
    no_vig_a = raw_a / (raw_a + raw_b)

    quotes_by_side = {side_a.side: (side_a, no_vig_a), side_b.side: (side_b, 1 - no_vig_a)}
    if candidate_direction not in quotes_by_side:
        return MarketPriorResult(
            market_prior_available=False,
            market_prior_probability=None,
            market_prior_quality="CANDIDATE_DIRECTION_NOT_QUOTED",
            reference_market_probability_raw=raw_a,
            reference_market_side=side_a.side,
            reference_market_price=side_a.american_odds,
        )

    candidate_quote, candidate_no_vig = quotes_by_side[candidate_direction]

    return MarketPriorResult(
        market_prior_available=True,
        market_prior_probability=candidate_no_vig,
        market_prior_quality="EXACT_TWO_WAY_NO_VIG",
        reference_market_probability_raw=american_to_implied_prob(candidate_quote.american_odds),
        reference_market_side=candidate_quote.side,
        reference_market_price=candidate_quote.american_odds,
    )


@dataclass
class BlendResult:
    weight_used: float
    weight_source: str
    calibrated_probability: Optional[float]


def blend_market_prior(
    p_independent: float,
    market_prior: MarketPriorResult,
    settled_n_in_cohort: int,
    learned_weight: Optional[float] = None,
) -> BlendResult:
    """
    Cold start (settled_n_in_cohort < 200, or no learned_weight yet):
        w = 0.00 — independent model probability passes through unchanged.
    Once eligible (>=200 settled, out-of-fold-optimized weight supplied):
        log-odds stacking, w capped at 0.35.
    """
    if not market_prior.market_prior_available or market_prior.market_prior_probability is None:
        return BlendResult(weight_used=0.0, weight_source="NO_MARKET_PRIOR", calibrated_probability=p_independent)

    if settled_n_in_cohort < LEARNING_GATE_MIN_SETTLED or learned_weight is None:
        return BlendResult(weight_used=0.0, weight_source="COLD_START_ZERO_WEIGHT", calibrated_probability=p_independent)

    w = max(W_MIN, min(W_MAX, learned_weight))

    def logit(p: float) -> float:
        p = max(min(p, 1 - 1e-9), 1e-9)
        return math.log(p / (1 - p))

    def sigmoid(x: float) -> float:
        return 1 / (1 + math.exp(-x))

    blended_logit = (1 - w) * logit(p_independent) + w * logit(market_prior.market_prior_probability)
    return BlendResult(
        weight_used=w,
        weight_source="LEARNED_OUT_OF_FOLD",
        calibrated_probability=sigmoid(blended_logit),
    )
