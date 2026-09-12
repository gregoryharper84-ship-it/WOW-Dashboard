from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from .kalshi_market_data import KALSHI_BASE_URL


# Live hourly Weather products have stable Series identities. Prefer these
# explicit identities over broad /series enumeration, which is a discovery
# surface rather than a settlement or model authority. Exact market rules are
# still reacquired and parsed for every contract before any capture is accepted.
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

    Discovery is intentionally permissive. Exact hourly semantics are validated
    later by the frozen contract-rule parser; discovery never grants model or
    settlement authority by itself.
    """

    def __init__(self, get_json):
        self.get_json = get_json

    def candidate_series(self, *, expected_location: str) -> tuple[WeatherSeriesCandidate, ...]:
        needle = str(expected_location or "").strip().casefold()
        if not needle:
            raise WeatherMarketDiscoveryError("EXPECTED_LOCATION_MISSING", "expected_location")

        # First resolve an explicitly registered hourly Series. This prevents a
        # silent zero-cohort when the exchange's broad Series listing changes
        # ordering, indexing, category vocabulary, or visibility. A direct
        # Series lookup is public market metadata only; the later frozen rules
        # package remains controlling for source/location/time/strike semantics.
        registered = KNOWN_HOURLY_SERIES.get(needle, ())
        direct: list[WeatherSeriesCandidate] = []
        for ticker in registered:
            try:
                payload = self.get_json(f"{KALSHI_BASE_URL}/series/{ticker}", None)
            except Exception:
                continue
            row = payload.get("series") if isinstance(payload, Mapping) else None
            candidate = _candidate_from_row(row, needle=needle)
            if candidate is not None and candidate.ticker == ticker:
                direct.append(candidate)
        if direct:
            return tuple(sorted(direct, key=lambda item: item.ticker))

        # Fallback discovery intentionally does not depend on a hard-coded
        # category query token. Current and historical Kalshi surfaces have used
        # both climate and weather terminology. Fetch the public Series list and
        # validate category/location/temperature from the canonical rows.
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
