"""Read-only, point-in-time market diagnostics for V17 upset research.

Never creates a sporting probability, changes an existing fitted outcome,
certifies a model, changes admission/ranking, or executes a wager. A market
estimate is intentionally NOT a substitute for a governed sporting model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
MUTATES_SPORTING_PROBABILITY = False
GRANTS_MODEL_CERTIFICATION = False
GRANTS_RANK_ELIGIBILITY = False
CONTRACT = "V17_UPSET_MARKET_DIAGNOSTICS_RESEARCH_V1"


def _timestamp(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("MARKET_TIMESTAMP_MISSING")
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("MARKET_TIMESTAMP_INVALID") from exc
    if ts.utcoffset() is None:
        raise ValueError("MARKET_TIMESTAMP_MUST_BE_TZ_AWARE")
    return ts.astimezone(timezone.utc)


def _probability(raw: Any, code: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(code)
    value = float(raw)
    if not isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(code)
    return value


def _american_implied(raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("MARKET_ODDS_INVALID")
    odds = float(raw)
    if not isfinite(odds) or abs(odds) < 100:
        raise ValueError("MARKET_ODDS_INVALID")
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def no_vig_probabilities(
    american_odds: Mapping[str, float],
    *,
    method: str = "power",
) -> dict[str, float]:
    """Explicit, deterministic margin removal for exact two/three-way markets.

    'power' solves sum(raw_i ** exponent) == 1, pinned as this
    RESEARCH contract's primary reference; 'multiplicative' divides
    probabilities by their sum and is strictly a sensitivity comparator.
    Their longshot results differ. Neither constitutes a model or
    independently validated sporting information edge.
    """
    if method not in {"power", "multiplicative"}:
        raise ValueError("MARKET_NO_VIG_METHOD_UNSUPPORTED")
    if len(american_odds) not in (2, 3):
        raise ValueError("MARKET_OUTCOME_SPACE_INVALID")
    raw = {str(k): _american_implied(v) for k, v in american_odds.items()}
    if any(not key.strip() for key in raw):
        raise ValueError("MARKET_OUTCOME_LABEL_MISSING")
    total = sum(raw.values())
    if total < 1.0 - 1e-9:
        raise ValueError("MARKET_UNDERROUND_UNSUPPORTED")
    if method == "multiplicative":
        return {key: value / total for key, value in raw.items()}
    lower, upper = 1.0, 64.0
    # Extreme but representable prices may require a power exponent above
    # 64. Never silently normalize an unbracketed bisection result, which
    # would manufacture a distorted market reference probability.
    while sum(value ** upper for value in raw.values()) > 1.0 and upper < 1048576.0:
        lower, upper = upper, upper * 2.0
    if sum(value ** upper for value in raw.values()) > 1.0:
        raise ValueError("MARKET_POWER_NO_VIG_NOT_CONVERGED")
    for _ in range(90):
        exponent = (lower + upper) / 2.0
        if sum(value ** exponent for value in raw.values()) > 1.0:
            lower = exponent
        else:
            upper = exponent
    exponent = (lower + upper) / 2.0
    adjusted = {key: value ** exponent for key, value in raw.items()}
    normalizer = sum(adjusted.values())
    if normalizer <= 0.0 or not isfinite(normalizer) or abs(normalizer - 1.0) > 1e-8:
        raise ValueError("MARKET_POWER_NO_VIG_NOT_CONVERGED")
    return {key: value / normalizer for key, value in adjusted.items()}


def analyze_upset_market_snapshot(
    *,
    official_event_id: str,
    market_type: str,
    settlement_basis: str,
    event_start_time_utc: str,
    decision_time_utc: str,
    book_quotes: Iterable[Mapping[str, Any]],
    governed_outcomes: Mapping[str, Mapping[str, float]],
    governed_receipt_id: str,
    method: str = "power",
    max_quote_age_minutes: int = 60,
    empirical_reference_probs: Mapping[str, float] | None = None,
    reference_class_receipt_id: str | None = None,
    reference_class_as_of_utc: str | None = None,
) -> dict[str, Any]:
    """Evaluate source-visible market disagreement, never a certified edge.

    Each book quote requires official_event_id, market_type, settlement_basis,
    book, source_snapshot_id, captured_at_utc, and
    american_odds for the complete matching market outcome space. Historical
    empirical reference probabilities require a separately identified,
    pre-decision reference-class receipt. No informational edge is assumed
    merely because the model disagrees with a bookmaker.
    """
    if not all(isinstance(x, str) and x.strip() for x in (
        official_event_id, market_type, settlement_basis, governed_receipt_id,
    )):
        raise ValueError("MARKET_IDENTITY_OR_RECEIPT_MISSING")
    decision, start = _timestamp(decision_time_utc), _timestamp(event_start_time_utc)
    if decision >= start:
        raise ValueError("MARKET_DECISION_NOT_PREGAME")
    if isinstance(max_quote_age_minutes, bool) or not isinstance(max_quote_age_minutes, int) or max_quote_age_minutes < 0:
        raise ValueError("MARKET_MAX_QUOTE_AGE_INVALID")
    labels = set(governed_outcomes)
    if len(labels) not in (2, 3) or any(not str(label).strip() for label in labels):
        raise ValueError("MARKET_GOVERNED_OUTCOME_SPACE_INVALID")
    model = {}
    for label, data in governed_outcomes.items():
        p = _probability(data["calibrated_probability"], "MARKET_MODEL_PROBABILITY_INVALID")
        lo = _probability(data["calibrated_lower_bound"], "MARKET_MODEL_BOUNDS_INVALID")
        hi = _probability(data["calibrated_upper_bound"], "MARKET_MODEL_BOUNDS_INVALID")
        if not lo <= p <= hi:
            raise ValueError("MARKET_MODEL_BOUNDS_INVALID")
        model[label] = (p, lo, hi)
    if not isclose(sum(x[0] for x in model.values()), 1.0, rel_tol=0.0, abs_tol=1e-4):
        raise ValueError("MARKET_MODEL_OUTCOMES_NOT_NORMALIZED")

    refs = None
    if empirical_reference_probs is not None:
        if (set(empirical_reference_probs) != labels
                or not reference_class_receipt_id
                or reference_class_as_of_utc is None
                or _timestamp(reference_class_as_of_utc) > decision):
            raise ValueError("MARKET_REFERENCE_CLASS_UNVERIFIED")
        refs = {
            label: _probability(value, "MARKET_REFERENCE_CLASS_INVALID")
            for label, value in empirical_reference_probs.items()
        }
        if not isclose(sum(refs.values()), 1.0, rel_tol=0.0, abs_tol=1e-4):
            raise ValueError("MARKET_REFERENCE_CLASS_INVALID")

    quotes = tuple(book_quotes)
    if not quotes:
        raise ValueError("MARKET_BOOK_QUOTES_MISSING")
    seen_books: set[str] = set()
    book_probs: list[dict[str, float]] = []
    sources: list[str] = []
    for quote in quotes:
        # Each independently sourced book quote must identify the same event
        # and exact settlement market as the controlling snapshot request.
        if (
            str(quote.get("official_event_id") or "").strip() != official_event_id
            or str(quote.get("market_type") or "").strip() != market_type
            or str(quote.get("settlement_basis") or "").strip() != settlement_basis
        ):
            raise ValueError("MARKET_BOOK_EVENT_OR_SETTLEMENT_MISMATCH")
        book, snapshot = str(quote.get("book") or "").strip(), str(quote.get("source_snapshot_id") or "").strip()
        if not book or not snapshot or book.casefold() in seen_books:
            raise ValueError("MARKET_BOOK_OR_SOURCE_INVALID")
        seen_books.add(book.casefold())
        captured = _timestamp(quote.get("captured_at_utc"))
        if captured > decision or captured >= start:
            raise ValueError("MARKET_QUOTE_FROM_FUTURE")
        if (decision - captured).total_seconds() > 60 * max_quote_age_minutes:
            # A stale quote must not contribute to an apparently actionable edge.
            raise ValueError("MARKET_QUOTE_STALE")
        sides = quote.get("american_odds")
        if not isinstance(sides, Mapping) or set(sides) != labels:
            raise ValueError("MARKET_BOOK_OUTCOME_MISMATCH")
        book_probs.append(no_vig_probabilities(sides, method=method))
        sources.append(snapshot)

    consensus = {
        label: sum(values[label] for values in book_probs) / len(book_probs)
        for label in labels
    }
    # Averaging across books is useful for descriptive consensus, but never
    # proves a market favorite if *any* included book selects a different side
    # or has a near-tie. The favorite label is a cross-source identity assertion,
    # not a numerical parameter that a mean can safely manufacture.
    favorite_prob = max(consensus.values())
    favorite_labels = [label for label in labels if abs(consensus[label] - favorite_prob) <= 0.005]
    favorite = favorite_labels[0] if len(favorite_labels) == 1 else None
    individual_favorites: list[str | None] = []
    for probabilities in book_probs:
        largest = max(probabilities.values())
        leaders = [
            label for label in labels
            if abs(probabilities[label] - largest) <= 0.005
        ]
        individual_favorites.append(leaders[0] if len(leaders) == 1 else None)
    if favorite is None or any(label != favorite for label in individual_favorites):
        favorite = None

    detail: dict[str, dict[str, Any]] = {}
    for label, (p, lo, hi) in model.items():
        per_book = [values[label] for values in book_probs]
        market_p = consensus[label]
        detail[label] = {
            "governed_probability_unchanged": p,
            "governed_lower_bound_unchanged": lo,
            "governed_upper_bound_unchanged": hi,
            "no_vig_market_probability": market_p,
            "cross_book_probability_range": max(per_book) - min(per_book),
            # This is a difference between two separately sourced probability
            # claims; it does not establish model skill or betting value.
            "market_disagreement": p - market_p,
            "market_disagreement_reason_code": "MODEL_MARKET_DISAGREEMENT_UNVALIDATED",
            "conservative_disagreement": lo - max(per_book),
            "reference_class_bias_component": (
                refs[label] - market_p if refs is not None else None
            ),
            "reference_class_residual_disagreement": (
                p - refs[label] if refs is not None else None
            ),
            "is_non_market_favorite": favorite is not None and label != favorite,
            "information_edge_status": (
                "BIAS_DECOMPOSITION_AVAILABLE_INFORMATION_EDGE_UNPROVEN"
                if refs is not None else "REFERENCE_CLASS_UNAVAILABLE_INFORMATION_EDGE_UNPROVEN"
            ),
            "information_edge_proven": False,
            "automatic_pick_promotion": False,
        }
    return {
        "contract": CONTRACT,
        "status": "RESEARCH_ONLY" if len(book_probs) >= 2 else "RESEARCH_ONLY_SINGLE_BOOK",
        "official_event_id": official_event_id,
        "market_type": market_type,
        "settlement_basis": settlement_basis,
        "decision_time_utc": decision.isoformat(),
        "market_favorite": favorite,
        "market_favorite_verified": favorite is not None,
        "book_favorites_agree": favorite is not None,
        "book_count": len(book_probs),
        "source_snapshot_ids": sources,
        "governed_receipt_id": governed_receipt_id,
        "no_vig_method": method,
        "no_vig_method_role": "PRIMARY_RESEARCH" if method == "power" else "SENSITIVITY_ONLY",
        "no_vig_method_contract_version": "V17_POWER_PRIMARY_V1",
        "reference_class_receipt_id": reference_class_receipt_id if refs is not None else None,
        "outcomes": detail,
        "market_probability_is_sporting_model": False,
        "model_probability_mutated": False,
        "automatic_pick_promotion": False,
        "rank_eligible": False,
        "can_execute": False,
    }
