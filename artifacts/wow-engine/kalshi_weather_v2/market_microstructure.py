from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


class MarketMicrostructureError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class MarketMicrostructureSnapshot:
    microstructure_snapshot_id: str
    ticker: str
    retrieved_at: str
    market_status: str
    raw_market: Mapping[str, Any]
    raw_orderbook: Mapping[str, Any]
    event_ticker: str | None = None
    series_ticker: str | None = None
    prediction_id: str | None = None
    yes_best_bid: float | None = None
    no_best_bid: float | None = None
    yes_best_ask: float | None = None
    no_best_ask: float | None = None
    yes_bid_size: float | None = None
    no_bid_size: float | None = None
    yes_ask_size: float | None = None
    no_ask_size: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    liquidity: float | None = None
    weather_probability_input_allowed: bool = False
    can_execute: bool = False

    def __post_init__(self) -> None:
        for name in ("microstructure_snapshot_id", "ticker", "retrieved_at", "market_status"):
            if not str(getattr(self, name) or "").strip():
                raise MarketMicrostructureError("MARKET_MICROSTRUCTURE_REQUIRED_FIELD_MISSING", name)
        if self.weather_probability_input_allowed:
            raise MarketMicrostructureError("MARKET_DATA_PROBABILITY_CONTAMINATION", self.ticker)
        if self.can_execute:
            raise MarketMicrostructureError("KALSHI_WEATHER_EXECUTION_FORBIDDEN", self.ticker)
        for name in ("yes_best_bid", "no_best_bid", "yes_best_ask", "no_best_ask"):
            value = getattr(self, name)
            if value is not None and (not isfinite(float(value)) or float(value) < 0.0 or float(value) > 1.0):
                raise MarketMicrostructureError("MARKET_PRICE_INVALID", name)

    @property
    def yes_spread(self) -> float | None:
        if self.yes_best_bid is None or self.yes_best_ask is None:
            return None
        return max(0.0, float(self.yes_best_ask) - float(self.yes_best_bid))

    @property
    def no_spread(self) -> float | None:
        if self.no_best_bid is None or self.no_best_ask is None:
            return None
        return max(0.0, float(self.no_best_ask) - float(self.no_best_bid))

    @property
    def orderbook_imbalance(self) -> float | None:
        yes = float(self.yes_bid_size or 0.0)
        no = float(self.no_bid_size or 0.0)
        denom = yes + no
        if denom <= 0.0:
            return None
        return (yes - no) / denom

    def persistence_row(self) -> Mapping[str, Any]:
        return {
            "microstructure_snapshot_id": self.microstructure_snapshot_id,
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "series_ticker": self.series_ticker,
            "prediction_id": self.prediction_id,
            "retrieved_at": self.retrieved_at,
            "market_status": self.market_status,
            "yes_best_bid": self.yes_best_bid,
            "no_best_bid": self.no_best_bid,
            "yes_best_ask": self.yes_best_ask,
            "no_best_ask": self.no_best_ask,
            "yes_bid_size": self.yes_bid_size,
            "no_bid_size": self.no_bid_size,
            "yes_ask_size": self.yes_ask_size,
            "no_ask_size": self.no_ask_size,
            "yes_spread": self.yes_spread,
            "no_spread": self.no_spread,
            "orderbook_imbalance": self.orderbook_imbalance,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "liquidity": self.liquidity,
            "raw_market": dict(self.raw_market),
            "raw_orderbook": dict(self.raw_orderbook),
            "weather_probability_input_allowed": False,
            "can_execute": False,
        }
