"""Versioned settlement-station registry for supported Kalshi daily-high series.

Seeded only from mappings already verified in the production weather lane. Nearby
station substitution is prohibited. Update this file only from audited contract rules.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

REGISTRY_VERSION = "weather-stations-2026-09-07-v1"

_STATIONS: dict[str, dict[str, Any]] = {
    "NYC": {"series": "KXHIGHNY", "city": "New York", "station_id": "KNYC", "station_name": "Central Park, New York", "nws_site": "OKX", "nws_issuedby": "NYC", "timezone": "America/New_York", "ncei_station_id": "GHCND:USW00094728"},
    "LA":  {"series": "KXHIGHLAX", "city": "Los Angeles", "station_id": "KLAX", "station_name": "Los Angeles Airport, CA", "nws_site": "LOX", "nws_issuedby": "LAX", "timezone": "America/Los_Angeles", "ncei_station_id": "GHCND:USW00023174"},
    "MIA": {"series": "KXHIGHMIA", "city": "Miami", "station_id": "KMIA", "station_name": "Miami International Airport", "nws_site": "MFL", "nws_issuedby": "MIA", "timezone": "America/New_York", "ncei_station_id": "GHCND:USW00012839"},
    "CHI": {"series": "KXHIGHCHI", "city": "Chicago", "station_id": "KMDW", "station_name": "Chicago Midway, IL", "nws_site": "LOT", "nws_issuedby": "MDW", "timezone": "America/Chicago", "ncei_station_id": "GHCND:USW00014819"},
    "AUS": {"series": "KXHIGHAUS", "city": "Austin", "station_id": "KAUS", "station_name": "Austin Bergstrom, TX", "nws_site": "EWX", "nws_issuedby": "AUS", "timezone": "America/Chicago", "ncei_station_id": "GHCND:USW00013904"},
}

_BANNED = {
    "MIA": {"KPBI", "PBI"},
    "LA": {"KBUR", "BUR"},
    "CHI": {"KORD", "ORD"},
}


def supported_stations() -> dict[str, dict[str, Any]]:
    return {code: {**deepcopy(row), "registry_version": REGISTRY_VERSION, "verified": True} for code, row in _STATIONS.items()}


def resolve_station(city_code: str, series: str | None = None) -> dict[str, Any]:
    code = str(city_code or "").strip().upper()
    row = _STATIONS.get(code)
    if row is None:
        raise ValueError("WEATHER_SETTLEMENT_CITY_UNSUPPORTED")
    if series and str(series).upper() != str(row["series"]).upper():
        raise ValueError("WEATHER_SERIES_STATION_MISMATCH")
    return {**deepcopy(row), "registry_version": REGISTRY_VERSION, "verified": True}


def validate_station(city_code: str, station_id: str, series: str | None = None) -> dict[str, Any]:
    code, station = str(city_code or "").strip().upper(), str(station_id or "").strip().upper()
    if station in _BANNED.get(code, set()):
        raise ValueError("SETTLEMENT_STATION_REGRESSION_BANNED")
    expected = resolve_station(code, series)
    if station != expected["station_id"]:
        raise ValueError("SETTLEMENT_STATION_MISMATCH")
    return expected
