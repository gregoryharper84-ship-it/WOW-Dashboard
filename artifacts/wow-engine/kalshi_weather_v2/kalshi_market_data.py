from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .models import MarketSnapshot


KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


class KalshiMarketDataError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class KalshiOrderbookEvidence:
    ticker: str
    retrieved_at: str
    market_status: str
    yes_best_bid: float | None
    no_best_bid: float | None
    yes_best_ask: float | None
    no_best_ask: float | None
    yes_bid_size: float | None
    no_bid_size: float | None
    source_market: Mapping[str, Any]
    source_orderbook: Mapping[str, Any]

    @property
    def market_open(self) -> bool:
        # Current REST Market objects use `active` for tradable markets.
        # `open` remains accepted for compatibility with older fixtures and
        # list-filter terminology, but it is not the canonical object state.
        return self.market_status.strip().lower() in {"active", "open"}

    @property
    def orderbook_nonempty(self) -> bool:
        return self.yes_best_bid is not None or self.no_best_bid is not None


class KalshiPublicMarketAdapter:
    """Read-only public Kalshi market-data adapter.

    Kalshi orderbooks expose YES and NO bids. In a binary contract, the
    executable ask for YES is 1 - best NO bid, and vice versa. We derive asks
    only from the observed opposite-side bid; we never substitute last price,
    displayed chance, midpoint, or an inferred probability.
    """

    def __init__(self, get_json):
        self.get_json = get_json

    def get_market(self, ticker: str) -> Mapping[str, Any]:
        ticker = _clean_ticker(ticker)
        payload = self.get_json(f"{KALSHI_BASE_URL}/markets/{ticker}", None)
        market = payload.get("market") if isinstance(payload, Mapping) else None
        if not isinstance(market, Mapping):
            raise KalshiMarketDataError("KALSHI_MARKET_PAYLOAD_INVALID", "market object missing")
        if str(market.get("ticker") or "").upper() != ticker:
            raise KalshiMarketDataError("KALSHI_MARKET_IDENTITY_MISMATCH", ticker)
        return market

    def get_orderbook(self, ticker: str) -> Mapping[str, Any]:
        ticker = _clean_ticker(ticker)
        payload = self.get_json(f"{KALSHI_BASE_URL}/markets/{ticker}/orderbook", None)
        orderbook = payload.get("orderbook_fp") if isinstance(payload, Mapping) else None
        if not isinstance(orderbook, Mapping):
            raise KalshiMarketDataError("KALSHI_ORDERBOOK_PAYLOAD_INVALID", "orderbook_fp object missing")
        return orderbook

    def get_series_fee_changes(self, series_ticker: str) -> tuple[Mapping[str, Any], ...]:
        """Fetch upcoming public fee changes for one exact series ticker."""
        series_ticker = _clean_ticker(series_ticker)
        payload = self.get_json(
            f"{KALSHI_BASE_URL}/series/fee_changes?series_ticker={series_ticker}", None
        )
        rows = payload.get("series_fee_change_arr") if isinstance(payload, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise KalshiMarketDataError("KALSHI_FEE_CHANGES_PAYLOAD_INVALID", "series_fee_change_arr missing")

        normalized: list[Mapping[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise KalshiMarketDataError("KALSHI_FEE_CHANGE_ROW_INVALID", repr(row))
            row_ticker = str(row.get("series_ticker") or "").strip().upper()
            if row_ticker != series_ticker:
                raise KalshiMarketDataError(
                    "KALSHI_FEE_CHANGE_IDENTITY_MISMATCH", f"expected={series_ticker} got={row_ticker}"
                )
            normalized.append(dict(row))
        return tuple(normalized)

    def snapshot(self, ticker: str, *, retrieved_at: str) -> KalshiOrderbookEvidence:
        market = self.get_market(ticker)
        orderbook = self.get_orderbook(ticker)
        yes_bid, yes_size = _best_bid(orderbook.get("yes_dollars"))
        no_bid, no_size = _best_bid(orderbook.get("no_dollars"))

        yes_ask = None if no_bid is None else _one_minus(no_bid)
        no_ask = None if yes_bid is None else _one_minus(yes_bid)

        return KalshiOrderbookEvidence(
            ticker=_clean_ticker(ticker),
            retrieved_at=retrieved_at,
            market_status=str(market.get("status") or ""),
            yes_best_bid=yes_bid,
            no_best_bid=no_bid,
            yes_best_ask=yes_ask,
            no_best_ask=no_ask,
            yes_bid_size=yes_size,
            no_bid_size=no_size,
            source_market=market,
            source_orderbook=orderbook,
        )

    @staticmethod
    def to_market_snapshot(
        evidence: KalshiOrderbookEvidence,
        *,
        side: str,
        fee_known: bool = False,
        fee_per_share: float | None = None,
        effective_break_even: float | None = None,
        friction_model_verified: bool = False,
    ) -> MarketSnapshot:
        """Convert an observed executable ask into the governed market shape.

        Fee/friction values are caller-supplied only after a separate verified
        fee policy calculation. This adapter deliberately does not invent a
        zero-fee assumption.
        """
        normalized = side.strip().upper()
        if normalized not in {"YES", "NO"}:
            raise KalshiMarketDataError("KALSHI_SIDE_INVALID", side)

        yes_price = evidence.yes_best_ask if normalized == "YES" else None
        no_price = evidence.no_best_ask if normalized == "NO" else None
        if normalized == "YES":
            yes_break_even, no_break_even = effective_break_even, None
        else:
            yes_break_even, no_break_even = None, effective_break_even

        return MarketSnapshot(
            yes_price=yes_price,
            no_price=no_price,
            price_time=evidence.retrieved_at,
            market_open=evidence.market_open,
            orderbook_nonempty=evidence.orderbook_nonempty,
            executable_price_verified=(yes_price is not None or no_price is not None),
            fee_known=fee_known,
            fee_per_share=fee_per_share,
            friction_model_verified=friction_model_verified,
            yes_effective_break_even=yes_break_even,
            no_effective_break_even=no_break_even,
        )


def _clean_ticker(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if not text or any(ch.isspace() for ch in text):
        raise KalshiMarketDataError("KALSHI_TICKER_INVALID", str(ticker))
    return text


def _best_bid(levels: Any) -> tuple[float | None, float | None]:
    if levels in (None, []):
        return None, None
    if not isinstance(levels, Sequence) or isinstance(levels, (str, bytes)):
        raise KalshiMarketDataError("KALSHI_ORDERBOOK_LEVELS_INVALID", "levels must be an array")

    parsed: list[tuple[Decimal, Decimal]] = []
    for level in levels:
        if not isinstance(level, Sequence) or isinstance(level, (str, bytes)) or len(level) < 2:
            raise KalshiMarketDataError("KALSHI_ORDERBOOK_LEVEL_INVALID", repr(level))
        try:
            price = Decimal(str(level[0]))
            size = Decimal(str(level[1]))
        except (InvalidOperation, ValueError) as exc:
            raise KalshiMarketDataError("KALSHI_ORDERBOOK_NUMBER_INVALID", repr(level)) from exc
        if not (Decimal("0") <= price <= Decimal("1")) or size < 0:
            raise KalshiMarketDataError("KALSHI_ORDERBOOK_NUMBER_OUT_OF_RANGE", repr(level))
        parsed.append((price, size))

    best = max(parsed, key=lambda item: item[0])
    return float(best[0]), float(best[1])


def _one_minus(value: float) -> float:
    return float(Decimal("1") - Decimal(str(value)))
