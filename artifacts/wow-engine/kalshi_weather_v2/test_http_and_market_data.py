import httpx
import pytest

from kalshi_weather_v2.http_client import HttpAcquisitionError, HttpPolicy, ReadOnlyJsonClient
from kalshi_weather_v2.kalshi_market_data import KalshiMarketDataError, KalshiPublicMarketAdapter


def _client(handler):
    transport = httpx.MockTransport(handler)
    return ReadOnlyJsonClient(
        policy=HttpPolicy(timeout_seconds=1, max_attempts=1, backoff_seconds=0),
        client=httpx.Client(transport=transport),
    )


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
    assert snapshot.yes_best_ask != 0.99
    assert snapshot.market_open is True
    assert snapshot.orderbook_nonempty is True


def test_kalshi_market_snapshot_does_not_invent_fee_or_break_even():
    def get_json(url, _headers):
        if url.endswith("/markets/KXTEST"):
            return {"market": {"ticker": "KXTEST", "status": "active"}}
        return {"orderbook_fp": {"yes_dollars": [["0.60", "4"]], "no_dollars": [["0.35", "7"]]}}

    evidence = KalshiPublicMarketAdapter(get_json).snapshot(
        "KXTEST", retrieved_at="2026-09-09T23:00:00-05:00"
    )
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

    evidence = KalshiPublicMarketAdapter(get_json).snapshot(
        "KXTEST", retrieved_at="2026-09-09T23:00:00-05:00"
    )
    assert evidence.yes_best_ask is None
    assert evidence.no_best_ask == 0.40


def test_kalshi_market_identity_mismatch_fails_closed():
    adapter = KalshiPublicMarketAdapter(
        lambda _url, _headers: {"market": {"ticker": "OTHER", "status": "active"}}
    )
    with pytest.raises(KalshiMarketDataError) as exc:
        adapter.get_market("KXTEST")
    assert exc.value.code == "KALSHI_MARKET_IDENTITY_MISMATCH"


def test_legacy_open_status_remains_compatible_but_closed_is_not_tradable():
    def snapshot_for(status):
        def get_json(url, _headers):
            if url.endswith("/markets/KXTEST"):
                return {"market": {"ticker": "KXTEST", "status": status}}
            return {
                "orderbook_fp": {
                    "yes_dollars": [["0.60", "4"]],
                    "no_dollars": [["0.35", "7"]],
                }
            }

        return KalshiPublicMarketAdapter(get_json).snapshot(
            "KXTEST", retrieved_at="2026-09-09T23:00:00-05:00"
        )

    assert snapshot_for("open").market_open is True
    assert snapshot_for("closed").market_open is False


def test_series_fee_changes_are_exact_ticker_scoped_and_include_history_by_default():
    seen = []

    def get_json(url, _headers):
        seen.append(url)
        return {
            "series_fee_change_arr": [
                {
                    "id": "fc-1",
                    "series_ticker": "KXHIGHNY",
                    "fee_type": "quadratic",
                    "fee_multiplier": 1,
                    "scheduled_ts": "2026-09-01T00:00:00Z",
                }
            ]
        }

    rows = KalshiPublicMarketAdapter(get_json).get_series_fee_changes("kxhighny")
    assert rows[0]["series_ticker"] == "KXHIGHNY"
    assert seen == [
        "https://external-api.kalshi.com/trade-api/v2/series/fee_changes?series_ticker=KXHIGHNY&show_historical=true"
    ]


def test_series_fee_change_identity_mismatch_fails_closed():
    adapter = KalshiPublicMarketAdapter(
        lambda _url, _headers: {
            "series_fee_change_arr": [{"series_ticker": "OTHER", "scheduled_ts": "2026-09-11T00:00:00Z"}]
        }
    )
    with pytest.raises(KalshiMarketDataError) as exc:
        adapter.get_series_fee_changes("KXHIGHNY")
    assert exc.value.code == "KALSHI_FEE_CHANGE_IDENTITY_MISMATCH"


def test_event_fee_changes_are_exact_event_scoped():
    seen = []

    def get_json(url, _headers):
        seen.append(url)
        return {
            "event_fee_changes": [
                {
                    "id": "efc-1",
                    "event_ticker": "KXHIGHNY-26SEP10",
                    "series_ticker": "KXHIGHNY",
                    "fee_type_override": None,
                    "fee_multiplier_override": None,
                    "scheduled_ts": "2026-09-11T00:00:00Z",
                }
            ],
            "cursor": "",
        }

    rows = KalshiPublicMarketAdapter(get_json).get_event_fee_changes("kxhighny-26sep10")
    assert rows[0]["event_ticker"] == "KXHIGHNY-26SEP10"
    assert seen == [
        "https://external-api.kalshi.com/trade-api/v2/events/fee_changes?event_ticker=KXHIGHNY-26SEP10&limit=1000"
    ]


def test_event_fee_change_pagination_fails_closed_instead_of_returning_partial_history():
    adapter = KalshiPublicMarketAdapter(
        lambda _url, _headers: {"event_fee_changes": [], "cursor": "more"}
    )
    with pytest.raises(KalshiMarketDataError) as exc:
        adapter.get_event_fee_changes("KXHIGHNY-26SEP10")
    assert exc.value.code == "KALSHI_EVENT_FEE_CHANGES_PAGINATION_UNRESOLVED"
