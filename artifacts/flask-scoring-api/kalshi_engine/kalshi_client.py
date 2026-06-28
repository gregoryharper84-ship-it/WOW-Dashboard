"""
kalshi_client.py  —  Read-only Kalshi REST client
WOW v16 Kalshi Exchange Layer

Public market-data endpoints (markets, orderbook, trades, events) do NOT
require authentication and are served from external-api.kalshi.com.

Authenticated endpoints (portfolio, account, orders) require RSA-signed
headers:
  KALSHI-ACCESS-KEY       = API Key ID   (env: KALSHI_API_KEY_ID)
  KALSHI-ACCESS-TIMESTAMP = epoch ms (str)
  KALSHI-ACCESS-SIGNATURE = base64(RSA-SHA256(timestamp + method + path))
                            using the PEM private key  (env: KALSHI_PRIVATE_KEY)

HARD RULES:
  - No order placement in this module (execution_guard enforces this).
  - All write operations are paper-trade only.
  - Timeout: 5s for market data, 10s for bulk event scans.
"""
from __future__ import annotations

import base64
import os
import time
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Primary production host for public market data (no auth required)
_PUBLIC_BASE     = "https://external-api.kalshi.com/trade-api/v2"
# Fallback host — identical API surface, different subdomain
_PUBLIC_FALLBACK = "https://api.elections.kalshi.com/trade-api/v2"

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
    """
    Base URL priority:
      1. KALSHI_BASE_URL env var (explicit override)
      2. https://external-api.kalshi.com/trade-api/v2  (Kalshi-recommended prod host)
      Fallback to api.elections.kalshi.com is handled in _get() on connection error.
    """
    return os.environ.get("KALSHI_BASE_URL", _PUBLIC_BASE).rstrip("/")


# ---------------------------------------------------------------------------
# RSA auth headers (only added when KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY set)
# ---------------------------------------------------------------------------

def _build_auth_headers(method: str, path: str) -> dict[str, str]:
    """
    Build Kalshi RSA-signed request headers.

    Signing scheme (Kalshi v2):
      message   = timestamp_ms_str + method.upper() + path_without_query
      signature = RSA-SHA256(private_key, message), base64-encoded

    Returns {} if credentials are not configured (safe for public endpoints).
    """
    key_id      = os.environ.get("KALSHI_API_KEY_ID", "").strip()
    private_pem = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()

    if not key_id or not private_pem:
        return {}

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp_ms = str(int(time.time() * 1000))

        # Path must not include query string
        clean_path = path.split("?")[0]
        message    = (timestamp_ms + method.upper() + clean_path).encode("utf-8")

        # Load private key — handle both raw PEM and escaped newlines
        pem_str = private_pem.replace("\\n", "\n")
        private_key = serialization.load_pem_private_key(
            pem_str.encode("utf-8"),
            password=None,
        )

        signature_bytes = private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")

        return {
            "KALSHI-ACCESS-KEY":       key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature_b64,
        }
    except Exception as exc:
        # Never crash the scan — log and proceed without auth
        import logging
        logging.getLogger(__name__).warning(
            "Kalshi auth header build failed: %s — falling back to unauthenticated", exc
        )
        return {}


def _get(
    path: str,
    params: dict | None = None,
    timeout: int = _TIMEOUT_MARKET,
    authenticated: bool = False,
) -> dict[str, Any]:
    """
    Make a GET request against the Kalshi API.

    authenticated=False → public endpoint, no auth headers attached.
    authenticated=True  → RSA-signed headers added (if credentials are present).

    On connection error or 5xx with the primary base URL, automatically retries
    once against _PUBLIC_FALLBACK (api.elections.kalshi.com).
    """
    hdrs = dict(_get_session().headers)
    if authenticated:
        hdrs.update(_build_auth_headers("GET", path))

    bases_to_try = [_base()]
    fallback = _PUBLIC_FALLBACK.rstrip("/")
    if fallback not in bases_to_try:
        bases_to_try.append(fallback)

    last_exc: Exception | None = None
    for base in bases_to_try:
        try:
            url  = f"{base}{path}"
            resp = requests.get(url, headers=hdrs, params=params or {}, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            continue  # try next base
        except requests.HTTPError as exc:
            # 4xx are definitive — don't retry with a different host
            if exc.response is not None and exc.response.status_code < 500:
                raise
            last_exc = exc
            continue

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Market endpoints  (all public — no auth required)
# ---------------------------------------------------------------------------

def get_market(ticker: str) -> dict[str, Any]:
    """
    GET /markets/{ticker}
    Returns full market details including status, yes_bid, yes_ask, last_price.
    Public endpoint — no auth needed.
    """
    return _get(f"/markets/{ticker}")


def get_event(event_ticker: str) -> dict[str, Any]:
    """
    GET /events/{event_ticker}
    Returns event and all child markets.
    Public endpoint — no auth needed.
    """
    return _get(f"/events/{event_ticker}")


def get_series(series_ticker: str) -> dict[str, Any]:
    """
    GET /series/{series_ticker}
    Returns the series (collection of events).
    Public endpoint — no auth needed.
    """
    return _get(f"/series/{series_ticker}")


def get_orderbook(ticker: str, depth: int = 10) -> dict[str, Any]:
    """
    GET /markets/{ticker}/orderbook
    Returns YES and NO side order books (bids/asks with sizes).
    Public endpoint — no auth needed.
    """
    return _get(f"/markets/{ticker}/orderbook", params={"depth": depth})


def get_market_status(ticker: str) -> dict[str, Any]:
    """
    Returns just market status fields: status, close_time, result.
    """
    data   = get_market(ticker)
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
    ticker:  str,
    min_ts:  int | None = None,
    max_ts:  int | None = None,
    limit:   int        = 100,
) -> dict[str, Any]:
    """
    GET /markets/{ticker}/trades
    Recent trades with timestamps and prices.
    Public endpoint — no auth needed.
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
    category: str | None = None,
    status:   str        = "open",
    limit:    int        = 50,
    cursor:   str | None = None,
) -> dict[str, Any]:
    """
    GET /markets — paginated market listing.
    Public endpoint — no auth needed.
    """
    params: dict[str, Any] = {"limit": min(limit, 200), "status": status}
    if category:
        params["category"] = category
    if cursor:
        params["cursor"] = cursor
    return _get("/markets", params=params, timeout=_TIMEOUT_BULK)


# ---------------------------------------------------------------------------
# Authenticated endpoints (require KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY)
# ---------------------------------------------------------------------------

def get_portfolio_balance() -> dict[str, Any]:
    """
    GET /portfolio/balance
    Returns current account balance.
    Authenticated — requires credentials.
    PAPER-TRADE / READ-ONLY — no order submission.
    """
    return _get("/portfolio/balance", authenticated=True)


def get_portfolio_positions(
    limit:  int        = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """
    GET /portfolio/positions
    Returns current open positions.
    Authenticated — requires credentials.
    PAPER-TRADE / READ-ONLY — no order submission.
    """
    params: dict[str, Any] = {"limit": min(limit, 1000)}
    if cursor:
        params["cursor"] = cursor
    return _get("/portfolio/positions", params=params, authenticated=True)


# ---------------------------------------------------------------------------
# Safe wrappers (return error dict instead of raising)
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


def safe_search_markets(
    category: str | None = None,
    status:   str        = "open",
    limit:    int        = 50,
) -> dict[str, Any]:
    try:
        return search_markets(category=category, status=status, limit=limit)
    except Exception as exc:
        return {"markets": [], "error": str(exc)}
