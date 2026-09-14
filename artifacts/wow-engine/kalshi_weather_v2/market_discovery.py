from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from .kalshi_market_data import KALSHI_BASE_URL


# These are explicit, currently verified hourly Series identities. The mapping
# is discovery-only; exact market rules remain the final source/location/time/
# strike gate before a contract can enter the governed weather pipeline.
KNOWN_HOURLY_SERIES: Mapping[str, tuple[str, ...]] = {
    "miami": ("KXTEMPMIAH",),
    "chicago metro area": ("KXTEMPCHIHS",),
    "coastal los angeles": ("KXTEMPLAXHS",),
}


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

    Discovery is permissive but never authoritative. Exact hourly semantics are
    validated later by frozen rule acquisition. Multiple public Kalshi list
    surfaces are used only for availability resilience; none can override the
    final contract parser.
    """

    def __init__(self, get_json):
        self.get_json = get_json
        self.last_market_discovery_path: str | None = None

    def candidate_series(self, *, expected_location: str) -> tuple[WeatherSeriesCandidate, ...]:
        needle = str(expected_location or "").strip().casefold()
        if not needle:
            raise WeatherMarketDiscoveryError("EXPECTED_LOCATION_MISSING", "expected_location")

        registered = KNOWN_HOURLY_SERIES.get(needle, ())
        direct: list[WeatherSeriesCandidate] = []
        for ticker in registered:
            try:
                payload = self.get_json(f"{KALSHI_BASE_URL}/series/{ticker}", None)
            except Exception:
                continue
            row = payload.get("series") if isinstance(payload, Mapping) else None
            candidate = _candidate_from_registered_row(row, ticker=ticker)
            if candidate is not None:
                direct.append(candidate)
        if direct:
            return tuple(sorted(direct, key=lambda item: item.ticker))

        # Broad listing is a fallback only. Current and historical Kalshi
        # surfaces have used multiple climate/weather category labels, so filter
        # the canonical returned category instead of hard-coding the query token.
        params = urlencode({"include_product_metadata": "true"})
        payload = self.get_json(f"{KALSHI_BASE_URL}/series?{params}", None)
        rows = payload.get("series") if isinstance(payload, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise WeatherMarketDiscoveryError("KALSHI_SERIES_LIST_INVALID", "series array missing")

        out: list[WeatherSeriesCandidate] = []
        for row in rows:
            candidate = _candidate_from_row(row, needle=needle)
            if candidate is not None:
                out.append(candidate)
        if not out:
            raise WeatherMarketDiscoveryError(
                "KALSHI_WEATHER_SERIES_NOT_DISCOVERED",
                expected_location,
            )
        return tuple(sorted(out, key=lambda item: item.ticker))

    def open_markets(self, *, series_ticker: str, max_pages: int = 10) -> tuple[Mapping[str, Any], ...]:
        series = str(series_ticker or "").strip().upper()
        if not series:
            raise WeatherMarketDiscoveryError("SERIES_TICKER_MISSING", "series_ticker")

        rows = self._paged_markets(series=series, status="open", max_pages=max_pages)
        if rows:
            self.last_market_discovery_path = "MARKETS_STATUS_OPEN"
            return rows

        # If the market list unexpectedly comes back empty, corroborate against
        # open events with nested markets. This is still official Kalshi public
        # market data and protects against list-index/status-filter drift.
        nested = self._open_event_markets(series=series, max_pages=max_pages)
        if nested:
            self.last_market_discovery_path = "EVENTS_STATUS_OPEN_NESTED_MARKETS"
            return nested

        # Final availability fallback: fetch the Series' current market rows
        # without a status filter and locally accept only active/open lifecycle
        # states. Exact rules are still re-fetched per ticker before capture.
        all_current = self._paged_markets(series=series, status=None, max_pages=max_pages)
        active = tuple(
            row
            for row in all_current
            if str(row.get("status") or "").strip().lower() in {"active", "open"}
        )
        self.last_market_discovery_path = "MARKETS_LOCAL_ACTIVE_FILTER"
        return active

    def _paged_markets(
        self,
        *,
        series: str,
        status: str | None,
        max_pages: int,
    ) -> tuple[Mapping[str, Any], ...]:
        cursor = ""
        rows_out: list[Mapping[str, Any]] = []
        for _ in range(max_pages):
            params = {
                "series_ticker": series,
                "limit": "1000",
                "mve_filter": "exclude",
            }
            if status:
                params["status"] = status
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

    def _open_event_markets(self, *, series: str, max_pages: int) -> tuple[Mapping[str, Any], ...]:
        cursor = ""
        rows_out: list[Mapping[str, Any]] = []
        seen_tickers: set[str] = set()
        for _ in range(max_pages):
            params = {
                "series_ticker": series,
                "status": "open",
                "with_nested_markets": "true",
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor
            payload = self.get_json(f"{KALSHI_BASE_URL}/events?{urlencode(params)}", None)
            events = payload.get("events") if isinstance(payload, Mapping) else None
            if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
                raise WeatherMarketDiscoveryError("KALSHI_EVENTS_LIST_INVALID", f"series={series}")
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                markets = event.get("markets")
                if not isinstance(markets, Sequence) or isinstance(markets, (str, bytes)):
                    continue
                for row in markets:
                    if not isinstance(row, Mapping):
                        continue
                    status = str(row.get("status") or "").strip().lower()
                    ticker = str(row.get("ticker") or "").strip().upper()
                    if status not in {"active", "open"} or not ticker or ticker in seen_tickers:
                        continue
                    seen_tickers.add(ticker)
                    rows_out.append(dict(row))
            cursor = str(payload.get("cursor") or "").strip() if isinstance(payload, Mapping) else ""
            if not cursor:
                break
        else:
            raise WeatherMarketDiscoveryError("KALSHI_EVENTS_PAGINATION_UNRESOLVED", series)
        return tuple(rows_out)


def _candidate_from_registered_row(raw: Any, *, ticker: str) -> WeatherSeriesCandidate | None:
    if not isinstance(raw, Mapping):
        return None
    row_ticker = str(raw.get("ticker") or "").strip().upper()
    if row_ticker != ticker:
        return None
    title = str(raw.get("title") or "")
    category = str(raw.get("category") or "")
    category_folded = category.casefold()
    if "weather" not in category_folded and "climate" not in category_folded:
        return None
    blob = " ".join(
        [
            title,
            " ".join(str(x) for x in (raw.get("tags") or []) if x is not None),
            json.dumps(raw.get("product_metadata") or {}, sort_keys=True, default=str),
        ]
    ).casefold()
    if "temperature" not in blob:
        return None
    return WeatherSeriesCandidate(ticker=row_ticker, title=title, category=category, raw=dict(raw))


def _candidate_from_row(raw: Any, *, needle: str) -> WeatherSeriesCandidate | None:
    if not isinstance(raw, Mapping):
        return None
    title = str(raw.get("title") or "")
    category = str(raw.get("category") or "")
    category_folded = category.casefold()
    if "weather" not in category_folded and "climate" not in category_folded:
        return None
    blob = " ".join(
        [
            title,
            " ".join(str(x) for x in (raw.get("tags") or []) if x is not None),
            json.dumps(raw.get("product_metadata") or {}, sort_keys=True, default=str),
        ]
    ).casefold()
    if needle not in blob or "temperature" not in blob:
        return None
    ticker = str(raw.get("ticker") or "").strip().upper()
    if not ticker:
        return None
    return WeatherSeriesCandidate(ticker=ticker, title=title, category=category, raw=dict(raw))
