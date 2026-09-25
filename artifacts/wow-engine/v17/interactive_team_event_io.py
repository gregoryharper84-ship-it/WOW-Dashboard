"""Interactive TEAM_EVENT I/O optimizations for the V17 Action path.

This module reduces transport wall time without changing fitted-model math,
calibration, ranking, terminal reduction, or execution authority.

Two narrowly scoped optimizations are installed:
1. Reuse the accepted event-api Supabase client for the process lifetime so
   repeated canonical hydration/governance calls reuse the same HTTP connection
   pool instead of constructing a new client per lookup.
2. Replace only the interactive MLB prospective scorer's external player-platoon
   fetcher with a connection-pooled, TTL-cached StatsAPI fetch whose network
   timeout is bounded below the ChatGPT Action transport budget.

A cache miss that cannot obtain fresh external evidence still fails closed via
``ProspectiveModelUnavailable``. Cached evidence is used only inside its TTL;
expired data is never silently substituted. ``can_execute`` remains false.
"""
from __future__ import annotations

import logging
import os
from threading import Lock
from time import monotonic, perf_counter
from typing import Any

import httpx


LOGGER = logging.getLogger("wow.v17.interactive_team_event_io")
CAN_EXECUTE = False
_STATE_KEY_DB = "_wow_v17_interactive_cached_client_installed"
_STATE_KEY_MLB = "_wow_v17_interactive_mlb_evidence_installed"
_DEFAULT_STATS_TIMEOUT_SECONDS = 4.0
_DEFAULT_STATS_CACHE_TTL_SECONDS = 600.0
_MIN_STATS_TIMEOUT_SECONDS = 1.0
_MAX_STATS_TIMEOUT_SECONDS = 6.0
_MIN_STATS_CACHE_TTL_SECONDS = 30.0
_MAX_STATS_CACHE_TTL_SECONDS = 1800.0

_DB_LOCK = Lock()
_STATS_CLIENT_LOCK = Lock()
_STATS_CACHE_LOCK = Lock()
_STATS_CLIENT: httpx.Client | None = None
_STATS_CACHE: dict[tuple[int, tuple[int, ...]], tuple[float, dict[int, dict[str, Any]]]] = {}


def _env_float(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lower), upper)


def _stats_timeout_seconds() -> float:
    return _env_float(
        "WOW_V17_MLB_STATSAPI_TIMEOUT_SECONDS",
        _DEFAULT_STATS_TIMEOUT_SECONDS,
        _MIN_STATS_TIMEOUT_SECONDS,
        _MAX_STATS_TIMEOUT_SECONDS,
    )


def _stats_cache_ttl_seconds() -> float:
    return _env_float(
        "WOW_V17_MLB_STATS_CACHE_TTL_SECONDS",
        _DEFAULT_STATS_CACHE_TTL_SECONDS,
        _MIN_STATS_CACHE_TTL_SECONDS,
        _MAX_STATS_CACHE_TTL_SECONDS,
    )


def _stats_client() -> httpx.Client:
    global _STATS_CLIENT
    client = _STATS_CLIENT
    if client is not None:
        return client
    with _STATS_CLIENT_LOCK:
        client = _STATS_CLIENT
        if client is None:
            total = _stats_timeout_seconds()
            client = httpx.Client(
                timeout=httpx.Timeout(total, connect=min(2.0, total)),
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
                headers={"User-Agent": "WOW-V17-Team-Event/1.0"},
            )
            _STATS_CLIENT = client
        return client


