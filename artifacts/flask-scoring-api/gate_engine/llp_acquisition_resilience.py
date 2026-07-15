"""
llp_acquisition_resilience.py — WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE

Extends WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE with:
  1. League-scoped canonical event identity — no ambiguous city/state aliases
  2. Provider-specific market alias resolution (moneyline → h2h, etc.)
  3. UTC normalization and league-aware time tolerance for event matching
  4. PrizePicks Game Winner adapter: decimal vs American odds disambiguation
  5. Full acquisition ladder with structured contract-stage reporting
  6. Multi-book no-vig consensus reconstruction (min 2 independent fresh books)
  7. Source ceilings by data quality tier
  8. Anti-circular model probability enforcement

Governance: inherits all existing governance, thresholds, handshake,
PostgreSQL ledger, fail-closed behavior, market firewall, Reliability Freeze.
No new terminal labels. final_label remains one of the six existing LLP labels.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any


# ===========================================================================
# 1. Provider Market Aliases
# ===========================================================================

# Canonical internal market key used by the engine
_CANONICAL_H2H     = "h2h"
_CANONICAL_SPREADS = "spreads"
_CANONICAL_TOTALS  = "totals"

# Maps provider → {provider_market_name → canonical_name}
PROVIDER_MARKET_ALIASES: dict[str, dict[str, str]] = {
    "the_odds_api": {
        "moneyline":   _CANONICAL_H2H,
        "game_winner": _CANONICAL_H2H,
        "h2h":         _CANONICAL_H2H,
        "spread":      _CANONICAL_SPREADS,
        "spreads":     _CANONICAL_SPREADS,
        "point_spread": _CANONICAL_SPREADS,
        "total":       _CANONICAL_TOTALS,
        "totals":      _CANONICAL_TOTALS,
        "game_total":  _CANONICAL_TOTALS,
        "over_under":  _CANONICAL_TOTALS,
    },
    "fanduel": {
        "moneyline":   _CANONICAL_H2H,
        "money line":  _CANONICAL_H2H,
        "game_winner": _CANONICAL_H2H,
        "point_spread": _CANONICAL_SPREADS,
        "point spread": _CANONICAL_SPREADS,
        "game_total":  _CANONICAL_TOTALS,
        "over_under":  _CANONICAL_TOTALS,
        "total":       _CANONICAL_TOTALS,
    },
    "draftkings": {
        "moneyline":   _CANONICAL_H2H,
        "money line":  _CANONICAL_H2H,
        "spread":      _CANONICAL_SPREADS,
        "game_spread": _CANONICAL_SPREADS,
        "total":       _CANONICAL_TOTALS,
        "game_total":  _CANONICAL_TOTALS,
    },
    "betmgm": {
        "moneyline":   _CANONICAL_H2H,
        "game_winner": _CANONICAL_H2H,
        "spread":      _CANONICAL_SPREADS,
        "total":       _CANONICAL_TOTALS,
    },
    "caesars": {
        "moneyline":   _CANONICAL_H2H,
        "game_winner": _CANONICAL_H2H,
        "spread":      _CANONICAL_SPREADS,
        "total":       _CANONICAL_TOTALS,
    },
    "prizepicks": {
        # PrizePicks Game Winner is its own adapter path — not a sportsbook h2h
        "game_winner":        "game_winner_pp",
        "more_less":          "player_prop",
        "goblin":             "player_prop_goblin",
        "demon":              "player_prop_demon",
    },
}

# Sources that provide identity/status only — never sportsbook odds
OFFICIAL_IDENTITY_ONLY_SOURCES: frozenset[str] = frozenset({
    "espn", "nba_api", "nba_official", "wnba_official", "mlb_official",
    "nhl_official", "nfl_official", "cbssports_official", "league_official",
    "direct_league_official_source", "official_box_score_gamelog",
})


def resolve_market_alias(
    provider: str,
    market_name: str,
) -> tuple[str | None, dict[str, Any]]:
    """
    Resolve a provider-specific market name to the engine's canonical name.

    Returns:
        (canonical_name | None, diagnostics)

    canonical_name is None when the mapping is unsupported.
    Canonical "game_winner_pp" signals the PrizePicks Game Winner adapter path.
    """
    provider_key = (provider or "").lower().strip()
    market_key   = (market_name or "").lower().strip().replace("-", "_")

    alias_map = PROVIDER_MARKET_ALIASES.get(provider_key)
    if alias_map is None:
        return None, {
            "resolved": False,
            "provider": provider_key,
            "market_name": market_name,
            "reason": f"unknown_provider:{provider_key}",
        }

    canonical = alias_map.get(market_key)
    if canonical is None:
        return None, {
            "resolved": False,
            "provider": provider_key,
            "market_name": market_name,
            "reason": f"unsupported_market:{market_key}:known_markets={sorted(alias_map.keys())}",
        }

    return canonical, {
        "resolved":     True,
        "provider":     provider_key,
        "market_name":  market_name,
        "canonical":    canonical,
        "is_pp_adapter": canonical == "game_winner_pp",
    }


def is_official_identity_source(source: str | None) -> bool:
    """Return True when the source provides identity/status only, not odds."""
    return (source or "").lower().strip() in OFFICIAL_IDENTITY_ONLY_SOURCES


# ===========================================================================
# 2. UTC Normalization
# ===========================================================================

_UTC_SUFFIXES = ("Z", "+00:00", "+0000")
_TZ_OFFSETS: dict[str, int] = {
    "ET":  -5, "EST": -5, "EDT": -4,
    "CT":  -6, "CST": -6, "CDT": -5,
    "MT":  -7, "MST": -7, "MDT": -6,
    "PT":  -8, "PST": -8, "PDT": -7,
}


def normalize_to_utc(raw_dt_str: str | None) -> dict[str, Any]:
    """
    Normalize a raw datetime string to UTC ISO-8601.

    Handles:
      - Strings already in UTC (Z / +00:00 suffix)
      - US timezone abbreviations (ET, CT, MT, PT and DST variants)
      - ISO 8601 with explicit offset

    Returns:
        {
          "utc_datetime": str | None   — ISO-8601 UTC
          "utc_date":     str | None   — YYYY-MM-DD
          "normalized":   bool
          "original":     str | None
          "error":        str | None
        }
    """
    if not raw_dt_str:
        return {
            "utc_datetime": None,
            "utc_date":     None,
            "normalized":   False,
            "original":     raw_dt_str,
            "error":        "empty_input",
        }

    s = raw_dt_str.strip()

    # Already UTC?
    for suf in _UTC_SUFFIXES:
        if s.endswith(suf):
            try:
                s_clean = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s_clean)
                dt_utc = dt.astimezone(timezone.utc)
                return {
                    "utc_datetime": dt_utc.isoformat(),
                    "utc_date":     dt_utc.strftime("%Y-%m-%d"),
                    "normalized":   True,
                    "original":     raw_dt_str,
                    "error":        None,
                }
            except Exception as exc:
                return _utc_error(raw_dt_str, str(exc))

    # US timezone abbreviation suffix (e.g. "2026-07-14 19:05:00 ET")
    m = re.match(r"^(.*?)\s+([A-Z]{2,3})$", s)
    if m:
        dt_part = m.group(1).strip()
        tz_abbr = m.group(2).upper()
        offset_hours = _TZ_OFFSETS.get(tz_abbr)
        if offset_hours is not None:
            try:
                dt_naive = datetime.fromisoformat(dt_part)
                dt_utc = dt_naive - timedelta(hours=offset_hours)
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                return {
                    "utc_datetime": dt_utc.isoformat(),
                    "utc_date":     dt_utc.strftime("%Y-%m-%d"),
                    "normalized":   True,
                    "original":     raw_dt_str,
                    "error":        None,
                }
            except Exception as exc:
                return _utc_error(raw_dt_str, str(exc))

    # ISO 8601 with explicit non-UTC offset
    try:
        dt = datetime.fromisoformat(s)
        dt_utc = dt.astimezone(timezone.utc)
        return {
            "utc_datetime": dt_utc.isoformat(),
            "utc_date":     dt_utc.strftime("%Y-%m-%d"),
            "normalized":   True,
            "original":     raw_dt_str,
            "error":        None,
        }
    except Exception:
        pass

    return _utc_error(raw_dt_str, "unrecognized_format")


def _utc_error(original: str | None, reason: str) -> dict[str, Any]:
    return {
        "utc_datetime": None,
        "utc_date":     None,
        "normalized":   False,
        "original":     original,
        "error":        reason,
    }


# ===========================================================================
# 3. League-Aware Event Identity Matching
# ===========================================================================

# Configurable per-league tolerance (minutes) for event time matching
LEAGUE_TIME_TOLERANCE_MINUTES: dict[str, int] = {
    "nba":      60,
    "wnba":     60,
    "nhl":      60,
    "nfl":      60,
    "mlb":     180,   # doubleheaders, weather delays, makeup games
    "ncaab":    90,
    "ncaaf":    90,
    "default": 120,
}

# Ambiguous city/state aliases that are REJECTED without league scope
AMBIGUOUS_CITY_ALIASES: frozenset[str] = frozenset({
    "washington", "indiana", "new york", "los angeles", "chicago",
    "boston", "detroit", "miami", "dallas", "houston", "phoenix",
    "minnesota", "portland", "atlanta", "orlando",
})


def is_alias_ambiguous(alias: str, league: str | None) -> bool:
    """
    Return True when an alias is ambiguous across leagues and no league is provided.
    "Washington" is unambiguous when league=MLB (→ WSH Nationals) but
    ambiguous without any league context.
    """
    if league:
        return False  # league scope resolves the ambiguity
    return (alias or "").lower().strip() in AMBIGUOUS_CITY_ALIASES


def detect_doubleheader(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect when candidates share the same (date, away_normalized, home_normalized).
    A doubleheader requires a game number or provider event ID to disambiguate.

    Returns:
        {
          "collision":          bool
          "count":              int
          "requires_game_id":   bool
          "candidate_keys":     list[str]
        }
    """
    seen: dict[str, int] = {}
    for c in candidates:
        key = (
            f"{c.get('date', '')}:"
            f"{(c.get('away') or '').lower().strip()}@"
            f"{(c.get('home') or '').lower().strip()}"
        )
        seen[key] = seen.get(key, 0) + 1

    collisions = {k: v for k, v in seen.items() if v > 1}
    has_collision = bool(collisions)
    return {
        "collision":        has_collision,
        "count":            max(collisions.values()) if collisions else 1,
        "requires_game_id": has_collision,
        "candidate_keys":   list(collisions.keys()),
    }


