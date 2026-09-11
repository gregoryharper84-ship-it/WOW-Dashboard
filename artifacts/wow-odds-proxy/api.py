"""Read-only credential proxy for The Odds API v4.

Security properties:
- The vendor ``ODDS_API_KEY`` is read only from the server environment and is
  never accepted from a caller, returned in a response, or written to logs.
- Ordinary callers authenticate with ``WOW_ODDS_PROXY_ACTION_KEY`` via Bearer.
  The exact protected-main WOW Multi-Scout GitHub workflow may alternatively
  authenticate with a short-lived GitHub OIDC token whose issuer, audience,
  repository IDs, workflow ref, branch ref, event, and runner are verified.
- Only explicitly defined GET capabilities are proxied: active sports, events,
  event markets, and event odds. There is no generic URL/path proxy and no
  write method.
- Upstream failures are sanitized so request URLs/credentials are not echoed.

This service is acquisition-only. It cannot place, route, modify, cancel, or
approve a wager and has no connection to the WOW execution lane.
"""
from __future__ import annotations

import os
import secrets
from typing import Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from github_actions_oidc import GitHubOIDCValidationError, verify_github_actions_oidc

app = FastAPI(title="WOW Odds API Credential Proxy", version="1.3.0")

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
UPSTREAM_TIMEOUT_SECONDS = 12.0
QUOTA_HEADERS = ("x-requests-remaining", "x-requests-used", "x-requests-last")
CORE_EVENT_MARKETS = "h2h,spreads,totals"
MARKET_INVENTORY_FALLBACK_HEADER = "x-wow-market-inventory-fallback"
_market_inventory_endpoint_unavailable = False

SportPath = Path(..., pattern=r"^[A-Za-z0-9_]+$", min_length=1, max_length=100)
EventPath = Path(..., pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=128)


def _csv_query():
    """Return a fresh FastAPI Query object so parameter aliases cannot bleed."""
    return Query(
        None,
        pattern=r"^[A-Za-z0-9_.:-]+(?:,[A-Za-z0-9_.:-]+)*$",
        max_length=4096,
    )


def _require_proxy_action_key(authorization: Optional[str] = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "ODDS_PROXY_AUTH_REQUIRED", "can_execute": False})

    supplied = authorization[len("Bearer ") :]
    configured = os.environ.get("WOW_ODDS_PROXY_ACTION_KEY")
    if configured and secrets.compare_digest(supplied, configured):
        return

    try:
        verify_github_actions_oidc(supplied)
        return
    except GitHubOIDCValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "ODDS_PROXY_AUTH_INVALID", "can_execute": False},
        ) from exc


def _vendor_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail={"code": "ODDS_API_KEY_UNCONFIGURED", "can_execute": False})
    return key


def _clean_params(**values) -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            params[key] = "true" if value else "false"
        else:
            params[key] = str(value)
    return params


def _http_get(url: str, params: dict[str, str]) -> httpx.Response:
    with httpx.Client(timeout=UPSTREAM_TIMEOUT_SECONDS, follow_redirects=False) as client:
        return client.get(url, params=params, headers={"Accept": "application/json"})


def _quota_headers(response: httpx.Response) -> dict[str, str]:
    return {header: response.headers[header] for header in QUOTA_HEADERS if header in response.headers}


def _safe_upstream_message(response: httpx.Response) -> Optional[str]:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    for field in ("message", "error", "detail"):
        value = payload.get(field)
        if isinstance(value, str):
            vendor_key = os.environ.get("ODDS_API_KEY")
            if vendor_key:
                value = value.replace(vendor_key, "[REDACTED]")
            return value[:500]
    return None


def _upstream_error_response(response: httpx.Response) -> JSONResponse:
    body = {
        "ok": False,
        "code": "ODDS_API_UPSTREAM_ERROR",
        "upstream_status": response.status_code,
        "can_execute": False,
    }
    message = _safe_upstream_message(response)
    if message:
        body["message"] = message
    return JSONResponse(content=body, status_code=response.status_code, headers=_quota_headers(response))


def _proxy_get(upstream_path: str, params: dict[str, str]) -> JSONResponse:
    upstream_params = dict(params)
    upstream_params["apiKey"] = _vendor_key()
    try:
        response = _http_get(f"{ODDS_API_BASE}{upstream_path}", upstream_params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError):
        raise HTTPException(status_code=502, detail={"code": "ODDS_API_UPSTREAM_UNREACHABLE", "can_execute": False})
    headers = _quota_headers(response)
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail={"code": "ODDS_API_UPSTREAM_NON_JSON", "can_execute": False})
    if 200 <= response.status_code < 300:
        return JSONResponse(content=payload, status_code=response.status_code, headers=headers)
    return _upstream_error_response(response)


def _core_market_inventory_fallback(upstream_odds_path: str, params: dict[str, str]) -> JSONResponse:
    """Use live core event odds as a market-key inventory when /markets is not entitled.

    The returned payload is the vendor's real event-odds response, not synthetic
    bookmaker evidence. Scout only derives the available market keys from it and
    then performs its normal evidence fetch. This intentionally exposes only
    h2h/spreads/totals; prop discovery remains unavailable without market inventory.
    """
    fallback_params = dict(params)
    fallback_params.update({"markets": CORE_EVENT_MARKETS, "oddsFormat": "american"})
    fallback_params["apiKey"] = _vendor_key()
    try:
        response = _http_get(f"{ODDS_API_BASE}{upstream_odds_path}", fallback_params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError):
        raise HTTPException(status_code=502, detail={"code": "ODDS_API_UPSTREAM_UNREACHABLE", "can_execute": False})
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail={"code": "ODDS_API_UPSTREAM_NON_JSON", "can_execute": False})
    if 200 <= response.status_code < 300:
        headers = _quota_headers(response)
        headers[MARKET_INVENTORY_FALLBACK_HEADER] = "core-event-odds"
        return JSONResponse(content=payload, status_code=response.status_code, headers=headers)
    return _upstream_error_response(response)


