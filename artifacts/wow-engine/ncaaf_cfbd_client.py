"""Read-only CFBD acquisition adapter for NCAAF model research.

Current documented base URL: https://api.collegefootballdata.com
Authentication: Bearer token in CFBD_API_KEY.

This adapter is deliberately narrow and allowlisted. It cannot write to CFBD,
place wagers, or substitute ratings/market data for WOW's independent QB,
depth-chart, injury, provenance, calibration, or final-refresh gates.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

import httpx

CFBD_BASE_URL = "https://api.collegefootballdata.com"
CAN_EXECUTE = False

_ALLOWED_ENDPOINTS = {
    "/games",
    "/games/players",
    "/ratings/core",
    "/ratings/sp",
    "/ratings/srs",
    "/ratings/elo",
    "/ratings/fpi",
}


class CFBDUnavailable(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
        rate_limit_retries: int = 0,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = int(http_status) if http_status is not None else None
        self.retry_after_seconds = (
            float(retry_after_seconds) if retry_after_seconds is not None else None
        )
        self.rate_limit_retries = int(rate_limit_retries)


@dataclass(frozen=True)
class CFBDResponse:
    endpoint: str
    params: Mapping[str, Any]
    rows: list[Mapping[str, Any]]


def _bounded_retry_after(response: Any, *, maximum: float) -> float | None:
    """Return a safe Retry-After delay only when CFBD explicitly supplies one.

    WOW does not guess a delay for 429s because a quota/entitlement exhaustion can
    also surface as a rate-limit response. Retrying without an explicit bounded
    provider signal would waste quota and extend request latency without evidence.
    """
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value <= 0.0 or value > float(maximum):
        return None
    return value


class CFBDClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = CFBD_BASE_URL,
        timeout_seconds: float = 20.0,
        max_rate_limit_retries: int = 2,
        max_retry_after_seconds: float = 30.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if not str(api_key or "").strip():
            raise CFBDUnavailable("CFBD_API_KEY_MISSING", "CFBD_API_KEY is required for read-only acquisition.")
        if base_url.rstrip("/") != CFBD_BASE_URL:
            raise CFBDUnavailable("CFBD_BASE_URL_NOT_APPROVED", "Only the approved CFBD production base URL is allowed.")
        if int(max_rate_limit_retries) < 0 or int(max_rate_limit_retries) > 3:
            raise ValueError("max_rate_limit_retries must be between 0 and 3")
        if float(max_retry_after_seconds) <= 0.0 or float(max_retry_after_seconds) > 60.0:
            raise ValueError("max_retry_after_seconds must be in (0, 60]")
        self.api_key = api_key.strip()
        self.base_url = CFBD_BASE_URL
        self.timeout_seconds = float(timeout_seconds)
        self.max_rate_limit_retries = int(max_rate_limit_retries)
        self.max_retry_after_seconds = float(max_retry_after_seconds)
        self._sleep = sleep_fn

    @classmethod
    def from_environment(cls) -> "CFBDClient":
        return cls(api_key=os.getenv("CFBD_API_KEY", ""))

    def get(self, endpoint: str, *, params: Optional[Mapping[str, Any]] = None) -> CFBDResponse:
        if endpoint not in _ALLOWED_ENDPOINTS:
            raise CFBDUnavailable("CFBD_ENDPOINT_NOT_ALLOWLISTED", f"Endpoint {endpoint!r} is not approved for NCAAF acquisition.")
        clean_params = {str(k): v for k, v in (params or {}).items() if v is not None}
        rate_limit_retries = 0
        last_retry_after: float | None = None
        while True:
            try:
                response = httpx.get(
                    f"{self.base_url}{endpoint}",
                    params=clean_params,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:
                raise CFBDUnavailable("CFBD_REQUEST_FAILED", "CFBD read-only request failed.") from exc

            if response.status_code == 429:
                last_retry_after = _bounded_retry_after(
                    response, maximum=self.max_retry_after_seconds
                )
                if (
                    last_retry_after is not None
                    and rate_limit_retries < self.max_rate_limit_retries
                ):
                    rate_limit_retries += 1
                    self._sleep(last_retry_after)
                    continue

            if response.status_code != 200:
                raise CFBDUnavailable(
                    "CFBD_HTTP_ERROR",
                    f"CFBD returned HTTP {response.status_code} for {endpoint}.",
                    http_status=response.status_code,
                    retry_after_seconds=last_retry_after,
                    rate_limit_retries=rate_limit_retries,
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

    def player_game_stats(
        self,
        *,
        year: int,
        week: Optional[int] = None,
        team: Optional[str] = None,
        conference: Optional[str] = None,
        classification: Optional[str] = "fbs",
        season_type: Optional[str] = "both",
        category: Optional[str] = None,
    ) -> CFBDResponse:
        """Fetch settled player box-score statistics through CFBD's documented route.

        CFBD requires a week, team, or conference when filtering by year. WOW's
        historical maintenance uses weekly FBS pulls so each request has a bounded,
        auditable sporting scope. This method only acquires source data; it grants
        no model capability or probability authority.
        """
        if year < 2000 or year > 2100:
            raise ValueError("year is outside supported research bounds")
        if week is None and not str(team or "").strip() and not str(conference or "").strip():
            raise ValueError("CFBD player stats require week, team, or conference when year is supplied")
        if week is not None and (int(week) < 0 or int(week) > 30):
            raise ValueError("week is outside supported NCAAF bounds")
        return self.get(
            "/games/players",
            params={
                "year": year,
                "week": week,
                "team": team,
                "conference": conference,
                "classification": classification,
                "seasonType": season_type,
                "category": category,
            },
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
