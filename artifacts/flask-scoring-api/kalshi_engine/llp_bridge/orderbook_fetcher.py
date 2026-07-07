"""
orderbook_fetcher.py  —  Server-side Kalshi live orderbook + market status fetch.
WOW-PATCH-2026-07-07-KALSHI-FINAL-LOCK-EDGE-DISCOVERY

ENFORCEMENT RULE:
  Only this module may tag kalshi_orderbook_source = "direct_api".
  Web UI / caller-supplied prices are NEVER "direct_api".
  Any normalized_price not produced from a fetch() call here must be tagged
  "caller_supplied" by the caller, and the route will cap such rows at
  LLP_WATCH via the KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API blocker.

Market status check:
  fetch() also calls GET /markets/{ticker} to extract status and
  trading_active (status == "open"). If the market is not open, the route
  surfaces MARKET_NOT_TRADING to evaluate_stub via trading_active=False.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .. import kalshi_client


def fetch(ticker: str, depth: int = 10) -> dict[str, Any]:
    """
    Pull a fresh Kalshi orderbook + market status for `ticker` directly from
    the Kalshi public API.

    Returns:
      {
        kalshi_orderbook_source:  "direct_api" | "fetch_failed"
        raw_orderbook:            dict | None   (pass to KalshiPriceNormalizer)
        orderbook_timestamp_utc:  str | None    (ISO-8601, set to call time)
        market_status:            str | None    ("open", "closed", etc.)
        trading_active:           bool | None   (True iff status == "open")
        yes_bid:                  int | None    (cents, display only)
        yes_ask:                  int | None    (cents, display only)
        last_price:               int | None    (cents, display only)
        volume:                   int | None
        fetch_error:              str | None
        dry_run_only:             True
        can_execute:              False
      }
    """
    now_utc_str = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")

    # ── Orderbook fetch ────────────────────────────────────────────────────
    raw_orderbook: dict | None = None
    fetch_error: str | None = None
    try:
        raw_orderbook = kalshi_client.get_orderbook(ticker, depth=depth)
    except Exception as exc:
        fetch_error = f"ORDERBOOK_FETCH_FAILED: {exc}"

    # ── Market status fetch ────────────────────────────────────────────────
    market_status: str | None = None
    trading_active: bool | None = None
    yes_bid: int | None = None
    yes_ask: int | None = None
    last_price: int | None = None
    volume: int | None = None
    status_error: str | None = None
    try:
        status_data = kalshi_client.get_market_status(ticker)
        market_status = status_data.get("status")
        trading_active = (market_status == "open")
        yes_bid   = status_data.get("yes_bid")
        yes_ask   = status_data.get("yes_ask")
        last_price = status_data.get("last_price")
        volume    = status_data.get("volume")
    except Exception as exc:
        status_error = f"MARKET_STATUS_FETCH_FAILED: {exc}"
        # trading_active remains None — gate will treat as unknown

    source = "direct_api" if raw_orderbook is not None else "fetch_failed"
    combined_error = "; ".join(e for e in [fetch_error, status_error] if e) or None

    return {
        "kalshi_orderbook_source":  source,
        "raw_orderbook":            raw_orderbook,
        "orderbook_timestamp_utc":  now_utc_str if raw_orderbook is not None else None,
        "market_status":            market_status,
        "trading_active":           trading_active,
        "yes_bid":                  yes_bid,
        "yes_ask":                  yes_ask,
        "last_price":               last_price,
        "volume":                   volume,
        "fetch_error":              combined_error,
        "dry_run_only":             True,
        "can_execute":              False,
    }


def detect_market_type(ticker: str | None, series_ticker: str | None = None) -> str:
    """
    Classify a Kalshi sports market as "main_winner" or "derivative" from
    its ticker or series_ticker.

    main_winner: KXMLBGAME-*, KXWNBAGAME-* (single-game head-to-head winner)
    derivative:  everything else (F5, 3-way, run-total, props, etc.)

    Used to select the correct EDGE_FLOOR in evaluate_stub:
      main_winner  → EDGE_FLOOR_MAIN       = 0.015 (1.5%)
      derivative   → EDGE_FLOOR_DERIVATIVE = 0.025 (2.5%)
    """
    _MAIN_WINNER_PREFIXES = ("KXMLBGAME-", "KXWNBAGAME-")
    for t in (ticker or "", series_ticker or ""):
        for prefix in _MAIN_WINNER_PREFIXES:
            if t.upper().startswith(prefix):
                return "main_winner"
    return "derivative"
