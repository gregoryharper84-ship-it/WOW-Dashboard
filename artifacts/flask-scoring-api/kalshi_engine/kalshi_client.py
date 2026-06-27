"""
kalshi_client.py  —  Read-only Kalshi REST client
WOW v16 Kalshi Exchange Layer

Kalshi public REST API: https://trading-api.kalshi.com/trade-api/v2
Some endpoints do not require auth (market data, orderbook).
Authenticated endpoints require KALSHI_API_KEY_ID + KALSHI_API_KEY_PRIVATE_KEY.

HARD RULES:
  - No order placement in this module (execution_guard enforces this).
  - All write operations are paper-trade only.
  - Timeout: 5s for market data, 10s for bulk event scans.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL    = "https://trading-api.kalshi.com/trade-api/v2"
DEMO_URL    = "https://demo-api.kalshi.co/trade-api/v2"

_TIMEOUT_MARKET = 5
_TIMEOUT_BULK   = 10

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })
    return _session


def _base() -> str:
    return os.environ.get("KALSHI_API_URL", BASE_URL)


def _auth_headers() -> dict[str, str]:
    """
    Return auth headers if KALSHI_API_KEY_ID and KALSHI_API_KEY are set.
    For read-only public endpoints, no auth is needed.
    """
    key_id  = os.environ.get("KALSHI_API_KEY_ID")
    key_val = os.environ.get("KALSHI_API_KEY")
    if key_id and key_val:
        return {"Authorization": f"Bearer {key_val}"}
    return {}


def _get(path: str, params: dict | None = None, timeout: int = _TIMEOUT_MARKET) -> dict[str, Any]:
    """Make a GET request. Returns the JSON body or raises."""
    url     = f"{_base()}{path}"
    headers = {**_get_session().headers, **_auth_headers()}
    resp    = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Market endpoints
# ---------------------------------------------------------------------------

def get_market(ticker: str) -> dict[str, Any]:
    """
    GET /markets/{ticker}
    Returns full market details including status, yes_bid, yes_ask, last_price.
    """
    return _get(f"/markets/{ticker}")


def get_event(event_ticker: str) -> dict[str, Any]:
    """
    GET /events/{event_ticker}
    Returns event and all child markets.
    """
    return _get(f"/events/{event_ticker}")


def get_series(series_ticker: str) -> dict[str, Any]:
    """
    GET /series/{series_ticker}
    Returns the series (collection of events).
    """
    return _get(f"/series/{series_ticker}")


def get_orderbook(ticker: str, depth: int = 10) -> dict[str, Any]:
    """
    GET /markets/{ticker}/orderbook
    Returns YES and NO side order books (bids/asks with sizes).
    """
    return _get(f"/markets/{ticker}/orderbook", params={"depth": depth})


def get_market_status(ticker: str) -> dict[str, Any]:
    """
    Returns just market status fields: status, close_time, result.
    """
    data = get_market(ticker)
    market = data.get("market") or data
    return {
        "ticker":     ticker,
        "status":     market.get("status"),
        "close_time": market.get("close_time"),
        "result":     market.get("result"),
        "yes_bid":    market.get("yes_bid"),
        "yes_ask":    market.get("yes_ask"),
        "last_price": market.get("last_price"),
        "volume":     market.get("volume"),
    }


def get_trades(
    ticker:    str,
    min_ts:    int | None = None,
    max_ts:    int | None = None,
    limit:     int        = 100,
) -> dict[str, Any]:
    """
    GET /markets/{ticker}/trades
    Recent trades with timestamps and prices.
    """
    params: dict[str, Any] = {"limit": min(limit, 1000)}
    if min_ts:
        params["min_ts"] = min_ts
    if max_ts:
        params["max_ts"] = max_ts
    return _get(f"/markets/{ticker}/trades", params=params)


def scan_event_markets(event_ticker: str) -> list[dict[str, Any]]:
    """
    Returns a flat list of all market summaries for an event.
    Useful for scanning 50+ contracts in one call.
    """
    data    = get_event(event_ticker)
    markets = data.get("event", {}).get("markets") or data.get("markets") or []
    return markets


def search_markets(
    category:    str | None  = None,
    status:      str          = "open",
    limit:       int          = 50,
    cursor:      str | None  = None,
) -> dict[str, Any]:
    """
    GET /markets — paginated market listing.
    """
    params: dict[str, Any] = {"limit": min(limit, 200), "status": status}
    if category:
        params["category"] = category
    if cursor:
        params["cursor"] = cursor
    return _get("/markets", params=params, timeout=_TIMEOUT_BULK)


# ---------------------------------------------------------------------------
# Safe wrappers (return None on error instead of raising)
# ---------------------------------------------------------------------------

def safe_get_market(ticker: str) -> dict[str, Any] | None:
    try:
        return get_market(ticker)
    except Exception as exc:
        return {"error": str(exc), "ticker": ticker}


def safe_get_orderbook(ticker: str, depth: int = 10) -> dict[str, Any] | None:
    try:
        return get_orderbook(ticker, depth=depth)
    except Exception as exc:
        return {"error": str(exc), "ticker": ticker}