def match_event_with_tolerance(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    reversed_ok: bool = True,
) -> dict[str, Any]:
    """
    Match target event to candidates using league-scoped identity,
    then time-based tolerance.

    Rules:
    - Identity match: same (league, away, home) using league-scoped normalize_team
    - Reversed candidate (home↔away swapped): allowed for lookup only;
      `is_reversed=True` is returned so caller can preserve true venue/side identity
    - Time tolerance is applied only after identity match

    Returns:
        {
          "matched":    bool
          "candidate":  dict | None
          "is_reversed": bool
          "confidence": "exact" | "reversed" | "time_tolerance" | "none"
          "notes":      list[str]
        }
    """
    from gate_engine.event_normalization import normalize_team

    league   = (target.get("league") or target.get("sport") or "").lower()
    t_date   = target.get("date", "")
    t_away   = normalize_team(target.get("away"), league) or (target.get("away") or "").upper()
    t_home   = normalize_team(target.get("home"), league) or (target.get("home") or "").upper()
    tol_min  = LEAGUE_TIME_TOLERANCE_MINUTES.get(league, LEAGUE_TIME_TOLERANCE_MINUTES["default"])

    notes: list[str] = []
    for c in candidates:
        c_date  = c.get("date", "")
        c_away  = normalize_team(c.get("away"), league) or (c.get("away") or "").upper()
        c_home  = normalize_team(c.get("home"), league) or (c.get("home") or "").upper()

        # Exact identity match on same date
        if c_date == t_date and c_away == t_away and c_home == t_home:
            return {"matched": True, "candidate": c, "is_reversed": False,
                    "confidence": "exact", "notes": notes}

        # Same date, reversed orientation — candidate found; preserve true venue
        if reversed_ok and c_date == t_date and c_away == t_home and c_home == t_away:
            notes.append(f"reversed_orientation:target={t_away}@{t_home} candidate={c_away}@{c_home}")
            return {"matched": True, "candidate": c, "is_reversed": True,
                    "confidence": "reversed", "notes": notes}

        # Time-tolerance match (adjacent dates, within tolerance window)
        t_dt = normalize_to_utc(target.get("start_time", ""))
        c_dt = normalize_to_utc(c.get("start_time", ""))
        if t_dt["utc_datetime"] and c_dt["utc_datetime"] and c_away == t_away and c_home == t_home:
            try:
                t_ts = datetime.fromisoformat(t_dt["utc_datetime"])
                c_ts = datetime.fromisoformat(c_dt["utc_datetime"])
                diff_min = abs((t_ts - c_ts).total_seconds()) / 60
                if diff_min <= tol_min:
                    notes.append(f"time_tolerance:{diff_min:.0f}min<={tol_min}min")
                    return {"matched": True, "candidate": c, "is_reversed": False,
                            "confidence": "time_tolerance", "notes": notes}
            except Exception:
                pass

    return {"matched": False, "candidate": None, "is_reversed": False,
            "confidence": "none", "notes": notes}


