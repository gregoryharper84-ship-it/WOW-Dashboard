from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contract_resolver import ContractResolutionError, resolve_weather_contract
from .kalshi_market import BASE_URL
from .models import ContractSnapshot


JsonGetter = Callable[[str, Mapping[str, str] | None], Mapping[str, Any]]


class KalshiContractAcquisitionError(ValueError):
    pass


@dataclass(frozen=True)
class KalshiRuleAcquisition:
    ticker: str
    event_ticker: str
    series_ticker: str
    retrieved_at: str
    market: Mapping[str, Any]
    event: Mapping[str, Any]
    series: Mapping[str, Any]
    rule_snapshot_id: str


@dataclass(frozen=True)
class WeatherSeriesRulePolicy:
    """Explicit semantic policy for one Kalshi weather series.

    This is intended to come from a governed registry/Supabase row. It exists
    because free-form contract text must not be heuristically converted into
    settlement identity, timezone, rounding, or exact event bounds.
    """

    series_ticker: str
    lane: str
    location: str
    metric: str
    units: str
    observation_window: str
    timezone: str
    settlement_source_name: str
    settlement_location_type: str
    rounding_convention: str
    trace_measurement_rules: str
    settlement_station_id: str | None = None
    settlement_station_name: str | None = None
    settlement_latitude: float | None = None
    settlement_longitude: float | None = None
    threshold_lower: float | None = None
    threshold_upper: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True


