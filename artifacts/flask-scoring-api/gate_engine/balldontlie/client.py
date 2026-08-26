"""
gate_engine/balldontlie/client.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

BallDontLie HTTP client with tier/capability detection.

Features
--------
- API key from env var `balldontlie` or `BALLDONTLIE_API_KEY` (secure secrets only)
- Lazy tier detection: first authenticated call probes tier-specific endpoints
- Per-process capability cache (TTL = 1 hour)
- Never assumes an endpoint/field exists because another sport or tier exposes it
- Structured status for every failure mode: AUTH_REQUIRED, AUTH_FAILED,
  RATE_LIMITED, NOT_IN_TIER, TIMEOUT, HTTP_ERROR, PARSE_ERROR
- Never raises — all errors returned as BDLResponse with status field
- Missing/invalid credentials → AUTH_REQUIRED; base model continues
- Rate limit detected → RATE_LIMITED; base model continues

can_execute=False unconditional.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.balldontlie.types import (
    BDLResponse,
    BDLStatus,
    BDLTier,
    BDL_SOURCE_NAME,
)

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------

BDL_NBA_BASE  = "https://api.balldontlie.io/v1"
BDL_WNBA_BASE = "https://api.balldontlie.io/wnba/v1"
BDL_MLB_BASE  = "https://api.balldontlie.io/mlb/v1"

_HTTP_TIMEOUT = 12   # seconds

# ---------------------------------------------------------------------------
# Tier probe map: tier → endpoint to probe
# If a probe succeeds (200), the account has at least that tier.
# ---------------------------------------------------------------------------

_TIER_PROBES: list[tuple[str, str]] = [
    # (tier, endpoint_path_from_base)
    # GOAT first (strongest gate)
    (BDLTier.GOAT,     f"{BDL_MLB_BASE}/game_innings"),
    # ALL_STAR
    (BDLTier.ALL_STAR, f"{BDL_NBA_BASE}/player_injuries"),
    # STARTER (use a known larger dataset endpoint)
    (BDLTier.STARTER,  f"{BDL_NBA_BASE}/standings"),
    # FREE — if /v1/players works at all
    (BDLTier.FREE,     f"{BDL_NBA_BASE}/players"),
]

# ---------------------------------------------------------------------------
# Process-level capability cache
# ---------------------------------------------------------------------------

_LOCK           = threading.Lock()
_TIER_DETECTED:  str                = BDLTier.UNKNOWN
_TIER_DETECTED_AT: float | None     = None
_ENDPOINT_CACHE: dict[str, bool]    = {}   # endpoint_url → available
_TIER_CACHE_TTL: float              = 3600.0  # 1 hour


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def _api_key() -> str:
    """Resolve BDL API key from secure app secrets."""
    return (
        os.environ.get("balldontlie") or
        os.environ.get("BALLDONTLIE_API_KEY") or
        ""
    )


def credentials_available() -> bool:
    """Return True when a BDL API key is present."""
    return bool(_api_key())


# ---------------------------------------------------------------------------
# Core HTTP wrapper
# ---------------------------------------------------------------------------

def _get(url: str, params: dict[str, Any] | None = None) -> BDLResponse:
    """
    Single HTTP GET against BDL.

    Returns BDLResponse with status set to one of BDLStatus constants.
    Never raises.
    """
    import requests  # local import for cold-start safety

    key = _api_key()
    if not key:
        return BDLResponse(
            status=BDLStatus.AUTH_REQUIRED,
            endpoint=url,
            notes=["balldontlie secret not set"],
        )

    try:
        resp = requests.get(
            url,
            headers={"Authorization": key},
            params=params or {},
            timeout=_HTTP_TIMEOUT,
        )
    except Exception as exc:
        short = str(exc)[:80]
        if "timed out" in short.lower() or "timeout" in short.lower():
            return BDLResponse(status=BDLStatus.TIMEOUT, endpoint=url,
                               notes=[f"timeout:{short}"])
        return BDLResponse(status=BDLStatus.HTTP_ERROR, endpoint=url,
                           notes=[f"request_error:{short}"])

    if resp.status_code == 200:
        try:
            body = resp.json()
        except Exception as exc:
            return BDLResponse(status=BDLStatus.PARSE_ERROR, endpoint=url,
                               notes=[f"json_parse:{exc!s:.60}"])
        return BDLResponse(
            status   = BDLStatus.OK,
            data     = body.get("data") or [],
            meta     = body.get("meta") or {},
            raw      = body,
            endpoint = url,
        )

    if resp.status_code in (401, 403):
        return BDLResponse(status=BDLStatus.AUTH_FAILED, endpoint=url,
                           notes=[f"http_{resp.status_code}"])
    if resp.status_code == 429:
        return BDLResponse(status=BDLStatus.RATE_LIMITED, endpoint=url,
                           notes=["rate_limited_429"])
    if resp.status_code == 404:
        return BDLResponse(status=BDLStatus.ENDPOINT_404, endpoint=url,
                           notes=[f"http_404"])
    if resp.status_code in (402, 403):
        # 402 Payment Required = plan gate
        return BDLResponse(status=BDLStatus.NOT_IN_TIER, endpoint=url,
                           notes=[f"http_{resp.status_code}_plan_gate"])
    return BDLResponse(status=BDLStatus.HTTP_ERROR, endpoint=url,
                       notes=[f"http_{resp.status_code}"])


# ---------------------------------------------------------------------------
# Tier / capability detection
# ---------------------------------------------------------------------------

def detect_tier(force: bool = False) -> str:
    """
    Detect BDL subscription tier by probing tier-specific endpoints.
    Cached for 1 hour per process. Returns one of BDLTier constants.

    Never raises. Returns BDLTier.UNAVAILABLE when credentials are absent.
    """
    global _TIER_DETECTED, _TIER_DETECTED_AT

    if not credentials_available():
        return BDLTier.UNAVAILABLE

    now = time.monotonic()
    with _LOCK:
        if (
            not force
            and _TIER_DETECTED != BDLTier.UNKNOWN
            and _TIER_DETECTED_AT is not None
            and (now - _TIER_DETECTED_AT) < _TIER_CACHE_TTL
        ):
            return _TIER_DETECTED

        detected = BDLTier.FREE   # assume at least FREE if credentials present
        for tier, probe_url in _TIER_PROBES:
            resp = _get(probe_url, {"per_page": 1})
            if resp.ok:
                detected = tier
                break   # first probe that succeeds = this tier
            if resp.auth_blocked:
                detected = BDLTier.UNAVAILABLE
                break
            # 402/403/404/NOT_IN_TIER → tier not available; try next
        _TIER_DETECTED    = detected
        _TIER_DETECTED_AT = now
        return detected


def endpoint_available(url: str) -> bool:
    """
    Check whether a specific BDL endpoint URL is accessible.
    Result is cached per process; probes on first call.
    Returns False if credentials absent or endpoint returns non-200.
    """
    global _ENDPOINT_CACHE
    if not credentials_available():
        return False
    with _LOCK:
        if url in _ENDPOINT_CACHE:
            return _ENDPOINT_CACHE[url]

    resp = _get(url, {"per_page": 1})
    available = resp.ok
    with _LOCK:
        _ENDPOINT_CACHE[url] = available
    return available


def endpoint_available_for_tier(required_tier: str) -> bool:
    """
    Return True if the detected tier is at or above required_tier.
    Tier ordering: FREE < STARTER < ALL_STAR < GOAT.
    """
    tier_rank = {
        BDLTier.UNAVAILABLE: -1,
        BDLTier.UNKNOWN:      0,
        BDLTier.FREE:         1,
        BDLTier.STARTER:      2,
        BDLTier.ALL_STAR:     3,
        BDLTier.GOAT:         4,
    }
    detected = detect_tier()
    return tier_rank.get(detected, 0) >= tier_rank.get(required_tier, 99)


# ---------------------------------------------------------------------------
# Paginated fetch (automatically follows cursor pagination)
# ---------------------------------------------------------------------------

def fetch_all(
    url: str,
    params: dict[str, Any] | None = None,
    max_pages: int = 5,
    per_page: int = 100,
) -> BDLResponse:
    """
    Fetch all pages from a BDL endpoint (cursor pagination).

    Returns a single BDLResponse with all data items combined.
    Stops at max_pages to prevent unbounded fetching.
    """
    p = dict(params or {})
    p["per_page"] = per_page

    all_data: list[dict] = []
    resp = _get(url, p)
    if not resp.ok:
        return resp

    all_data.extend(resp.data)

    cursor = (resp.meta or {}).get("next_cursor")
    page = 1
    while cursor and page < max_pages:
        p2 = dict(p)
        p2["cursor"] = cursor
        r2 = _get(url, p2)
        if not r2.ok:
            break
        all_data.extend(r2.data)
        cursor = (r2.meta or {}).get("next_cursor")
        page += 1

    return BDLResponse(
        status   = BDLStatus.OK,
        data     = all_data,
        meta     = resp.meta,
        endpoint = url,
        notes    = [f"pages_fetched={page+1}"] if page > 0 else [],
    )