# ===========================================================================
# 4. PrizePicks Game Winner Adapter
# ===========================================================================

# Decimal payout range for PrizePicks Game Winner markets
_PP_DECIMAL_MIN = 1.01
_PP_DECIMAL_MAX = 10.0   # beyond this, treat as American

def parse_prizepicks_game_winner_odds(
    value: float | int | str | None,
) -> dict[str, Any]:
    """
    Disambiguate PrizePicks decimal multipliers from American odds.

    Decision rules:
      - 1.01 ≤ value < 100:  decimal multiplier (e.g. 1.85, 1.88 → accept)
      - |value| ≥ 100:       American odds (e.g. -110, +130 → accept, convert)
      - value ≤ 1.0:         invalid (no profit; ambiguous edge; reject)
      - value ≤ 0:           invalid
      - non-numeric:         ambiguous → reject

    Returns:
        {
          "format":              "decimal" | "american" | "ambiguous" | "invalid"
          "accepted":            bool
          "raw_value":           float | None
          "no_vig_probability":  float | None  (for decimal: 1/value; for american: converted)
          "rejection_reason":    str | None
        }
    """
    # --- parse ---
    try:
        v = float(value) if value is not None else None
    except (TypeError, ValueError):
        return {
            "format":             "ambiguous",
            "accepted":           False,
            "raw_value":          None,
            "no_vig_probability": None,
            "rejection_reason":   f"non_numeric_value:{value!r}",
        }

    if v is None:
        return {
            "format":             "invalid",
            "accepted":           False,
            "raw_value":          None,
            "no_vig_probability": None,
            "rejection_reason":   "null_value",
        }

    # --- classify ---
    abs_v = abs(v)

    if abs_v >= 100.0:
        # American odds
        try:
            if v > 0:
                prob = 100.0 / (v + 100.0)
            else:
                prob = abs_v / (abs_v + 100.0)
            return {
                "format":             "american",
                "accepted":           True,
                "raw_value":          v,
                "no_vig_probability": round(prob, 6),
                "rejection_reason":   None,
            }
        except ZeroDivisionError:
            return {"format": "invalid", "accepted": False, "raw_value": v,
                    "no_vig_probability": None, "rejection_reason": "division_by_zero"}

    if _PP_DECIMAL_MIN <= v < 100.0:
        # Decimal payout multiplier
        prob = 1.0 / v
        return {
            "format":             "decimal",
            "accepted":           True,
            "raw_value":          v,
            "no_vig_probability": round(prob, 6),
            "rejection_reason":   None,
        }

    # value ≤ 1.0 or 0 < v < 1.01 — invalid
    reason = (
        "value_below_minimum_decimal:no_profit"
        if 0 < v <= 1.0
        else f"negative_decimal_invalid:{v}"
    )
    return {
        "format":             "invalid",
        "accepted":           False,
        "raw_value":          v,
        "no_vig_probability": None,
        "rejection_reason":   reason,
    }


