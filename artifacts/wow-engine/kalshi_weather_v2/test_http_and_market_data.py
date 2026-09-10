import httpx
import pytest

from kalshi_weather_v2.http_client import HttpAcquisitionError, HttpPolicy, ReadOnlyJsonClient
from kalshi_weather_v2.kalshi_market_data import KalshiMarketDataError, KalshiPublicMarketAdapter


def _client(handler):
    transport = httpx.MockTransport(handler)
    return ReadOnlyJsonClient(policy=HttpPolicy(timeout_seconds=1, max_attempts=1, backoff_seconds=0), client=httpx.Client(transport=transport))


def test_read_only_http_client_requires_https():
    client = _client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(HttpAcquisitionError) as exc:
        client.get_json("http://example.test/data")
    assert exc.value.code == "INSECURE_URL_PROHIBITED"


def test_read_only_http_client_rejects_non_object_json():
    client = _client(lambda request: httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(HttpAcquisitionError) as exc:
        client.get_json("https://example.test/data")
    assert exc.value.code == "JSON_OBJECT_REQUIRED"


def test_kalshi_orderbook_derives_executable_asks_from_opposite_bids_only():
    def get_json(url, _headers):
        if url.endswith("/markets/KXTEST"):
            return {"market": {"ticker": "KXTEST", "status": "active", "last_price_dollars": "0.99"}}
        if url.endswith("/markets/KXTEST/orderbook"):
            return {
                "orderbook_fp": {
                    "yes_dollars": [["0.57", "10"], ["0.55", "50"]],
                    "no_dollars": [["0.40", "20"], ["0.38", "80"]],
                }
            }
        raise AssertionError(url)

    adapter = KalshiPublicMarketAdapter(get_json)
    snapshot = adapter.snapshot("kxtest", retrieved_at="2026-09-09T23:00:00-05:00")

    assert snapshot.yes_best_bid == 0.57
    assert snapshot.no_best_bid == 0.40
    assert snapshot.yes_best_ask == 0.60
    assert snapshot.no_best_ask == 0.43
    # Last trade/displayed chance must not be substituted for executable ask.
    assert snapshot.yes_best_ask != 0.99
    assert snapshot.market_open is True
    assert snapshot.orderbook_nonempty is True


def test_kalshi_market_snapshot_does_not_invent_fee_or_break_even():
    def get_json(url, _headers):
        if url.endswith("/markets/KXTEST"):
            return {"market": {"ticker": "KXTEST", "status": "active"}}
        return {"orderbook_fp": {"yes_dollars": [["0.60", "4"]], "no_dollars": [["0.35", "7"]]}}

    evidence = KalshiPublicMarketAdapter(get_json).snapshot("KXTEST", retrieved_at="2026-09-09T23:00:00-05:00")
    market = KalshiPublicMarketAdapter.to_market_snapshot(evidence, side="YES")

    assert market.yes_price == 0.65
    assert market.fee_known is False
    assert market.friction_model_verified is False
    assert market.yes_effective_break_even is None


def test_kalshi_orderbook_empty_side_does_not_fabricate_ask():
    def get_json(url, _headers):
        if url.endswith("/markets/KXTEST"):
            return {"market": {"ticker": "KXTEST", "status": "active"}}
        return {"orderbook_fp": {"yes_dollars": [["0.60", "4"]], "no_dollars": []}}

    evidence = KalshiPublicMarketAdapter(get_json).snapshot("KXTEST", retrieved_at="2026-09-09T23:00:00-05:00")
    assert evidence.yes_best_ask is None
    assert evidence.no_best_ask == 0.40


def test_kalshi_market_identity_mismatch_fails_closed():
    adapter = KalshiPublicMarketAdapter(lambda _url, _headers: {"market": {"ticker": "OTHER", "status": "active"}})
    with pytest.raises(KalshiMarketDataError) as exc:
        adapter.get_market("KXTEST")
    assert exc.value.code == "KALSHI_MARKET_IDENTITY_MISMATCH"


def test_legacy_open_status_remains_compatible_but_closed_is_not_tradable():
    def snapshot_for(status):
        def get_json(url, _headers):
            if url.endswith("/markets/KXTEST"):
                return {"market": {"ticker": "KXTEST", "status": status}}
            return {"orderbook_fp": {"yes_dollars": [["0.60", "4"]], "no_dollars": [["0.35", "7"]]}}
        return KalshiPublicMarketAdapter(get_json).snapshot("KXTEST", retrieved_at="2026-09-09T23:00:00-05:00")

    assert snapshot_for("open").market_open is True
    assert snapshot_for("closed").market_open is False