def _require_regions_or_bookmakers(regions: Optional[str], bookmakers: Optional[str]) -> None:
    if not regions and not bookmakers:
        raise HTTPException(status_code=422, detail={"code": "REGIONS_OR_BOOKMAKERS_REQUIRED", "message": "Provide regions or bookmakers for this Odds API request.", "can_execute": False})


@app.get("/odds-api/health")
def health():
    return {
        "status": "ok", "service": "WOW_ODDS_API_CREDENTIAL_PROXY", "compute_provider": "RENDER",
        "vendor": "THE_ODDS_API_V4", "read_only": True,
        "vendor_key_configured": bool(os.environ.get("ODDS_API_KEY")),
        "caller_bearer_auth_configured": bool(os.environ.get("WOW_ODDS_PROXY_ACTION_KEY")),
        "github_multiscout_oidc_enabled": True,
        "market_inventory_core_fallback_enabled": True,
        "can_execute": False,
    }


@app.get("/odds-api/v4/sports", dependencies=[Depends(_require_proxy_action_key)])
def get_sports(all_sports: Optional[bool] = Query(None, alias="all")):
    """Return the upstream sport inventory so Scout coverage is not hard-coded."""
    return _proxy_get("/sports", _clean_params(all=all_sports))


@app.get("/odds-api/v4/sports/{sport}/events", dependencies=[Depends(_require_proxy_action_key)])
def get_events(
    sport: str = SportPath,
    date_format: Literal["iso", "unix"] = Query("iso", alias="dateFormat"),
    event_ids: Optional[str] = Query(None, alias="eventIds", pattern=r"^[A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*$", max_length=4096),
    commence_time_from: Optional[str] = Query(None, alias="commenceTimeFrom", max_length=64),
    commence_time_to: Optional[str] = Query(None, alias="commenceTimeTo", max_length=64),
    include_rotation_numbers: Optional[bool] = Query(None, alias="includeRotationNumbers"),
):
    params = _clean_params(dateFormat=date_format, eventIds=event_ids, commenceTimeFrom=commence_time_from, commenceTimeTo=commence_time_to, includeRotationNumbers=include_rotation_numbers)
    return _proxy_get(f"/sports/{sport}/events", params)


@app.get("/odds-api/v4/sports/{sport}/events/{event_id}/markets", dependencies=[Depends(_require_proxy_action_key)])
def get_event_markets(
    sport: str = SportPath, event_id: str = EventPath, regions: Optional[str] = _csv_query(),
    bookmakers: Optional[str] = _csv_query(), date_format: Literal["iso", "unix"] = Query("iso", alias="dateFormat"),
):
    global _market_inventory_endpoint_unavailable
    _require_regions_or_bookmakers(regions, bookmakers)
    params = _clean_params(regions=regions, bookmakers=bookmakers, dateFormat=date_format)
    odds_path = f"/sports/{sport}/events/{event_id}/odds"

    if _market_inventory_endpoint_unavailable:
        return _core_market_inventory_fallback(odds_path, params)

    upstream_params = dict(params)
    upstream_params["apiKey"] = _vendor_key()
    try:
        response = _http_get(f"{ODDS_API_BASE}/sports/{sport}/events/{event_id}/markets", upstream_params)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError):
        raise HTTPException(status_code=502, detail={"code": "ODDS_API_UPSTREAM_UNREACHABLE", "can_execute": False})

    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail={"code": "ODDS_API_UPSTREAM_NON_JSON", "can_execute": False})

    if 200 <= response.status_code < 300:
        return JSONResponse(content=payload, status_code=response.status_code, headers=_quota_headers(response))

    # The same key can successfully access /sports and /events while /markets is
    # plan/endpoint-restricted. Do not let that optional inventory capability zero
    # the entire Scout slate. Fall back only for 401/403 and only to live core odds.
    if response.status_code in {401, 403}:
        _market_inventory_endpoint_unavailable = True
        return _core_market_inventory_fallback(odds_path, params)

    return _upstream_error_response(response)


@app.get("/odds-api/v4/sports/{sport}/events/{event_id}/odds", dependencies=[Depends(_require_proxy_action_key)])
def get_event_odds(
    sport: str = SportPath, event_id: str = EventPath,
    markets: str = Query(..., pattern=r"^[A-Za-z0-9_]+(?:,[A-Za-z0-9_]+)*$", max_length=4096),
    regions: Optional[str] = _csv_query(), bookmakers: Optional[str] = _csv_query(),
    date_format: Literal["iso", "unix"] = Query("iso", alias="dateFormat"),
    odds_format: Literal["decimal", "american"] = Query("american", alias="oddsFormat"),
    include_links: Optional[bool] = Query(None, alias="includeLinks"), include_sids: Optional[bool] = Query(None, alias="includeSids"),
    include_bet_limits: Optional[bool] = Query(None, alias="includeBetLimits"),
    include_rotation_numbers: Optional[bool] = Query(None, alias="includeRotationNumbers"),
    include_multipliers: Optional[bool] = Query(None, alias="includeMultipliers"),
):
    _require_regions_or_bookmakers(regions, bookmakers)
    params = _clean_params(markets=markets, regions=regions, bookmakers=bookmakers, dateFormat=date_format, oddsFormat=odds_format, includeLinks=include_links, includeSids=include_sids, includeBetLimits=include_bet_limits, includeRotationNumbers=include_rotation_numbers, includeMultipliers=include_multipliers)
    return _proxy_get(f"/sports/{sport}/events/{event_id}/odds", params)
