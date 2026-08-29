"""Read-only CFBD acquisition adapter for NCAAF model research.

Current documented base URL: https://api.collegefootballdata.com
Authentication: Bearer token in CFBD_API_KEY.

This adapter is deliberately narrow and allowlisted. It cannot write to CFBD,
place wagers, or substitute ratings/market data for WOW's independent QB,
depth-chart, injury, provenance, calibration, or final-refresh gates.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import httpx

CFBD_BASE_URL = "https://api.collegefootballdata.com"
CAN_EXECUTE = False

_ALLOWED_ENDPOINTS = {
    "/games",
    "/ratings/core",
    "/ratings/sp",
    "/ratings/srs",
    "/ratings/elo",
    "/ratings/fpi",
}


class CFBDUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CFBDResponse:
    endpoint: str
    params: Mapping[str, Any]
    rows: list[Mapping[str, Any]]


class CFBDClient:
    def __init__(self, *, api_key: str, base_url: str = CFBD_BASE_URL, timeout_seconds: float = 20.0):
        if not str(api_key or "").strip():
            raise CFBDUnavailable("CFBD_API_KEY_MISSING", "CFBD_API_KEY is required for read-only acquisition.")
        if base_url.rstrip("/") != CFBD_BASE_URL:
            raise CFBDUnavailable("CFBD_BASE_URL_NOT_APPROVED", "Only the approved CFBD production base URL is allowed.")
        self.api_key = api_key.strip()
        self.base_url = CFBD_BASE_URL
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_environment(cls) -> "CFBDClient":
        return cls(api_key=os.getenv("CFBD_API_KEY", ""))

    def get(self, endpoint: str, *, params: Optional[Mapping[str, Any]] = None) -> CFBDResponse:
        if endpoint not in _ALLOWED_ENDPOINTS:
            raise CFBDUnavailable("CFBD_ENDPOINT_NOT_ALLOWLISTED", f"Endpoint {endpoint!r} is not approved for NCAAF acquisition.")
        clean_params = {str(k): v for k, v in (params or {}).items() if v is not None}
        try:
            response = httpx.get(
                f"{self.base_url}{endpoint}",
                params=clean_params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise CFBDUnavailable("CFBD_REQUEST_FAILED", "CFBD read-only request failed.") from exc
        if response.status_code != 200:
            raise CFBDUnavailable(
                "CFBD_HTTP_ERROR",
                f"CFBD returned HTTP {response.status_code} for {endpoint}.",
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise CFBDUnavailable("CFBD_INVALID_JSON", "CFBD response was not valid JSON.") from exc
        if not isinstance(payload, list) or any(not isinstance(row, Mapping) for row in payload):
            raise CFBDUnavailable("CFBD_INVALID_RESPONSE", "CFBD endpoint did not return the expected row array.")
        return CFBDResponse(endpoint=endpoint, params=clean_params, rows=list(payload))

    def games(self, *, year: int, week: Optional[int] = None, classification: Optional[str] = None) -> CFBDResponse:
        if year < 2000 or year > 2100:
            raise ValueError("year is outside supported research bounds")
        return self.get(
            "/games",
            params={"year": year, "week": week, "classification": classification},
        )

    def ratings(self, family: str, *, year: int, week: Optional[int] = None) -> CFBDResponse:
        normalized = str(family or "").strip().lower()
        endpoint = {
            "core": "/ratings/core",
            "sp": "/ratings/sp",
            "srs": "/ratings/srs",
            "elo": "/ratings/elo",
            "fpi": "/ratings/fpi",
        }.get(normalized)
        if endpoint is None:
            raise CFBDUnavailable("CFBD_RATING_FAMILY_NOT_ALLOWLISTED", f"Rating family {family!r} is not approved.")
        params: dict[str, Any] = {"year": year}
        if normalized == "elo" and week is not None:
            params["week"] = week
        return self.get(endpoint, params=params)