class KalshiWeatherContractSource:
    """GET-only acquisition of live market, event and series rule metadata."""

    def __init__(self, get_json: JsonGetter, *, base_url: str = BASE_URL) -> None:
        self.get_json = get_json
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("KALSHI_BASE_URL_MUST_BE_HTTPS")

    def acquire(self, ticker: str, *, retrieved_at: str) -> KalshiRuleAcquisition:
        ticker = str(ticker or "").strip()
        if not ticker:
            raise KalshiContractAcquisitionError("KALSHI_TICKER_MISSING")

        market_payload = self.get_json(f"{self.base_url}/markets/{ticker}", None)
        market = market_payload.get("market") if isinstance(market_payload, Mapping) else None
        if not isinstance(market, Mapping):
            raise KalshiContractAcquisitionError("KALSHI_MARKET_PAYLOAD_MISSING")
        if str(market.get("ticker") or "").strip() != ticker:
            raise KalshiContractAcquisitionError("KALSHI_MARKET_TICKER_MISMATCH")

        event_ticker = str(market.get("event_ticker") or "").strip()
        if not event_ticker:
            raise KalshiContractAcquisitionError("KALSHI_EVENT_TICKER_MISSING")
        event_payload = self.get_json(f"{self.base_url}/events/{event_ticker}", None)
        event = event_payload.get("event") if isinstance(event_payload, Mapping) else None
        if not isinstance(event, Mapping):
            raise KalshiContractAcquisitionError("KALSHI_EVENT_PAYLOAD_MISSING")
        if str(event.get("event_ticker") or "").strip() != event_ticker:
            raise KalshiContractAcquisitionError("KALSHI_EVENT_TICKER_MISMATCH")

        series_ticker = str(event.get("series_ticker") or "").strip()
        if not series_ticker:
            raise KalshiContractAcquisitionError("KALSHI_SERIES_TICKER_MISSING")
        series_payload = self.get_json(f"{self.base_url}/series/{series_ticker}", None)
        series = series_payload.get("series") if isinstance(series_payload, Mapping) else None
        if not isinstance(series, Mapping):
            raise KalshiContractAcquisitionError("KALSHI_SERIES_PAYLOAD_MISSING")
        if str(series.get("ticker") or "").strip() != series_ticker:
            raise KalshiContractAcquisitionError("KALSHI_SERIES_TICKER_MISMATCH")

        snapshot_material = {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "series_ticker": series_ticker,
            "retrieved_at": retrieved_at,
            "market": dict(market),
            "event": dict(event),
            "series": dict(series),
        }
        encoded = json.dumps(snapshot_material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        snapshot_id = "kalshi-rule-" + hashlib.sha256(encoded).hexdigest()
        return KalshiRuleAcquisition(
            ticker=ticker,
            event_ticker=event_ticker,
            series_ticker=series_ticker,
            retrieved_at=retrieved_at,
            market=dict(market),
            event=dict(event),
            series=dict(series),
            rule_snapshot_id=snapshot_id,
        )


def resolve_acquired_weather_contract(
    acquisition: KalshiRuleAcquisition,
    policy: WeatherSeriesRulePolicy,
) -> ContractSnapshot:
    """Cross-check a governed series policy against live Kalshi metadata.

    No missing semantic field is inferred from title/rules prose. Live market
    strike fields are treated as a cross-check only; the governed policy owns
    exact inclusive/exclusive semantics until a certified structured parser is
    implemented.
    """
    blockers: list[str] = []
    if policy.series_ticker.strip() != acquisition.series_ticker:
        blockers.append("SERIES_POLICY_TICKER_MISMATCH")

    settlement_sources = acquisition.series.get("settlement_sources")
    names = {
        str(row.get("name") or "").strip()
        for row in settlement_sources
        if isinstance(row, Mapping) and str(row.get("name") or "").strip()
    } if isinstance(settlement_sources, list) else set()
    if not names:
        blockers.append("LIVE_SETTLEMENT_SOURCE_LIST_MISSING")
    elif policy.settlement_source_name.strip() not in names:
        blockers.append("SETTLEMENT_SOURCE_POLICY_NOT_IN_LIVE_SERIES")

    market_floor = _optional_float(acquisition.market.get("floor_strike"))
    market_cap = _optional_float(acquisition.market.get("cap_strike"))
    if policy.threshold_lower is not None and market_floor is not None and abs(policy.threshold_lower - market_floor) > 1e-9:
        blockers.append("THRESHOLD_LOWER_LIVE_MARKET_MISMATCH")
    if policy.threshold_upper is not None and market_cap is not None and abs(policy.threshold_upper - market_cap) > 1e-9:
        blockers.append("THRESHOLD_UPPER_LIVE_MARKET_MISMATCH")

    if blockers:
        raise ContractResolutionError(code="NO_PLAY_SETTLEMENT_AMBIGUITY", blockers=tuple(blockers))

    market_title = str(acquisition.event.get("title") or acquisition.market.get("title") or "").strip()
    contract_title = str(acquisition.market.get("title") or acquisition.market.get("subtitle") or "").strip()
    yes_condition = str(acquisition.market.get("rules_primary") or "").strip()
    no_condition = str(acquisition.market.get("rules_secondary") or "").strip()
    if not market_title:
        market_title = f"Kalshi weather event {acquisition.event_ticker}"
    if not contract_title:
        contract_title = acquisition.ticker
    if not yes_condition or not no_condition:
        raise ContractResolutionError(
            code="NO_PLAY_SETTLEMENT_AMBIGUITY",
            blockers=("LIVE_CONTRACT_RULE_TEXT_MISSING",),
        )

    raw = {
        "market_title": market_title,
        "contract_title": contract_title,
        "ticker": acquisition.ticker,
        "lane": policy.lane,
        "yes_condition": yes_condition,
        "no_condition": no_condition,
        "location": policy.location,
        "metric": policy.metric,
        "units": policy.units,
        "observation_window": policy.observation_window,
        "timezone": policy.timezone,
        "settlement_source": policy.settlement_source_name,
        "settlement_station_id": policy.settlement_station_id,
        "settlement_station_name": policy.settlement_station_name,
        "settlement_location_type": policy.settlement_location_type,
        "settlement_latitude": policy.settlement_latitude,
        "settlement_longitude": policy.settlement_longitude,
        "rounding_convention": policy.rounding_convention,
        "trace_measurement_rules": policy.trace_measurement_rules,
        "market_close_time": str(acquisition.market.get("close_time") or "").strip(),
        "rule_snapshot_id": acquisition.rule_snapshot_id,
        "threshold_lower": policy.threshold_lower,
        "threshold_upper": policy.threshold_upper,
        "lower_inclusive": policy.lower_inclusive,
        "upper_inclusive": policy.upper_inclusive,
    }
    return resolve_weather_contract(raw)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
