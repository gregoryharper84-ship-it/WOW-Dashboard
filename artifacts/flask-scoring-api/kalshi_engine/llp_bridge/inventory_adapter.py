"""
inventory_adapter.py  —  KalshiInventoryAdapter
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2, Step 1
(revised 2026-07-05 — series_ticker scan, per Kalshi-live-reactivation spec)

Public market-data-only wrapper around kalshi_client, scoped to LLP sports /
winner markets. No auth, no order endpoints — read-only inventory checks.

IMPORTANT (root-cause fix, 2026-07-05): scanning the generic /markets listing
(no series_ticker) is dominated by dynamically-generated MVE combo/parlay
markets — a live check found the first 100+ paginated results were 100%
combo markets even though real single-game MLB/WNBA winner markets exist on
the exchange right now. Category="sports" is also not reliably scoped to
single-game markets (same combo-flooding problem). The only reliable way to
find real single-game winner-market inventory is to query specific known
series_ticker values directly.

Sports signal semantics (mirrors the generic /wow/kalshi/health signal, but
scoped to sports/winner-market inventory only):
  INVENTORY_READY          — Kalshi is reachable AND at least one open,
                              non-combo (mve_collection_ticker is null) sports
                              winner market was returned for a scoped series.
  INVENTORY_EMPTY          — Kalshi is reachable but no qualifying sports
                              winner-market was found in any scoped series.
  KALSHI_DATA_UNOBTAINABLE — Kalshi API unreachable, timed out, or errored.

This module never places orders and never calls authenticated endpoints.
"""
from __future__ import annotations

from typing import Any

from .. import kalshi_client

# Known Kalshi series tickers for single-game head-to-head winner markets,
# scoped to this task's priority sports (MLB, WNBA). Combos/parlays/props
# live under different series (e.g. KXMVE*, KXMLBTOTAL, KXMLBKS) and are
# excluded by construction — we never scan those series for winner-market
# inventory.
_WINNER_SERIES: dict[str, str] = {
    "MLB":  "KXMLBGAME",
    "WNBA": "KXWNBAGAME",
}


class KalshiInventoryAdapter:
    """Read-only Kalshi sports/winner-market inventory check."""

    def __init__(self) -> None:
        # No auth, no session state beyond the shared kalshi_client session.
        pass

    def check_sports_inventory(self, limit: int = 100) -> dict[str, Any]:
        """
        Query open Kalshi markets, scoped per-series to the known MLB/WNBA
        single-game winner-market series tickers, and determine sports
        winner-market inventory status. Public endpoint only (GET /markets)
        — no auth, no orders.

        Returns:
          {
            signal:                  str   INVENTORY_READY | INVENTORY_EMPTY
                                            | KALSHI_DATA_UNOBTAINABLE
            open_total:               int  total open markets returned
                                            across all scanned series
            sports_candidate_count:   int  markets that are real winner
                                            markets (non-combo, in a scoped
                                            series)
            sample_tickers:           list[str]  up to 5 candidate tickers
            candidates_by_league:     dict[str, int]  per-league candidate
                                            counts (MLB/WNBA)
            candidates:               list[dict]  up to `limit` full
                                            candidate market dicts (ticker,
                                            event_ticker, title, close_time,
                                            status), for callers that need
                                            more than sample_tickers
            error:                    str | None
            dry_run_only:             True
            can_execute:              False
          }
        """
        all_markets: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        candidates_by_league: dict[str, int] = {league: 0 for league in _WINNER_SERIES}
        errors: list[str] = []

        for league, series_ticker in _WINNER_SERIES.items():
            try:
                raw = kalshi_client.search_markets(
                    series_ticker=series_ticker, status="open", limit=limit,
                )
            except Exception as exc:
                errors.append(f"{league} ({series_ticker}): {exc}")
                continue

            markets = raw.get("markets") or []
            all_markets.extend(markets)

            league_candidates = [
                self._summarize(m, league)
                for m in markets if self._looks_like_sports_winner_market(m)
            ]
            candidates_by_league[league] = len(league_candidates)
            candidates.extend(league_candidates)

        # If every scoped series errored (and none returned any markets),
        # treat the whole check as unobtainable rather than silently empty.
        if errors and not all_markets:
            return self._unobtainable("; ".join(errors))

        signal = "INVENTORY_READY" if candidates else "INVENTORY_EMPTY"
        return {
            "signal":                 signal,
            "open_total":             len(all_markets),
            "sports_candidate_count": len(candidates),
            "sample_tickers":         [m.get("ticker") for m in candidates[:5]],
            "candidates_by_league":   candidates_by_league,
            "candidates":             candidates[:limit],
            "error":                  "; ".join(errors) if errors else None,
            "dry_run_only":           True,
            "can_execute":            False,
        }

    @staticmethod
    def _looks_like_sports_winner_market(market: dict[str, Any]) -> bool:
        """
        A winner market is a single-event, non-combo market. Combo/collection
        markets (mve_collection_ticker set) are excluded — LLP bridges only
        target single winner markets, never MVE combos. Since candidates are
        already scoped to a known *GAME winner series_ticker, no keyword
        filtering is needed here — the series scope itself is the filter.
        """
        return not market.get("mve_collection_ticker")

    @staticmethod
    def _summarize(market: dict[str, Any], league: str) -> dict[str, Any]:
        """
        Trim a raw Kalshi market dict down to the fields the LLP bridge
        actually needs. Raw Kalshi market payloads carry 40+ fields — we
        never forward the full object over the wire.
        """
        return {
            "league":            league,
            "ticker":            market.get("ticker"),
            "event_ticker":      market.get("event_ticker"),
            "series_ticker":     market.get("series_ticker") or market.get("ticker", "").split("-")[0],
            "title":             market.get("title"),
            "yes_sub_title":     market.get("yes_sub_title"),
            "no_sub_title":      market.get("no_sub_title"),
            "status":            market.get("status"),
            "close_time":        market.get("close_time"),
            "open_time":         market.get("open_time"),
            "rules_primary":     market.get("rules_primary"),
            "mve_collection_ticker": market.get("mve_collection_ticker"),
        }

    @staticmethod
    def _unobtainable(error: str) -> dict[str, Any]:
        return {
            "signal":                 "KALSHI_DATA_UNOBTAINABLE",
            "open_total":             0,
            "sports_candidate_count": 0,
            "sample_tickers":         [],
            "candidates_by_league":   {league: 0 for league in _WINNER_SERIES},
            "candidates":             [],
            "error":                  error[:300],
            "dry_run_only":           True,
            "can_execute":            False,
        }
