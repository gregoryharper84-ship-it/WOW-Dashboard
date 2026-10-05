from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

from .http_client import ReadOnlyJsonClient
from .kalshi_market_data import KalshiPublicMarketAdapter
from .market_microstructure import MarketMicrostructureSnapshot
from .persistence import KalshiWeatherPersistence, content_id


@dataclass(frozen=True)
class MicrostructureCaptureResult:
    attempted: int
    written: int
    failures: tuple[str, ...]
    can_execute: bool = False


def capture_market_microstructure_batch(
    *,
    client,
    tickers: Sequence[str],
    retrieved_at: str,
    series_by_ticker: Mapping[str, str | None] | None = None,
    market_by_ticker: Mapping[str, Mapping[str, object]] | None = None,
    http: ReadOnlyJsonClient | None = None,
) -> MicrostructureCaptureResult:
    """Persist point-in-time market state independently of weather predictions.

    This recorder is downstream market intelligence only. It must never be used
    as meteorological evidence or as an input to the weather probability model.
    """
    unique_tickers = tuple(
        dict.fromkeys(str(ticker).strip().upper() for ticker in tickers if str(ticker).strip())
    )
    if not unique_tickers:
        return MicrostructureCaptureResult(
            attempted=0,
            written=0,
            failures=(),
            can_execute=False,
        )

    owned_http = http is None
    http_client = http or ReadOnlyJsonClient()
    adapter = KalshiPublicMarketAdapter(http_client.get_json)
    persistence = KalshiWeatherPersistence(client)
    series_map = {
        str(key).strip().upper(): (str(value).strip().upper() if value else None)
        for key, value in (series_by_ticker or {}).items()
    }
    market_map = {
        str(key).strip().upper(): value
        for key, value in (market_by_ticker or {}).items()
        if isinstance(value, Mapping)
    }
    failures: list[str] = []
    written = 0

    try:
        for ticker in unique_tickers:
            try:
                cached_market = market_map.get(ticker)
                evidence = (
                    adapter.snapshot_from_market(cached_market, retrieved_at=retrieved_at)
                    if cached_market is not None
                    else adapter.snapshot(ticker, retrieved_at=retrieved_at)
                )
                market = dict(evidence.source_market)
                snapshot_id = content_id(
                    "kalshi-weather-microstructure",
                    {
                        "ticker": evidence.ticker,
                        "retrieved_at": evidence.retrieved_at,
                        "market_updated_time": market.get("updated_time"),
                        "yes_best_bid": evidence.yes_best_bid,
                        "no_best_bid": evidence.no_best_bid,
                        "yes_best_ask": evidence.yes_best_ask,
                        "no_best_ask": evidence.no_best_ask,
                    },
                )
                snapshot = MarketMicrostructureSnapshot(
                    microstructure_snapshot_id=snapshot_id,
                    ticker=evidence.ticker,
                    event_ticker=_text_or_none(market.get("event_ticker")),
                    series_ticker=series_map.get(evidence.ticker),
                    prediction_id=None,
                    retrieved_at=evidence.retrieved_at,
                    market_status=evidence.market_status,
                    yes_best_bid=evidence.yes_best_bid,
                    no_best_bid=evidence.no_best_bid,
                    yes_best_ask=evidence.yes_best_ask,
                    no_best_ask=evidence.no_best_ask,
                    yes_bid_size=evidence.yes_bid_size,
                    no_bid_size=evidence.no_bid_size,
                    yes_ask_size=evidence.no_bid_size,
                    no_ask_size=evidence.yes_bid_size,
                    volume=_number_or_none(market.get("volume_fp") or market.get("volume")),
                    open_interest=_number_or_none(
                        market.get("open_interest_fp") or market.get("open_interest")
                    ),
                    liquidity=_number_or_none(
                        market.get("liquidity_dollars")
                        or market.get("liquidity_fp")
                        or market.get("liquidity")
                    ),
                    raw_market=market,
                    raw_orderbook=dict(evidence.source_orderbook),
                    weather_probability_input_allowed=False,
                    can_execute=False,
                )
                persistence.persist_market_microstructure(snapshot)
                written += 1
            except Exception as exc:
                code = str(getattr(exc, "code", "") or type(exc).__name__)
                failures.append(f"{ticker}:{code}")
    finally:
        if owned_http:
            http_client.close()

    return MicrostructureCaptureResult(
        attempted=len(unique_tickers),
        written=written,
        failures=tuple(failures),
        can_execute=False,
    )


def _text_or_none(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return float(number)
