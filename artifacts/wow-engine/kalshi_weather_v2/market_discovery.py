from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from .kalshi_market_data import KALSHI_BASE_URL


class WeatherMarketDiscoveryError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class WeatherSeriesCandidate:
    ticker: str
    title: str
    category: str
    raw: Mapping[str, Any]


class KalshiWeatherMarketDiscovery:
    """Read-only discovery for candidate Weather series/markets.

    Discovery is intentionally permissive. Exact hourly semantics are validated
    later by the frozen contract-rule parser; discovery never grants model or
    settlement authority by itself.
    """

    def __init__(self, get_json):
        self.get_json = get_json

    def candidate_series(self, *, expected_location: str) -> tuple[WeatherSeriesCandidate, ...]:
        # Do not depend on a hard-coded Kalshi category query token here. The
        # current exchange category is "Climate and Weather", while older docs
        # and UI paths have also used "Climate"/"weather" terminology. Fetch the
        # public series list and apply an explicit weather-category check to the
        # returned canonical category field instead.
        params = urlencode({"include_product_metadata": "true"})
        payload = self.get_json(f"{KALSHI_BASE_URL}/series?{params}", None)
        rows = payload.get("series") if isinstance(payload, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise WeatherMarketDiscoveryError("KALSHI_SERIES_LIST_INVALID", "series array missing")

        needle = str(expected_location or "").strip().casefold()
        if not needle:
            raise WeatherMarketDiscoveryError("EXPECTED_LOCATION_MISSING", "expected_location")

        out: list[WeatherSeriesCandidate] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            title = str(row.get("title") or "")
            category = str(row.get("category") or "")
            category_folded = category.casefold()
            if "weather" not in category_folded and "climate" not in category_folded:
                continue
            blob = " ".join(
                [
                    title,
                    " ".join(str(x) for x in (row.get("tags") or []) if x is not None),
                    json.dumps(row.get("product_metadata") or {}, sort_keys=True, default=str),
                ]
            ).casefold()
            if needle not in blob or "temperature" not in blob:
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                out.append(WeatherSeriesCandidate(ticker=ticker, title=title, category=category, raw=dict(row)))
        return tuple(sorted(out, key=lambda item: item.ticker))

    def open_markets(self, *, series_ticker: str, max_pages: int = 10) -> tuple[Mapping[str, Any], ...]:
        series = str(series_ticker or "").strip().upper()
        if not series:
            raise WeatherMarketDiscoveryError("SERIES_TICKER_MISSING", "series_ticker")
        cursor = ""
        rows_out: list[Mapping[str, Any]] = []
        for _ in range(max_pages):
            params = {
                "series_ticker": series,
                "status": "open",
                "limit": "1000",
                "mve_filter": "exclude",
            }
            if cursor:
                params["cursor"] = cursor
            payload = self.get_json(f"{KALSHI_BASE_URL}/markets?{urlencode(params)}", None)
            rows = payload.get("markets") if isinstance(payload, Mapping) else None
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise WeatherMarketDiscoveryError("KALSHI_MARKETS_LIST_INVALID", f"series={series}")
            rows_out.extend(dict(row) for row in rows if isinstance(row, Mapping))
            cursor = str(payload.get("cursor") or "").strip() if isinstance(payload, Mapping) else ""
            if not cursor:
                break
        else:
            raise WeatherMarketDiscoveryError("KALSHI_MARKETS_PAGINATION_UNRESOLVED", series)
        return tuple(rows_out)