# ===========================================================================
# 5. Multi-Book No-Vig Consensus Reconstruction
# ===========================================================================

@dataclass
class BookQuote:
    """One two-sided sportsbook quote for a game-winner market."""
    book_name:      str
    away_american:  float | None  # American odds for away team
    home_american:  float | None  # American odds for home team
    fetched_at_utc: str           # ISO-8601 UTC — for freshness check
    provider_event_id: str = ""   # for doubleheader disambiguation


def _american_to_implied(american: float) -> float:
    """Convert American odds to raw implied probability (pre-vig)."""
    if american >= 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def _no_vig_two_way(imp_a: float, imp_b: float) -> tuple[float, float]:
    """Strip vig from two implied probabilities."""
    total = imp_a + imp_b
    return imp_a / total, imp_b / total


def _age_minutes(fetched_at_utc: str, now_utc: datetime) -> float | None:
    try:
        ts = datetime.fromisoformat(fetched_at_utc.replace("Z", "+00:00"))
        return (now_utc - ts.astimezone(timezone.utc)).total_seconds() / 60
    except Exception:
        return None


def reconstruct_consensus_no_vig(
    quotes: list[BookQuote],
    max_age_minutes: int = 60,
    min_books: int = 2,
    outlier_threshold_pct: float = 0.08,
) -> dict[str, Any]:
    """
    Reconstruct no-vig consensus probability from multi-book quotes.

    Requirements (all enforced; violations → quote excluded):
      - Both sides (away + home) present
      - Freshness: fetched_at_utc within max_age_minutes
      - Independent: books must be distinct names
      - Coherent: no-vig probability must not deviate > outlier_threshold_pct
        from the group mean (outliers removed iteratively once)

    Returns:
        {
          "success":               bool
          "consensus_away":        float | None  — no-vig probability for away team
          "consensus_home":        float | None
          "method":                "median_two_sided"
          "books_included":        list[str]
          "books_excluded":        list[{book, reason}]
          "book_count":            int
          "min_books_required":    int
          "freshness_max_minutes": int
          "failure_reason":        str | None
        }
    """
    now_utc   = datetime.now(timezone.utc)
    included: list[tuple[str, float, float]] = []  # (book, no_vig_away, no_vig_home)
    excluded: list[dict[str, str]] = []

    seen_books: set[str] = set()

    for q in quotes:
        book = (q.book_name or "").strip().lower()

        # Deduplicate books
        if book in seen_books:
            excluded.append({"book": q.book_name, "reason": "duplicate_book"})
            continue
        seen_books.add(book)

        # Reject if either side missing
        if q.away_american is None or q.home_american is None:
            excluded.append({"book": q.book_name, "reason": "missing_opposite_side"})
            continue

        # Freshness check
        age = _age_minutes(q.fetched_at_utc, now_utc)
        if age is None:
            excluded.append({"book": q.book_name, "reason": "unparseable_timestamp"})
            continue
        if age > max_age_minutes:
            excluded.append({"book": q.book_name,
                              "reason": f"stale:{age:.0f}min>{max_age_minutes}min"})
            continue

        # Compute no-vig
        try:
            imp_a = _american_to_implied(q.away_american)
            imp_h = _american_to_implied(q.home_american)
            nv_a, nv_h = _no_vig_two_way(imp_a, imp_h)
            included.append((q.book_name, nv_a, nv_h))
        except Exception as exc:
            excluded.append({"book": q.book_name, "reason": f"math_error:{exc}"})

    # Outlier removal — one-pass: exclude books whose no_vig_away differs
    # from the group mean by more than outlier_threshold_pct
    if len(included) >= 2:
        mean_away = sum(r[1] for r in included) / len(included)
        filtered   = []
        for r in included:
            deviation = abs(r[1] - mean_away)
            if deviation > outlier_threshold_pct:
                excluded.append({"book": r[0],
                                  "reason": f"outlier:deviation={deviation:.4f}>{outlier_threshold_pct}"})
            else:
                filtered.append(r)
        included = filtered

    if len(included) < min_books:
        return {
            "success":               False,
            "consensus_away":        None,
            "consensus_home":        None,
            "method":                "median_two_sided",
            "books_included":        [],
            "books_excluded":        excluded,
            "book_count":            0,
            "min_books_required":    min_books,
            "freshness_max_minutes": max_age_minutes,
            "failure_reason":        (
                f"insufficient_coherent_books:{len(included)}<{min_books}"
            ),
        }

    # Median across coherent books
    away_probs = sorted(r[1] for r in included)
    home_probs = sorted(r[2] for r in included)
    n = len(away_probs)
    consensus_away = (
        away_probs[n // 2]
        if n % 2 == 1
        else (away_probs[n // 2 - 1] + away_probs[n // 2]) / 2
    )
    consensus_home = (
        home_probs[n // 2]
        if n % 2 == 1
        else (home_probs[n // 2 - 1] + home_probs[n // 2]) / 2
    )

    return {
        "success":               True,
        "consensus_away":        round(consensus_away, 6),
        "consensus_home":        round(consensus_home, 6),
        "method":                "median_two_sided",
        "books_included":        [r[0] for r in included],
        "books_excluded":        excluded,
        "book_count":            len(included),
        "min_books_required":    min_books,
        "freshness_max_minutes": max_age_minutes,
        "failure_reason":        None,
    }


# ===========================================================================
# 6. Source Ceilings
# ===========================================================================

class SourceQuality:
    DIRECT_FRESH_SPORTSBOOK   = "direct_fresh_sportsbook"    # live, 2+ books, <60min
    AGGREGATOR_RECONSTRUCTED  = "aggregator_reconstructed"   # 1 book or aggregator-only
    SCREENSHOT_MANUAL_PROXY   = "screenshot_manual_proxy"    # screenshot/operator/manual
    NO_VALID_BASELINE         = "no_valid_baseline"          # no sportsbook reference at all

# LLP label ceiling per source quality (None = no ceiling imposed)
SOURCE_CEILING_MAP: dict[str, str | None] = {
    SourceQuality.DIRECT_FRESH_SPORTSBOOK:  None,          # full path available
    SourceQuality.AGGREGATOR_RECONSTRUCTED: "LLP_WATCH",   # coded ceiling
    SourceQuality.SCREENSHOT_MANUAL_PROXY:  "LLP_SCOUT",   # max applicable ceiling
    SourceQuality.NO_VALID_BASELINE:        "LLP_SCOUT",   # max applicable ceiling
}


def classify_source_quality(source_meta: dict[str, Any]) -> dict[str, Any]:
    """
    Classify data source quality and return the applicable ceiling.

    Expected keys in source_meta (all optional):
      book_count:      int   — number of coherent independent books
      age_minutes:     float — data age
      is_screenshot:   bool  — operator/screenshot/manual
      is_proxy:        bool  — proxy/reconstructed single-source
      is_aggregator:   bool  — aggregator without direct quotes
      reconstruction_books: int — books used in reconstruction

    Returns:
        {
          "quality":        str   — SourceQuality constant
          "ceiling":        str | None  — LLP label ceiling (None = unrestricted)
          "ceiling_reason": str
        }
    """
    if source_meta.get("is_screenshot") or source_meta.get("is_proxy"):
        q = SourceQuality.SCREENSHOT_MANUAL_PROXY
        return {"quality": q, "ceiling": SOURCE_CEILING_MAP[q],
                "ceiling_reason": "screenshot_or_proxy_source"}

    book_count = source_meta.get("book_count") or source_meta.get("reconstruction_books") or 0
    if book_count == 0:
        q = SourceQuality.NO_VALID_BASELINE
        return {"quality": q, "ceiling": SOURCE_CEILING_MAP[q],
                "ceiling_reason": "no_valid_sportsbook_baseline"}

    if book_count < 2 or source_meta.get("is_aggregator"):
        q = SourceQuality.AGGREGATOR_RECONSTRUCTED
        return {"quality": q, "ceiling": SOURCE_CEILING_MAP[q],
                "ceiling_reason": "single_book_or_aggregator_only"}

    age = source_meta.get("age_minutes")
    if age is not None and age > 60:
        q = SourceQuality.AGGREGATOR_RECONSTRUCTED
        return {"quality": q, "ceiling": SOURCE_CEILING_MAP[q],
                "ceiling_reason": f"stale_data:{age:.0f}min>60min"}

    q = SourceQuality.DIRECT_FRESH_SPORTSBOOK
    return {"quality": q, "ceiling": SOURCE_CEILING_MAP[q],
            "ceiling_reason": "direct_fresh_coherent_multi_book"}


# ===========================================================================
# 7. Anti-Circular Model Probability Enforcement
# ===========================================================================

_CIRCULAR_KEYS = frozenset({
    "market_consensus", "consensus_probability", "market_implied",
    "sportsbook_no_vig", "consensus_no_vig",
})


def check_model_independence(enrichment: dict[str, Any]) -> dict[str, Any]:
    """
    Verify that the model probability is built independently of market consensus.

    The enrichment dict may contain:
      model_source:      str   — if this is a _CIRCULAR_KEY, flag it
      model_probability: float — the claimed model probability
      model_shrinkage:   dict  — optional: {pre_shrink, weight, post_shrink}
      shrinkage_used:    bool  — explicitly declares shrinkage was applied

    Rules:
      1. model_source ∈ CIRCULAR_KEYS → circular_risk=True, independent=False
      2. shrinkage_used=True but model_shrinkage incomplete → violation
      3. Otherwise independent=True

    Returns:
        {
          "independent":        bool
          "circular_risk":      bool
          "violation":          str | None
          "shrinkage_disclosed": bool
          "pre_shrink_prob":    float | None
          "shrinkage_weight":   float | None
          "post_shrink_prob":   float | None
        }
    """
    model_source = (enrichment.get("model_source") or "").lower().strip()
    is_circular  = model_source in _CIRCULAR_KEYS

    shrinkage_used   = bool(enrichment.get("shrinkage_used"))
    shrinkage_meta   = enrichment.get("model_shrinkage") or {}
    pre_shrink       = shrinkage_meta.get("pre_shrink") or enrichment.get("pre_shrink_probability")
    weight           = shrinkage_meta.get("weight") or enrichment.get("shrinkage_weight")
    post_shrink      = shrinkage_meta.get("post_shrink") or enrichment.get("post_shrink_probability")

    shrinkage_complete = all(v is not None for v in [pre_shrink, weight, post_shrink])
    shrinkage_disclosed = shrinkage_used and shrinkage_complete

    violation: str | None = None
    if is_circular:
        violation = f"circular_model_source:{model_source}"
    elif shrinkage_used and not shrinkage_complete:
        missing = [
            n for n, v in [("pre_shrink", pre_shrink),
                            ("weight", weight),
                            ("post_shrink", post_shrink)]
            if v is None
        ]
        violation = f"undisclosed_shrinkage:missing={missing}"

    return {
        "independent":         not is_circular and violation is None,
        "circular_risk":       is_circular,
        "violation":           violation,
        "shrinkage_disclosed": shrinkage_disclosed,
        "pre_shrink_prob":     pre_shrink,
        "shrinkage_weight":    weight,
        "post_shrink_prob":    post_shrink,
    }


# ===========================================================================
# 8. Contract Stage Reporting
# ===========================================================================

# Canonical stage names in evaluation order
CONTRACT_STAGES: list[str] = [
    "event_identity",
    "market_alias_resolution",
    "utc_normalization",
    "primary_provider_odds",
    "fallback_provider_odds",
    "no_vig_consensus_reconstruction",
    "source_ceiling_classification",
    "model_independence_check",
    "acquisition_ladder_complete",
]


def build_contract_stage_report(
    stages_completed: list[str],
    stages_failed: dict[str, str],  # {stage_name: failure_reason}
    provider_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build a structured contract-stage report.

    No new terminal labels are created. final_label remains one of the
    six existing LLP labels. This report is injected as an observability
    field into the row's gates dict under "acquisition_resilience".
    """
    completed_set = set(stages_completed)
    failed_set    = set(stages_failed.keys())
    pending       = [s for s in CONTRACT_STAGES
                     if s not in completed_set and s not in failed_set]

    return {
        "stages_total":      len(CONTRACT_STAGES),
        "stages_completed":  len(stages_completed),
        "stages_failed":     len(stages_failed),
        "stages_pending":    len(pending),
        "completed":         stages_completed,
        "failed":            stages_failed,
        "pending":           pending,
        "fully_complete":    len(pending) == 0 and len(stages_failed) == 0,
        "provider_diagnostics": provider_diagnostics,
    }
