"""
inventory_adapter.py  —  KalshiInventoryAdapter
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2, Step 1

Public market-data-only wrapper around kalshi_client, scoped to LLP sports /
winner markets. No auth, no order endpoints — read-only inventory checks.

Sports signal semantics (mirrors the generic /wow/kalshi/health signal, but
scoped to sports/winner-market inventory only):
  INVENTORY_READY          — Kalshi is reachable AND at least one open,
                              non-combo (mve_collection_ticker is null) sports
                              market was returned.
  INVENTORY_EMPTY          — Kalshi is reachable but no qualifying sports
                              winner-market was found.
  KALSHI_DATA_UNOBTAINABLE — Kalshi API unreachable, timed out, or errored.

This module never places orders and never calls authenticated endpoints.
"""
from __future__ import annotations

from typing import Any

from .. import kalshi_client

# Kalshi sports category value accepted by /markets?category=
_SPORTS_CATEGORY = "sports"

# Keyword fallback filter — category param is not always honored server-side
# (see memory: kalshi-scan-arch — category param not honored server-side).
_SPORTS_KEYWORDS = (
    "NBA", "WNBA", "MLB", "NFL", "NHL", "NCAAF", "NCAAB",
    "SOCCER", "TENNIS", "GAME", "MATCH", "SERIES", "VS",
)


class KalshiInventoryAdapter:
    """Read-only Kalshi sports/winner-market inventory check."""

    def __init__(self) -> None:
        # No auth, no session state beyond the shared kalshi_client session.
        pass

    def check_sports_inventory(self, limit: int = 100) -> dict[str, Any]:
        """
        Query open Kalshi markets and determine sports winner-market inventory
        status. Public endpoint only (GET /markets) — no auth, no orders.

        Returns:
          {
            signal:                str   INVENTORY_READY | INVENTORY_EMPTY
                                          | KALSHI_DATA_UNOBTAINABLE
            open_total:             int  total open markets returned
            sports_candidate_count: int  markets that look like winner markets
                                          (non-combo, sport-keyword match)
            sample_tickers:         list[str]  up to 5 candidate tickers
            error:                  str | None
            dry_run_only:           True
            can_execute:            False
          }
        """
        try:
            raw = kalshi_client.search_markets(
                category=_SPORTS_CATEGORY, status="open", limit=limit,
            )
        except Exception as exc:
            return self._unobtainable(str(exc))

        markets = raw.get("markets") or []

        # Category filter is not reliably honored server-side — always also
        # apply the keyword fallback so we don't silently under-report.
        if not markets:
            try:
                raw = kalshi_client.search_markets(status="open", limit=limit)
                markets = raw.get("markets") or []
            except Exception as exc:
                return self._unobtainable(str(exc))

        candidates = [m for m in markets if self._looks_like_sports_winner_market(m)]

        signal = "INVENTORY_READY" if candidates else "INVENTORY_EMPTY"
        return {
            "signal":                 signal,
            "open_total":             len(markets),
            "sports_candidate_count": len(candidates),
            "sample_tickers":         [m.get("ticker") for m in candidates[:5]],
            "error":                  None,
            "dry_run_only":           True,
            "can_execute":            False,
        }

    @staticmethod
    def _looks_like_sports_winner_market(market: dict[str, Any]) -> bool:
        """
        A winner market is a single-event, non-combo market. Combo/collection
        markets (mve_collection_ticker set) are excluded — LLP bridges only
        target single winner markets, never MVE combos.
        """
        if market.get("mve_collection_ticker"):
            return False
        haystack = " ".join(
            str(market.get(k, "")) for k in ("title", "subtitle", "ticker", "category")
        ).upper()
        return any(kw in haystack for kw in _SPORTS_KEYWORDS)

    @staticmethod
    def _unobtainable(error: str) -> dict[str, Any]:
        return {
            "signal":                 "KALSHI_DATA_UNOBTAINABLE",
            "open_total":             0,
            "sports_candidate_count": 0,
            "sample_tickers":         [],
            "error":                  error[:300],
            "dry_run_only":           True,
            "can_execute":            False,
        }
