from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .models import MarketSnapshot


JsonGetter = Callable[[str, Mapping[str, str] | None], Mapping[str, Any]]
DEFAULT_KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class KalshiMarketEvidence:
    ticker: str
    status: str
    updated_time: str | None
    yes_bid: float | None
    no_bid: float | None
    yes_ask: float | None
    no_ask: float | None
    best_yes_bid_size: float | None
    best_no_bid_size: float | None
    orderbook_nonempty: bool
    source_market_url: str
    source_orderbook_url: str


class KalshiReadOnlyMarketAdapter:
    """GET-only Kalshi market evidence adapter.

    The orderbook is bid-only. Executable YES buy price is 1 - best NO bid;
    executable NO buy price is 1 - best YES bid. This component never exposes
    order placement, cancellation, portfolio, transfer, or mutation methods.
    """

    def __init__(self, get_json: JsonGetter, *, base_url: str = DEFAULT_KALSHI_BASE_URL) -> None:
        self.get_json = get_json
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("Kalshi base URL must use https")

    def fetch_market_evidence(self, ticker: str) -> KalshiMarketEvidence:
        ticker = str(ticker or "").strip()
        if not ticker:
            raise ValueError("ticker is required")
        market_url = f"{self.base_url}/markets/{ticker}"
        orderbook_url = f"{self.base_url}/markets/{ticker}/orderbook"
        market_payload = self.get_json(market_url, None)
        orderbook_payload = self.get_json(orderbook_url, None)
        market = market_payload.get("market") if isinstance(market_payload, Mapping) else None
        if not isinstance(market, Mapping):
            raise ValueError("Kalshi market payload missing market object")

        book = orderbook_payload.get("orderbook_fp") if isinstance(orderbook_payload, Mapping) else None
        if not isinstance(book, Mapping):
            raise ValueError("Kalshi orderbook payload missing orderbook_fp")

        yes_bid, yes_size = _best_bid(book.get("yes_dollars"))
        no_bid, no_size = _best_bid(book.get("no_dollars"))
        yes_ask = None if no_bid is None else _bounded_price(1.0 - no_bid)
        no_ask = None if yes_bid is None else _bounded_price(1.0 - yes_bid)

        return KalshiMarketEvidence(
            ticker=ticker,
            status=str(market.get("status") or "").lower(),
            updated_time=_optional_text(market.get("updated_time")),
            yes_bid=yes_bid,
            no_bid=no_bid,
            yes_ask=yes_ask,
            no_ask=no_ask,
            best_yes_bid_size=yes_size,
            best_no_bid_size=no_size,
            orderbook_nonempty=yes_bid is not None or no_bid is not None,
            source_market_url=market_url,
            source_orderbook_url=orderbook_url,
        )

    @staticmethod
    def to_market_snapshot(
        evidence: KalshiMarketEvidence,
        *,
        price_time: str,
        fee_known: bool = False,
        fee_per_share: float | None = None,
        friction_model_verified: bool = False,
        yes_effective_break_even: float | None = None,
        no_effective_break_even: float | None = None,
    ) -> MarketSnapshot:
        return MarketSnapshot(
            yes_price=evidence.yes_ask,
            no_price=evidence.no_ask,
            price_time=price_time,
            market_open=evidence.status == "open",
            orderbook_nonempty=evidence.orderbook_nonempty,
            executable_price_verified=evidence.orderbook_nonempty and (evidence.yes_ask is not None or evidence.no_ask is not None),
            fee_known=fee_known,
            fee_per_share=fee_per_share,
            friction_model_verified=friction_model_verified,
            yes_effective_break_even=yes_effective_break_even,
            no_effective_break_even=no_effective_break_even,
        )


def _best_bid(levels: Any) -> tuple[float | None, float | None]:
    if not isinstance(levels, list) or not levels:
        return None, None
    parsed: list[tuple[float, float]] = []
    for row in levels:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            price = float(Decimal(str(row[0])))
            size = float(Decimal(str(row[1])))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if 0.0 <= price <= 1.0 and size > 0:
            parsed.append((price, size))
    if not parsed:
        return None, None
    return max(parsed, key=lambda item: item[0])


def _bounded_price(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
