from __future__ import annotations

import httpx
import pytest

from .http_client import HttpPolicy, JsonHttpClient
from .kalshi_market import KalshiPublicMarketAdapter, executable_sides_from_orderbook


def test_orderbook_derives_executable_buy_prices_from_opposite_bids():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.61", "10.00"], ["0.59", "20.00"]],
            "no_dollars": [["0.37", "5.00"], ["0.35", "20.00"]],
        }
    }
    sides = executable_sides_from_orderbook(payload)
    assert sides.yes_best_bid == 0.61
    assert sides.no_best_bid == 0.37
    assert sides.yes_buy == pytest.approx(0.63)
    assert sides.no_buy == pytest.approx(0.39)
    assert sides.orderbook_nonempty is True


def test_orderbook_never_uses_midpoint_as_entry_price():
    payload = {"orderbook_fp": {"yes_dollars": [["0.70", "1"]], "no_dollars": [["0.20", "1"]]}}
    sides = executable_sides_from_orderbook(payload)
    assert sides.yes_buy == pytest.approx(0.80)
    assert sides.no_buy == pytest.approx(0.30)
    assert sides.yes_buy != pytest.approx(0.50)


def test_empty_orderbook_does_not_claim_executable_price():
    def get_json(url, _headers):
        if "/orderbook" in url:
            return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        return {"market": {"ticker": "WX", "status": "open"}}

    snapshot = KalshiPublicMarketAdapter(get_json).snapshot("WX", retrieved_at="2026-09-10T21:00:00Z")
    assert snapshot.orderbook_nonempty is False
    assert snapshot.executable_price_verified is False
    assert snapshot.yes_price is None
    assert snapshot.no_price is None


def test_public_adapter_is_read_only_and_builds_snapshot():
    calls = []

    def get_json(url, headers):
        calls.append((url, headers))
        if "/orderbook" in url:
            return {"orderbook_fp": {"yes_dollars": [["0.55", "5"]], "no_dollars": [["0.40", "5"]]}}
        return {"market": {"ticker": "WX", "status": "open"}}

    adapter = KalshiPublicMarketAdapter(get_json)
    snapshot = adapter.snapshot(
        "WX",
        retrieved_at="2026-09-10T21:00:00Z",
        yes_effective_break_even=0.61,
        no_effective_break_even=0.46,
        fee_known=True,
        friction_model_verified=True,
    )
    assert snapshot.market_open is True
    assert snapshot.yes_price == pytest.approx(0.60)
    assert snapshot.no_price == pytest.approx(0.45)
    assert snapshot.executable_price_verified is True
    assert all(call[0].startswith("https://external-api.kalshi.com/trade-api/v2/") for call in calls)
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "cancel_order")


def test_json_http_client_retries_get_only():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    getter = JsonHttpClient(HttpPolicy(max_attempts=2, backoff_seconds=0), client=client)
    assert getter.get_json("https://example.test/data") == {"ok": True}
    assert attempts["n"] == 2
    assert not hasattr(getter, "post_json")


def test_json_http_client_caches_successful_get():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    getter = JsonHttpClient(HttpPolicy(max_attempts=1, cache_ttl_seconds=60), client=client)
    assert getter.get_json("https://example.test/data") == {"ok": True}
    assert getter.get_json("https://example.test/data") == {"ok": True}
    assert attempts["n"] == 1


def test_json_http_client_rejects_non_https():
    getter = JsonHttpClient(HttpPolicy(max_attempts=1))
    with pytest.raises(ValueError, match="HTTPS"):
        getter.get_json("http://example.test/data")
