"""
category_router.py  —  Kalshi market category classification
WOW v16.5 Category-Router / Singles-Governor Layer

Pure, stateless classification: no network calls, no DB.
Every discovered Kalshi market object passes through here before any
model, gate, or ledger is consulted.

Classification priority order (first match wins):
  1. Combo/slate (mve_collection_ticker OR underlying_count >= 2)
       → KALSHI_REJECT_COMBO_DISABLED
  2. Economics / macro category
       → RESEARCH_LANE_NOT_BUILT  (never receives a probability — ever)
  3. Weather / temperature category
       → WEATHER_LANE, eligible for weather_gate pipeline
  4. Sports outright winner (FULL_GAME_OUTRIGHT_WINNER market type)
       → SPORTS_WINNER_LANE, eligible for sports_gate pipeline
  5. Anything else (derivatives, props, politics, entertainment, mentions,
     breaking-news) → CATEGORY_DISABLED_OR_UNSUPPORTED
       Must never fall through to generic modeling.
"""
from __future__ import annotations

from typing import Any

# ── Category sets ─────────────────────────────────────────────────────────────

_ECONOMICS_CATEGORIES: frozenset[str] = frozenset({
    "economics", "macro_economics", "scheduled_economics",
    "macro", "economy", "economic",
})

_WEATHER_CATEGORIES: frozenset[str] = frozenset({
    "weather", "temperature", "weather_temperature", "nhigh",
})

_SPORTS_CATEGORIES: frozenset[str] = frozenset({
    "sports", "baseball", "basketball", "football", "hockey",
    "soccer", "mma", "tennis", "golf",
})

_SPORTS_WINNER_MARKET_TYPES: frozenset[str] = frozenset({
    "full_game_outright_winner",
    "game_winner",
    "moneyline",
    "winner",
})

_DISABLED_CATEGORIES: frozenset[str] = frozenset({
    "politics", "entertainment", "mentions", "breaking_news",
    "celebrity", "awards", "crypto", "narrative",
    "derivative", "player_prop", "prop",
})


def classify_market(market: dict[str, Any]) -> dict[str, Any]:
    """
    Classify a single Kalshi market object into a category/lane.

    Parameters
    ----------
    market  — raw market dict from inventory adapter / kalshi_client,
              expected keys (all optional):
                mve_collection_ticker, underlying_count, category,
                market_type, series_ticker, ticker, title, subtitle,
                close_time, trading_active

    Returns
    -------
    dict with:
      category         str  — "combo" | "economics" | "weather" | "sports_winner" | "disabled"
      lane             str  — canonical lane label
      rejection_code   str | None
      eligible         bool — True only for weather / sports_winner lanes
      rationale        str
    """
    mve_ticker     = market.get("mve_collection_ticker") or ""
    underlying_ct  = market.get("underlying_count") or 0
    raw_category   = (market.get("category") or "").strip().lower().replace("-", "_")
    raw_mtype      = (market.get("market_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    ticker         = market.get("ticker") or ""
    title          = market.get("title") or market.get("subtitle") or ""

    # ── Rule 1: Combo / multi-underlying ─────────────────────────────────────
    if mve_ticker or underlying_ct >= 2:
        reason = (
            f"mve_collection_ticker={mve_ticker!r}" if mve_ticker
            else f"underlying_count={underlying_ct} >= 2"
        )
        return _result(
            category       = "combo",
            lane           = "KALSHI_REJECT_COMBO_DISABLED",
            rejection_code = "KALSHI_REJECT_COMBO_DISABLED",
            eligible       = False,
            rationale      = f"Multi-underlying market blocked: {reason}. "
                             f"Singles-governor only — combo slates never enter pipeline.",
        )

    # ── Rule 2: Economics ─────────────────────────────────────────────────────
    if raw_category in _ECONOMICS_CATEGORIES:
        return _result(
            category       = "economics",
            lane           = "RESEARCH_LANE_NOT_BUILT",
            rejection_code = "RESEARCH_LANE_NOT_BUILT",
            eligible       = False,
            rationale      = f"Scheduled-economics category='{raw_category}' — "
                             f"RESEARCH_LANE_NOT_BUILT: no probability ever assigned "
                             f"to economics markets per governance rule.",
        )

    # ── Rule 3: Weather ───────────────────────────────────────────────────────
    if raw_category in _WEATHER_CATEGORIES or _is_weather_ticker(ticker, title):
        return _result(
            category       = "weather",
            lane           = "WEATHER_LANE",
            rejection_code = None,
            eligible       = True,
            rationale      = f"Weather market identified via category='{raw_category}' or "
                             f"ticker/title pattern. Routes to WEATHER_LANE pipeline.",
        )

    # ── Rule 4: Sports outright winner ────────────────────────────────────────
    is_sports_cat    = raw_category in _SPORTS_CATEGORIES or raw_category == ""
    is_winner_type   = raw_mtype in _SPORTS_WINNER_MARKET_TYPES
    is_winner_ticker = _is_sports_winner_ticker(ticker)

    if is_sports_cat and (is_winner_type or is_winner_ticker):
        return _result(
            category       = "sports_winner",
            lane           = "SPORTS_WINNER_LANE",
            rejection_code = None,
            eligible       = True,
            rationale      = f"Sports outright winner market — category='{raw_category}', "
                             f"market_type='{raw_mtype}'. Routes to SPORTS_WINNER_LANE pipeline.",
        )

    # ── Rule 5: Everything else ───────────────────────────────────────────────
    # Derivatives, player props, politics, entertainment, mentions, breaking
    # news — never fall through to generic modeling.
    if raw_category in _DISABLED_CATEGORIES:
        detail = f"category='{raw_category}' is explicitly disabled"
    elif raw_category in _SPORTS_CATEGORIES and not is_winner_type:
        detail = f"sports market_type='{raw_mtype}' is not FULL_GAME_OUTRIGHT_WINNER"
    else:
        detail = f"category='{raw_category}', market_type='{raw_mtype}' — unrecognized/unsupported"

    return _result(
        category       = "disabled",
        lane           = "CATEGORY_DISABLED_OR_UNSUPPORTED",
        rejection_code = "CATEGORY_DISABLED_OR_UNSUPPORTED",
        eligible       = False,
        rationale      = f"CATEGORY_DISABLED_OR_UNSUPPORTED: {detail}. "
                         f"No generic model fallback — this lane is hard-blocked.",
    )


def _is_weather_ticker(ticker: str, title: str) -> bool:
    """Detect weather markets from ticker/title when category field is absent."""
    upper_t = ticker.upper()
    upper_h = title.upper()
    weather_signals = ("NHIGH", "HIGH", "WEATHER", "TEMP", "NHIGHS", "KXNHIGH")
    return any(s in upper_t or s in upper_h for s in weather_signals)


def _is_sports_winner_ticker(ticker: str) -> bool:
    """Detect winner markets from series prefix (KXMLBGAME, KXWNBAGAME, etc.)."""
    upper = ticker.upper()
    winner_prefixes = ("KXMLBGAME", "KXWNBAGAME", "KXNBAGAME", "KXNFLFAME",
                       "KXNHLGAME", "KXNCAAFGAME")
    return any(upper.startswith(p) for p in winner_prefixes)


def _result(
    *,
    category:       str,
    lane:           str,
    rejection_code: str | None,
    eligible:       bool,
    rationale:      str,
) -> dict[str, Any]:
    return {
        "category":       category,
        "lane":           lane,
        "rejection_code": rejection_code,
        "eligible":       eligible,
        "rationale":      rationale,
    }
