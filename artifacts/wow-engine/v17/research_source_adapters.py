"""Typed external research-source adapters for WOW V17 Scout teams.

These adapters acquire research evidence only. They never produce governed
probability, qualification, stake, or execution authority.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import httpx

SPORTSDATAIO_BASE = os.environ.get("WOW_SPORTSDATAIO_BASE_URL", "https://api.sportsdata.io").rstrip("/")


@dataclass(frozen=True)
class SourceCapability:
    sport_key: str
    capability: str
    provider: str
    endpoint_template: str | None
    source_class: str
    supported: bool
    configurable: bool = False
    limitation: str | None = None


@dataclass
class ResearchFetchResult:
    ok: bool
    provider: str
    sport_key: str
    capability: str
    source_class: str
    data: Any = None
    status: int | None = None
    code: str | None = None
    observed_at: str | None = None
    endpoint: str | None = None
    prediction_authority: bool = False
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Only endpoint paths verified from current provider documentation are hard-coded.
# Unverified paths remain configurable instead of being guessed.
SPORTSDATAIO_CAPABILITIES: tuple[SourceCapability, ...] = (
    SourceCapability("americanfootball_nfl", "injuries", "SPORTSDATAIO", "/v3/nfl/projections/json/InjuredPlayers", "ESTABLISHED_STATS_PROVIDER", True),
    SourceCapability("americanfootball_nfl", "depth_charts", "SPORTSDATAIO", "/v3/nfl/scores/json/DepthCharts", "ESTABLISHED_STATS_PROVIDER", True),
    SourceCapability("baseball_mlb", "injuries", "SPORTSDATAIO", "/v3/mlb/projections/json/InjuredPlayers", "ESTABLISHED_STATS_PROVIDER", True),
    SourceCapability("baseball_mlb", "depth_charts", "SPORTSDATAIO", "/v3/mlb/projections/json/DepthCharts", "ESTABLISHED_STATS_PROVIDER", True),
    SourceCapability("baseball_mlb", "starting_lineups", "SPORTSDATAIO", "/v3/mlb/projections/json/StartingLineupsByDate/{date}", "ESTABLISHED_STATS_PROVIDER", True),
    SourceCapability("basketball_nba", "injuries", "SPORTSDATAIO", "/v3/nba/projections/json/InjuredPlayers", "ESTABLISHED_STATS_PROVIDER", True),
    SourceCapability("basketball_nba", "depth_charts", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", True, True, "Endpoint path must be configured until contract is explicitly verified."),
    SourceCapability("basketball_nba", "starting_lineups", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", True, True, "Endpoint path must be configured until contract is explicitly verified."),
    SourceCapability("americanfootball_ncaaf", "injuries", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", True, True, "College injury feed exists, but endpoint path must be configured after account contract verification."),
    SourceCapability("americanfootball_ncaaf", "depth_charts", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", False, False, "Provider does not supply college football depth charts."),
    SourceCapability("americanfootball_ncaaf", "starting_lineups", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", False, False, "Provider does not supply college football lineup data."),
    SourceCapability("basketball_wnba", "injuries", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", True, True, "WNBA injury coverage exists, but endpoint path must be configured after account contract verification."),
    SourceCapability("basketball_wnba", "depth_charts", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", False, False, "Provider does not supply WNBA depth charts."),
    SourceCapability("basketball_wnba", "starting_lineups", "SPORTSDATAIO", None, "ESTABLISHED_STATS_PROVIDER", False, False, "Provider does not confirm WNBA lineups pregame."),
)

_CAP_INDEX = {(c.sport_key, c.capability): c for c in SPORTSDATAIO_CAPABILITIES}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def capability_for(sport_key: str, capability: str) -> SourceCapability | None:
    return _CAP_INDEX.get((str(sport_key), str(capability)))


def capability_matrix() -> list[dict[str, Any]]:
    return [asdict(c) for c in SPORTSDATAIO_CAPABILITIES]


def _configured_path(cap: SourceCapability) -> str | None:
    if cap.endpoint_template:
        return cap.endpoint_template
    env_key = "WOW_SPORTSDATAIO_" + cap.sport_key.upper().replace("-", "_") + "_" + cap.capability.upper() + "_PATH"
    value = os.environ.get(env_key)
    return value.strip() if value and value.strip() else None


def sportsdataio_fetch(
    sport_key: str,
    capability: str,
    *,
    params: dict[str, Any] | None = None,
    path_values: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> ResearchFetchResult:
    cap = capability_for(sport_key, capability)
    if cap is None:
        return ResearchFetchResult(False, "SPORTSDATAIO", sport_key, capability, "ESTABLISHED_STATS_PROVIDER", code="SOURCE_CAPABILITY_UNKNOWN", observed_at=_now_iso())
    if not cap.supported:
        return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, code="SOURCE_CAPABILITY_UNSUPPORTED", observed_at=_now_iso())

    api_key = os.environ.get("SPORTSDATAIO_API_KEY") or os.environ.get("WOW_SPORTSDATAIO_API_KEY")
    if not api_key:
        return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, code="SOURCE_CREDENTIAL_UNCONFIGURED", observed_at=_now_iso())

    path = _configured_path(cap)
    if not path:
        return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, code="SOURCE_ENDPOINT_UNCONFIGURED", observed_at=_now_iso())

    for key, value in (path_values or {}).items():
        path = path.replace("{" + key + "}", str(value))
    if "{" in path or "}" in path:
        return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, code="SOURCE_PATH_PARAMETER_MISSING", observed_at=_now_iso())

    endpoint = SPORTSDATAIO_BASE + (path if path.startswith("/") else "/" + path)
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"}
    owns_client = client is None
    http = client or httpx.Client(timeout=20.0)
    try:
        response = http.get(endpoint, headers=headers, params=params or {})
        if response.status_code >= 400:
            return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, status=response.status_code,
                                       code=f"SPORTSDATAIO_HTTP_{response.status_code}", observed_at=_now_iso(), endpoint=endpoint)
        try:
            payload = response.json()
        except ValueError:
            return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, status=response.status_code,
                                       code="SPORTSDATAIO_INVALID_JSON", observed_at=_now_iso(), endpoint=endpoint)
        return ResearchFetchResult(True, cap.provider, sport_key, capability, cap.source_class, data=payload, status=response.status_code,
                                   code="SOURCE_FETCH_OK", observed_at=_now_iso(), endpoint=endpoint)
    except httpx.TimeoutException:
        return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, code="SOURCE_TIMEOUT", observed_at=_now_iso(), endpoint=endpoint)
    except httpx.HTTPError:
        return ResearchFetchResult(False, cap.provider, sport_key, capability, cap.source_class, code="SOURCE_HTTP_ERROR", observed_at=_now_iso(), endpoint=endpoint)
    finally:
        if owns_client:
            http.close()


def adapter_health() -> dict[str, Any]:
    configured = bool(os.environ.get("SPORTSDATAIO_API_KEY") or os.environ.get("WOW_SPORTSDATAIO_API_KEY"))
    return {
        "provider": "SPORTSDATAIO",
        "credential_configured": configured,
        "capability_count": len(SPORTSDATAIO_CAPABILITIES),
        "supported_capability_count": sum(1 for c in SPORTSDATAIO_CAPABILITIES if c.supported),
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = [
    "SourceCapability", "ResearchFetchResult", "SPORTSDATAIO_CAPABILITIES",
    "capability_for", "capability_matrix", "sportsdataio_fetch", "adapter_health",
]