def _copy_player_map(value: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    # Downstream reads these payloads but does not mutate them. A shallow mapping
    # copy prevents accidental key mutation of the cache while avoiding expensive
    # deep copies of the StatsAPI response.
    return dict(value)


def _bounded_cached_player_stats(player_ids: list[int], season: int) -> dict[int, dict[str, Any]]:
    import mlb_event_specialist_v16 as specialist

    ids_tuple = tuple(sorted({int(value) for value in player_ids}))
    if not ids_tuple:
        return {}
    key = (int(season), ids_tuple)
    now = monotonic()
    ttl = _stats_cache_ttl_seconds()
    with _STATS_CACHE_LOCK:
        cached = _STATS_CACHE.get(key)
        if cached is not None and now - cached[0] <= ttl:
            LOGGER.info(
                "WOW_V17_INTERACTIVE_STAGE route=/score-team-event stage=mlb-player-stats-cache hit=true players=%s can_execute=false",
                len(ids_tuple),
            )
            return _copy_player_map(cached[1])
        if cached is not None:
            _STATS_CACHE.pop(key, None)

    ids = ",".join(str(value) for value in ids_tuple)
    url = (
        "https://statsapi.mlb.com/api/v1/people"
        f"?personIds={ids}"
        "&hydrate=stats(group=[hitting],type=[season,statSplits],sitCodes=vl,vr)"
        f"&season={int(season)}"
    )
    started = perf_counter()
    try:
        response = _stats_client().get(url)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        LOGGER.warning(
            "WOW_V17_INTERACTIVE_STAGE route=/score-team-event stage=mlb-player-stats-fetch status=FAILED error=%s stage_ms=%.3f can_execute=false",
            type(exc).__name__,
            (perf_counter() - started) * 1000.0,
        )
        raise specialist.ProspectiveModelUnavailable(
            "official_player_platoon_stats_unavailable"
        ) from exc

    out: dict[int, dict[str, Any]] = {}
    for player in body.get("people", []) or []:
        if isinstance(player, dict) and player.get("id") is not None:
            out[int(player["id"])] = player
    with _STATS_CACHE_LOCK:
        _STATS_CACHE[key] = (monotonic(), out)
    LOGGER.warning(
        "WOW_V17_INTERACTIVE_STAGE route=/score-team-event stage=mlb-player-stats-fetch status=PASS players=%s stage_ms=%.3f can_execute=false",
        len(ids_tuple),
        (perf_counter() - started) * 1000.0,
    )
    return _copy_player_map(out)


def install_interactive_event_api_client_reuse(event_api: Any) -> bool:
    """Reuse only the accepted TEAM_EVENT event-api client factory."""
    if getattr(event_api, _STATE_KEY_DB, False):
        return True
    original = getattr(event_api, "get_client", None)
    if not callable(original):
        return False

    cached: dict[str, Any] = {"client": None}

    def _get_client() -> Any:
        client = cached["client"]
        if client is not None:
            return client
        with _DB_LOCK:
            client = cached["client"]
            if client is None:
                started = perf_counter()
                client = original()
                cached["client"] = client
                LOGGER.warning(
                    "WOW_V17_INTERACTIVE_STAGE route=/score-team-event stage=supabase-client-create stage_ms=%.3f can_execute=false",
                    (perf_counter() - started) * 1000.0,
                )
            return client

    event_api.get_client = _get_client
    setattr(event_api, _STATE_KEY_DB, True)
    return True


def install_interactive_mlb_evidence_fetch() -> bool:
    """Inject bounded/cached external MLB evidence into the existing scorer."""
    import mlb_event_specialist_v16 as specialist

    if getattr(specialist, _STATE_KEY_MLB, False):
        return True
    original = specialist.score_prospective_event

    def _score_prospective_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("stats_fetcher", _bounded_cached_player_stats)
        return original(*args, **kwargs)

    specialist.score_prospective_event = _score_prospective_event
    setattr(specialist, _STATE_KEY_MLB, True)
    return True


def install_interactive_team_event_io(*, event_api: Any) -> bool:
    db_ok = install_interactive_event_api_client_reuse(event_api)
    mlb_ok = install_interactive_mlb_evidence_fetch()
    # Install contract binding *after* the I/O wrapper so its saved incumbent
    # scorer preserves the bounded/cached StatsAPI behavior.  This adds only
    # score-time schema validation and audit receipts; probability math is unchanged.
    from v17.mlb_feature_contract_binding import install_mlb_feature_contract_binding

    feature_contract_ok = install_mlb_feature_contract_binding()
    return bool(db_ok and mlb_ok and feature_contract_ok)


__all__ = [
    "CAN_EXECUTE",
    "install_interactive_event_api_client_reuse",
    "install_interactive_mlb_evidence_fetch",
    "install_interactive_team_event_io",
]
