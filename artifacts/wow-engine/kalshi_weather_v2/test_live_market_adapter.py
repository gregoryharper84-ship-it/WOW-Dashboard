import httpx

from kalshi_weather_v2.kalshi_market_adapter import KalshiReadOnlyMarketAdapter
from kalshi_weather_v2.live_http import SafeJsonGetClient


def test_safe_json_client_is_get_only_and_caches_success():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"ok": True}, request=request)

    client = SafeJsonGetClient(
        transport=httpx.MockTransport(handler),
        cache_ttl_seconds=60,
        max_attempts=1,
    )
    try:
        first = client.fetch("https://example.com/data")
        second = client.fetch("https://example.com/data")
    finally:
        client.close()

    assert first.from_cache is False
    assert second.from_cache is True
    assert first.payload == {"ok": True}
    assert calls == ["GET"]
    assert not hasattr(client, "post")
    assert not hasattr(client, "delete")


def test_safe_json_client_rejects_non_https():
    client = SafeJsonGetClient(max_attempts=1)
    try:
        try:
            client.fetch("http://example.com/data")
        except ValueError as exc:
            assert "https" in str(exc)
        else:
            raise AssertionError("expected ValueError")
    finally:
        client.close()


def test_kalshi_orderbook_derives_executable_buy_prices_from_opposite_best_bid():
    responses = {
        "https://external-api.kalshi.com/trade-api/v2/markets/KXTEST": {
            "market": {"ticker": "KXTEST", "status": "open", "updated_time": "2026-09-10T22:00:00Z"}
        },
        "https://external-api.kalshi.com/trade-api/v2/markets/KXTEST/orderbook": {
            "orderbook_fp": {
                "yes_dollars": [["0.1000", "5.00"], ["0.4200", "12.00"]],
                "no_dollars": [["0.2000", "3.00"], ["0.5100", "8.00"]],
            }
        },
    }

    def get_json(url, _headers=None):
        return responses[url]

    evidence = KalshiReadOnlyMarketAdapter(get_json).fetch_market_evidence("KXTEST")
    assert evidence.yes_bid == 0.42
    assert evidence.no_bid == 0.51
    assert evidence.yes_ask == 0.49
    assert evidence.no_ask == 0.58
    assert evidence.orderbook_nonempty is True

    market = KalshiReadOnlyMarketAdapter.to_market_snapshot(
        evidence,
        price_time="2026-09-10T22:00:02Z",
    )
    assert market.yes_price == 0.49
    assert market.no_price == 0.58
    assert market.market_open is True
    assert market.executable_price_verified is True
    assert market.fee_known is False
    assert market.friction_model_verified is False


def test_empty_orderbook_does_not_claim_executable_price():
    def get_json(url, _headers=None):
        if url.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        return {"market": {"ticker": "KXTEST", "status": "open"}}

    evidence = KalshiReadOnlyMarketAdapter(get_json).fetch_market_evidence("KXTEST")
    market = KalshiReadOnlyMarketAdapter.to_market_snapshot(evidence, price_time="2026-09-10T22:00:02Z")
    assert evidence.orderbook_nonempty is False
    assert market.executable_price_verified is False
    assert market.yes_price is None
    assert market.no_price is None
