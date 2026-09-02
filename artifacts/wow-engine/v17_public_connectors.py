"""V17 public evidence adapters. Network transport is injected for testability."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from v17_connector_contract import ConnectorPolicy, immutable_evidence_snapshot

JsonFetcher = Callable[[str, Mapping[str, str], Mapping[str, Any]], tuple[Any, Mapping[str, str]]]


class ConnectorFailure(RuntimeError):
    pass


def _safe_fetch(fetcher: JsonFetcher, url: str, *, host: str, headers=None, params=None):
    if (urlparse(url).hostname or "").lower() != host:
        raise ConnectorFailure("SOURCE_HOST_REJECTED")
    try:
        payload, response_headers = fetcher(url, headers or {}, params or {})
    except Exception as exc:
        raise ConnectorFailure("SOURCE_UNAVAILABLE_FAIL_CLOSED") from exc
    if not isinstance(payload, (dict, list)):
        raise ConnectorFailure("NON_JSON_SOURCE_RESPONSE")
    return payload, response_headers


class NWSStadiumWeatherConnector:
    policy = ConnectorPolicy(
        "NWS_API", "OFFICIAL_PUBLIC_US_GOV", 900,
        allowed_evidence_only_fields=("forecast", "observations", "alerts"),
        fallback_sources=("OFFICIAL_AIRPORT_OBSERVATION",),
    )

    def __init__(self, fetcher: JsonFetcher, user_agent: str):
        if not user_agent.strip():
            raise ValueError("NWS_USER_AGENT_REQUIRED")
        self.fetcher, self.headers = fetcher, {"User-Agent": user_agent, "Accept": "application/geo+json"}

    def stadium_forecast(self, latitude: float, longitude: float, *, event_id: str, requested_at: datetime) -> dict:
        point_url = f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}"
        point, _ = _safe_fetch(self.fetcher, point_url, host="api.weather.gov", headers=self.headers)
        forecast_url = point.get("properties", {}).get("forecastHourly")
        if not forecast_url:
            raise ConnectorFailure("NWS_FORECAST_LINK_MISSING")
        forecast, headers = _safe_fetch(self.fetcher, forecast_url, host="api.weather.gov", headers=self.headers)
        return immutable_evidence_snapshot(policy=self.policy, payload={"point": point, "forecast": forecast},
            request_timestamp=requested_at, source_published_timestamp=None, event_id=event_id,
            completeness_score=1.0 if forecast.get("properties", {}).get("periods") else 0.5)


class NHLPublicConnector:
    policy = ConnectorPolicy(
        "NHL_PUBLIC_WEB_API", "LEAGUE_PUBLIC_RATE_SENSITIVE", 1800,
        allowed_evidence_only_fields=("schedule", "roster", "play_by_play"),
        fallback_sources=("NHL_OFFICIAL_GAME_REPORT",),
    )
    base = "https://api-web.nhle.com/v1"

    def __init__(self, fetcher: JsonFetcher): self.fetcher = fetcher

    def capture(self, resource: str, identifier: str, *, event_id: str | None, requested_at: datetime) -> dict:
        paths = {"schedule": f"schedule/{identifier}", "roster": f"roster/{identifier}", "play_by_play": f"gamecenter/{identifier}/play-by-play"}
        if resource not in paths: raise ValueError("NHL_RESOURCE_UNSUPPORTED")
        payload, _ = _safe_fetch(self.fetcher, f"{self.base}/{paths[resource]}", host="api-web.nhle.com")
        return immutable_evidence_snapshot(policy=self.policy, payload={resource: payload}, request_timestamp=requested_at,
            source_published_timestamp=None, event_id=event_id, completeness_score=1.0 if payload else 0.0)


class NBAStatsBatchConnector:
    policy = ConnectorPolicy(
        "NBA_STATS", "LEAGUE_PUBLIC_RATE_SENSITIVE", 3600,
        allowed_evidence_only_fields=("batch_statistics", "minutes", "usage", "opportunity"),
        fallback_sources=("NBA_OFFICIAL_BOX_SCORE",),
    )
    allowed_endpoints = frozenset({"leaguegamelog", "leaguedashplayerstats", "boxscoretraditionalv3"})

    def __init__(self, fetcher: JsonFetcher): self.fetcher = fetcher

    def batch(self, endpoint: str, params: Mapping[str, Any], *, requested_at: datetime) -> dict:
        if endpoint not in self.allowed_endpoints: raise ValueError("NBA_ENDPOINT_UNSUPPORTED")
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com"}
        payload, _ = _safe_fetch(self.fetcher, f"https://stats.nba.com/stats/{endpoint}", host="stats.nba.com", headers=headers, params=params)
        return immutable_evidence_snapshot(policy=self.policy, payload={endpoint: payload}, request_timestamp=requested_at,
            source_published_timestamp=None, completeness_score=1.0 if payload else 0.0)


@dataclass
class OddsCreditBudget:
    monthly_limit: int
    reserve: int = 25
    used: int = 0

    def authorize(self, estimated_cost: int) -> None:
        if estimated_cost < 0 or self.used + estimated_cost > self.monthly_limit - self.reserve:
            raise ConnectorFailure("ODDS_API_CREDIT_BUDGET_CLOSED")

    def reconcile(self, response_headers: Mapping[str, str], estimated_cost: int) -> None:
        reported = response_headers.get("x-requests-used")
        self.used = int(reported) if reported is not None else self.used + estimated_cost


class OddsAPISnapshotConnector:
    policy = ConnectorPolicy(
        "THE_ODDS_API", "KEYED_COMMERCIAL_FREE_TIER", 300,
        allowed_evidence_only_fields=("opener", "decision_consensus", "close", "book_prices"),
        fallback_sources=("MANUAL_VERIFIED_MARKET_SNAPSHOT",),
    )

    def __init__(self, fetcher: JsonFetcher, budget: OddsCreditBudget, api_key: str | None = None):
        self.fetcher, self.budget = fetcher, budget
        self.api_key = api_key or os.getenv("THE_ODDS_API_KEY")
        if not self.api_key: raise ValueError("THE_ODDS_API_KEY_REQUIRED")

    def snapshot(self, sport: str, *, regions: str, markets: str, event_id: str | None, requested_at: datetime, estimated_cost: int = 1) -> dict:
        self.budget.authorize(estimated_cost)
        payload, headers = _safe_fetch(self.fetcher, f"https://api.the-odds-api.com/v4/sports/{sport}/odds", host="api.the-odds-api.com",
            params={"apiKey": self.api_key, "regions": regions, "markets": markets, "oddsFormat": "decimal"})
        self.budget.reconcile(headers, estimated_cost)
        return immutable_evidence_snapshot(policy=self.policy, payload={"odds": payload}, request_timestamp=requested_at,
            source_published_timestamp=None, event_id=event_id, completeness_score=1.0 if payload else 0.0)
