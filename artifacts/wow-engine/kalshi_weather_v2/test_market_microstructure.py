import pytest

from kalshi_weather_v2.market_microstructure import MarketMicrostructureError, MarketMicrostructureSnapshot


def test_microstructure_can_exist_without_prediction_and_derives_spread_and_imbalance():
    snap = MarketMicrostructureSnapshot(
        microstructure_snapshot_id="m-1",
        ticker="KXHIGHDFW-TEST",
        retrieved_at="2026-10-05T18:00:00Z",
        market_status="open",
        raw_market={"ticker": "KXHIGHDFW-TEST"},
        raw_orderbook={"yes": [[60, 10]], "no": [[39, 5]]},
        prediction_id=None,
        yes_best_bid=0.60,
        yes_best_ask=0.62,
        no_best_bid=0.38,
        no_best_ask=0.40,
        yes_bid_size=10,
        no_bid_size=5,
    )
    assert abs(snap.yes_spread - 0.02) < 1e-12
    assert abs(snap.no_spread - 0.02) < 1e-12
    assert abs(snap.orderbook_imbalance - (1 / 3)) < 1e-12
    row = snap.persistence_row()
    assert row["prediction_id"] is None
    assert row["weather_probability_input_allowed"] is False
    assert row["can_execute"] is False


def test_microstructure_rejects_probability_contamination_or_invalid_prices():
    with pytest.raises(MarketMicrostructureError) as exc:
        MarketMicrostructureSnapshot(
            microstructure_snapshot_id="m-2",
            ticker="K",
            retrieved_at="2026-10-05T18:00:00Z",
            market_status="open",
            raw_market={},
            raw_orderbook={},
            weather_probability_input_allowed=True,
        )
    assert exc.value.code == "MARKET_DATA_PROBABILITY_CONTAMINATION"

    with pytest.raises(MarketMicrostructureError) as exc:
        MarketMicrostructureSnapshot(
            microstructure_snapshot_id="m-3",
            ticker="K",
            retrieved_at="2026-10-05T18:00:00Z",
            market_status="open",
            raw_market={},
            raw_orderbook={},
            yes_best_ask=1.01,
        )
    assert exc.value.code == "MARKET_PRICE_INVALID"
