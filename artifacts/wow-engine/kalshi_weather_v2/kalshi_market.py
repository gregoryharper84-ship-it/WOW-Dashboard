from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .models import MarketSnapshot


JsonGetter = Callable[[str, Mapping[str, str] | None], Mapping[str, Any]]
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


class KalshiMarketDataError(ValueError):
    pass


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise KalshiMarketDataError("KALSHI_PRICE_INVALID") from exc


def _best_bid(levels: Any) -> tuple[Decimal | None, Decimal | None]:
    if not isinstance(levels, list) or not levels:
        return None, None
    rows: list[tuple[Decimal, Decimal]] = []
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = _decimal(level[0])
        size = _decimal(level[1])
        if price is None or size is None or size <= 0:
            continue
        if not (Decimal("0") <= price <= Decimal("1")):
            raise KalshiMarketDataError("KALSHI_PRICE_OUT_OF_RANGE")
        rows.append((price, size))
    if not rows:
        return None, None
    return max(rows, key=lambda row: row[0])


@dataclass(frozen=True)
class ExecutableSides:
    yes_buy: float | None
    no_buy: float | None
    yes_best_bid: float | None
    no_best_bid: float | None
    yes_best_bid_size: float | None
    no_best_bid_size: float | None
    orderbook_nonempty: bool


def executable_sides_from_orderbook(payload: Mapping[str, Any]) -> ExecutableSides:
    """Derive executable buy prices from Kalshi's bid-only fixed-point book.

    A NO bid at x is a YES ask at 1-x. A YES bid at y is a NO ask at 1-y.
    Midpoint/displayed chance is never used as an executable entry price.
    """
    book = payload.get("orderbook_fp") or payload.get("orderbook")
    if not isinstance(book, Mapping):
        raise KalshiMarketDataError("ORDERBOOK_MISSING")

    yes_levels = book.get("yes_dollars") or book.get("yes") or []
    no_levels = book.get("no_dollars") or book.get("no") or []
    yes_bid, yes_size = _best_bid(yes_levels)
    no_bid, no_size = _best_bid(no_levels)

    yes_buy = None if no_bid is None else Decimal("1") - no_bid
    no_buy = None if yes_bid is None else Decimal("1") - yes_bid

    return ExecutableSides(
        yes_buy=float(yes_buy) if yes_buy is not None else None,
        no_buy=float(no_buy) if no_buy is not None else None,
        yes_best_bid=float(yes_bid) if yes_bid is not None else None,
        no_best_bid=float(no_bid) if no_bid is not None else None,
        yes_best_bid_size=float(yes_size) if yes_size is not None else None,
        no_best_bid_size=float(no_size) if no_size is not None else None,
        orderbook_nonempty=yes_bid is not None or no_bid is not None,
    )


class KalshiPublicMarketAdapter:
    """Read-only adapter for public market and orderbook evidence."""

    def __init__(self, get_json: JsonGetter, *, base_url: str = BASE_URL):
        self.get_json = get_json
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("KALSHI_BASE_URL_MUST_BE_HTTPS")

    def market(self, ticker: str) -> Mapping[str, Any]:
        ticker = str(ticker or "").strip()
        if not ticker:
            raise KalshiMarketDataError("KALSHI_TICKER_MISSING")
        return self.get_json(f"{self.base_url}/markets/{ticker}", None)

    def orderbook(self, ticker: str, depth: int = 20) -> Mapping[str, Any]:
        ticker = str(ticker or "").strip()
        if not ticker:
            raise KalshiMarketDataError("KALSHI_TICKER_MISSING")
        depth = max(0, min(100, int(depth)))
        return self.get_json(f"{self.base_url}/markets/{ticker}/orderbook?depth={depth}", None)

    def snapshot(
        self,
        ticker: str,
        *,
        retrieved_at: str,
        yes_effective_break_even: float | None = None,
        no_effective_break_even: float | None = None,
        fee_known: bool = False,
        friction_model_verified: bool = False,
        fee_per_share: float | None = None,
    ) -> MarketSnapshot:
        market_payload = self.market(ticker)
        market = market_payload.get("market") if isinstance(market_payload.get("market"), Mapping) else market_payload
        if not isinstance(market, Mapping):
            raise KalshiMarketDataError("MARKET_PAYLOAD_MISSING")
        orderbook_payload = self.orderbook(ticker)
        sides = executable_sides_from_orderbook(orderbook_payload)

        status = str(market.get("status") or "").lower()
        executable = sides.orderbook_nonempty and (sides.yes_buy is not None or sides.no_buy is not None)
        return MarketSnapshot(
            yes_price=sides.yes_buy,
            no_price=sides.no_buy,
            price_time=retrieved_at,
            market_open=status == "open",
            orderbook_nonempty=sides.orderbook_nonempty,
            executable_price_verified=executable,
            fee_known=fee_known,
            fee_per_share=fee_per_share,
            friction_model_verified=friction_model_verified,
            yes_effective_break_even=yes_effective_break_even,
            no_effective_break_even=no_effective_break_even,
        )
