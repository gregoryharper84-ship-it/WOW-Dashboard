"""
WOW-PATCH-2026-08-02-LLP-SLIP-CONSTRUCTION-INTEGRITY
Precedence 100 — analytical rules only, direct extension of wow-correlation-guard.

Three rules:
  1. CROSS-BOOK PARLAY DETECTION     — multi-book/multi-exchange slips cannot be
                                       presented as a single combined parlay
  2. SAME-GAME CORRELATED STACK      — ML + player prop from the same team/game
                                       where prop rationale depends on ML outcome
  3. SELECTIVE RECENCY CONSISTENCY   — recency override requires explicit rule
                                       citation; cannot default to whichever
                                       framing favors the desired pick

can_execute = False  (unconditional)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Platforms treated as prediction exchanges (not sportsbooks)
PREDICTION_EXCHANGES: frozenset[str] = frozenset({
    "polymarket",
    "kalshi",
    "metaculus",
    "manifold",
    "prediction_exchange",
})

# Language that implies a combined cross-book slip
PARLAY_IMPLICATION_PHRASES: tuple[str, ...] = (
    "parlay",
    "four-leg ticket",
    "full ticket",
    "combined ticket",
    "multi-leg",
    "same-game parlay",
    "sgp",
    "combined odds",
    "combined payout",
    "combined slip",
)

# Recency-override justification keys that must be present for a valid override
REQUIRED_RECENCY_OVERRIDE_FIELDS: tuple[str, ...] = (
    "recency_rule_cited",     # which explicit WOW rule authorizes the override
    "stale_data_reason",      # why the historical data point is considered stale
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SlipConstructionResult:
    passed: bool = True
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule 1 — Cross-book parlay detection
# ---------------------------------------------------------------------------

def detect_cross_book_parlay(
    slip_legs: list[dict[str, Any]],
    slip_description: str | None = None,
) -> SlipConstructionResult:
    """
    A collection of legs spanning multiple sportsbooks or prediction exchanges
    cannot be presented as a single parlay.  No shared odds, no combined payout.

    Args:
        slip_legs: List of leg dicts, each containing at minimum:
                   {"book": str, "market": str, ...}
                   'book' should be lowercase (e.g. "polymarket", "draftkings")
        slip_description: Optional summary string checked for parlay language.

    Returns:
        SlipConstructionResult with CROSS_BOOK_PARLAY_ILLUSION label when
        multi-book legs are combined under parlay framing.
    """
    result = SlipConstructionResult()
    if not slip_legs:
        return result

    books: set[str] = {leg.get("book", "").lower().strip() for leg in slip_legs}
    books.discard("")

    exchanges = books & PREDICTION_EXCHANGES
    sportsbooks = books - PREDICTION_EXCHANGES

    # Cross-book condition: legs span more than one distinct book, OR
    # at least one prediction exchange is mixed with a sportsbook
    is_cross_book = len(books) > 1

    parlay_language_detected: str | None = None
    if slip_description:
        desc_lower = slip_description.lower()
        for phrase in PARLAY_IMPLICATION_PHRASES:
            if phrase in desc_lower:
                parlay_language_detected = phrase
                break

    if is_cross_book and parlay_language_detected:
        result.passed = False
        result.labels.append("CROSS_BOOK_PARLAY_ILLUSION")
        result.blockers.append(
            f"CROSS_BOOK_PARLAY_ILLUSION: {len(slip_legs)} legs span "
            f"{len(books)} platforms ({', '.join(sorted(books))}) — "
            f"no shared odds exist; presenting as parlay (phrase: "
            f"'{parlay_language_detected}') is structurally false. "
            "These are independent single bets with separate stakes and outcomes."
        )
        result.detail["books_found"] = sorted(books)
        result.detail["exchanges_found"] = sorted(exchanges)
        result.detail["sportsbooks_found"] = sorted(sportsbooks)
        result.detail["parlay_phrase_detected"] = parlay_language_detected
    elif is_cross_book:
        result.warnings.append(
            f"cross_book_slip_no_parlay_language: legs span {sorted(books)} — "
            "no parlay framing detected, but confirm combined-odds language is absent in output"
        )
    return result


# ---------------------------------------------------------------------------
# Rule 2 — Same-game correlated stack detection
# ---------------------------------------------------------------------------

def detect_same_game_correlated_stack(
    slip_legs: list[dict[str, Any]],
) -> SlipConstructionResult:
    """
    Detects ML + player prop pairs from the same team/game where the prop's
    stated rationale depends on the ML outcome (e.g. "player scores more
    because team wins big").  This is same-game stacking, not diversification.

    Args:
        slip_legs: List of leg dicts.  Relevant keys per leg:
            {
              "market_type": "ML" | "PLAYER_PROP" | ...,
              "team":        str,   # team the bet is on / for
              "game_id":     str,   # unique game identifier
              "rationale":   str,   # free-text; checked for ML-dependency language
            }

    Returns:
        SlipConstructionResult with SAME_GAME_CORRELATED_STACK label per
        detected ML↔prop pair.
    """
    result = SlipConstructionResult()
    if not slip_legs or len(slip_legs) < 2:
        return result

    # Phrases in a player-prop rationale that signal ML-outcome dependency
    ML_DEPENDENCY_PHRASES: tuple[str, ...] = (
        "if they win",
        "when they win",
        "winning big",
        "trailing",
        "garbage time",
        "garbage-time",
        "heavy favorite",
        "controlling the game",
        "offense running through",
        "blowout",
        "comfortable lead",
        "running up the score",
        "ahead big",
        "down big",
        "dominant win",
        "garbage possession",
        "should dominate",
    )

    ml_legs: list[dict[str, Any]] = [
        leg for leg in slip_legs
        if str(leg.get("market_type", "")).upper() in ("ML", "MONEYLINE", "GAME_WINNER")
    ]
    prop_legs: list[dict[str, Any]] = [
        leg for leg in slip_legs
        if str(leg.get("market_type", "")).upper() not in ("ML", "MONEYLINE", "GAME_WINNER")
        and leg.get("rationale")
    ]

    flagged_pairs: list[dict[str, str]] = []
    for ml in ml_legs:
        ml_team = str(ml.get("team", "")).lower()
        ml_game = str(ml.get("game_id", ""))
        for prop in prop_legs:
            prop_team = str(prop.get("team", "")).lower()
            prop_game = str(prop.get("game_id", ""))
            # Same game and same team as the ML
            same_game = (ml_game and prop_game and ml_game == prop_game) or (
                ml_team and prop_team and ml_team == prop_team
            )
            if not same_game:
                continue
            rationale = str(prop.get("rationale", "")).lower()
            matched_phrase: str | None = None
            for phrase in ML_DEPENDENCY_PHRASES:
                if phrase in rationale:
                    matched_phrase = phrase
                    break
            if matched_phrase:
                flagged_pairs.append({
                    "ml_leg_market": str(ml.get("market", "")),
                    "prop_leg_market": str(prop.get("market", "")),
                    "team": ml_team,
                    "game_id": ml_game,
                    "dependency_phrase": matched_phrase,
                })

    if flagged_pairs:
        result.passed = False
        result.labels.append("SAME_GAME_CORRELATED_STACK")
        for pair in flagged_pairs:
            result.blockers.append(
                f"SAME_GAME_CORRELATED_STACK: ML '{pair['ml_leg_market']}' + prop "
                f"'{pair['prop_leg_market']}' share game outcome dependency "
                f"(team: {pair['team'] or 'unknown'}, phrase: '{pair['dependency_phrase']}') — "
                "this is same-game stacking, not diversification"
            )
        result.detail["correlated_pairs"] = flagged_pairs
    return result


# ---------------------------------------------------------------------------
# Rule 3 — Selective recency consistency
# ---------------------------------------------------------------------------

def check_recency_consistency(
    candidate: dict[str, Any],
) -> SlipConstructionResult:
    """
    Recency (L5/L10) may only override a contrary historical data point when
    an explicit WOW rule is cited that authorizes the override.  Using recency
    opportunistically (when it favors the pick) while dismissing it elsewhere
    is SELECTIVE_RECENCY_APPLIED.

    Args:
        candidate: Candidate row dict.  Relevant keys:
            {
              "recency_overrides_history": bool,        # True when recency wins
              "recency_override_reason":   dict | None, # must carry rule + stale_reason
              "contrary_historical_note":  str | None,  # the data point being dismissed
            }

    Returns:
        SlipConstructionResult with SELECTIVE_RECENCY_APPLIED label when
        override is invoked without a cited rule.
    """
    result = SlipConstructionResult()
    if not candidate.get("recency_overrides_history"):
        return result

    override_reason: dict[str, Any] = candidate.get("recency_override_reason") or {}
    missing_fields: list[str] = [
        f for f in REQUIRED_RECENCY_OVERRIDE_FIELDS
        if not override_reason.get(f)
    ]

    if missing_fields:
        result.passed = False
        result.labels.append("SELECTIVE_RECENCY_APPLIED")
        result.blockers.append(
            f"SELECTIVE_RECENCY_APPLIED: recency override invoked without required "
            f"justification fields: {missing_fields} — recency must cite an explicit "
            "WOW rule and document why the historical data point is stale; applying "
            "whichever framing favors the pick is not a valid override"
        )
        result.detail["contrary_historical_note"] = candidate.get("contrary_historical_note")
        result.detail["missing_override_fields"] = missing_fields
    return result


# ---------------------------------------------------------------------------
# Main entry point (row-level)
# ---------------------------------------------------------------------------

def run_slip_construction_integrity(
    candidate: dict[str, Any],
    slip_legs: list[dict[str, Any]] | None = None,
    slip_description: str | None = None,
) -> SlipConstructionResult:
    """
    Run all three slip construction integrity checks.

    Args:
        candidate:        Candidate row dict (for recency check).
        slip_legs:        Full list of legs in the slip/portfolio (for cross-book
                          and correlated-stack checks). If None, those checks skip.
        slip_description: Summary string checked for parlay language.

    Returns:
        Merged SlipConstructionResult.
    """
    checks = []

    if slip_legs is not None:
        checks.append(detect_cross_book_parlay(slip_legs, slip_description))
        checks.append(detect_same_game_correlated_stack(slip_legs))

    checks.append(check_recency_consistency(candidate))

    merged = SlipConstructionResult()
    for c in checks:
        if not c.passed:
            merged.passed = False
        merged.blockers.extend(c.blockers)
        merged.warnings.extend(c.warnings)
        merged.labels.extend(c.labels)
        merged.detail.update(c.detail)

    return merged
