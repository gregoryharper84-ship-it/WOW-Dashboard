"""Bounded positive-result caches for the V17 interactive prop path.

The live Pick Request boundary historically re-read the same runtime capability
and certified route/artifact several times while processing a small interactive
batch. Successful registry results are cached only briefly; failures, blocked
states, missing artifacts, model outputs, probabilities, calibration, terminal
labels, and execution authority are never cached.
"""
from __future__ import annotations

from copy import deepcopy
import logging
import os
from threading import Lock, RLock, local
from time import monotonic
from typing import Any, Callable, Hashable

import prop_fitted_provider

_LOG = logging.getLogger("wow.v17.interactive.registry_cache")
_STATE_ATTR = "_wow_v17_interactive_registry_cache_installed"
_DEFAULT_TTL_SECONDS = 10.0
_MIN_TTL_SECONDS = 1.0
_MAX_TTL_SECONDS = 30.0

_CACHE_LOCK = RLock()
_CACHE: dict[tuple[str, Hashable], tuple[float, Any]] = {}
_KEY_LOCKS: dict[tuple[str, Hashable], Lock] = {}
_THREAD_LOCAL = local()


def _ttl_seconds() -> float:
    raw = os.getenv("WOW_INTERACTIVE_REGISTRY_CACHE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_TTL_SECONDS
    return min(max(value, _MIN_TTL_SECONDS), _MAX_TTL_SECONDS)


def _cache_get(namespace: str, key: Hashable) -> Any | None:
    now = monotonic()
    cache_key = (namespace, key)
    with _CACHE_LOCK:
        entry = _CACHE.get(cache_key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= now:
            _CACHE.pop(cache_key, None)
            return None
        return deepcopy(value)


def _cache_put(namespace: str, key: Hashable, value: Any) -> None:
    cache_key = (namespace, key)
    with _CACHE_LOCK:
        _CACHE[cache_key] = (monotonic() + _ttl_seconds(), deepcopy(value))


def _key_lock(namespace: str, key: Hashable) -> Lock:
    cache_key = (namespace, key)
    with _CACHE_LOCK:
        lock = _KEY_LOCKS.get(cache_key)
        if lock is None:
            lock = Lock()
            _KEY_LOCKS[cache_key] = lock
        return lock


def _positive_cached_call(
    namespace: str,
    key: Hashable,
    loader: Callable[[], Any],
    cacheable: Callable[[Any], bool],
) -> Any:
    cached = _cache_get(namespace, key)
    if cached is not None:
        return cached
    lock = _key_lock(namespace, key)
    with lock:
        cached = _cache_get(namespace, key)
        if cached is not None:
            return cached
        value = loader()
        if cacheable(value):
            _cache_put(namespace, key, value)
        return value


def clear_interactive_runtime_caches() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _KEY_LOCKS.clear()


def install_interactive_runtime_optimizations(market_api: Any) -> bool:
    if getattr(market_api, _STATE_ATTR, False):
        return True

    prod = getattr(market_api, "prod", None)
    original_get_client = getattr(prod, "get_client", None)
    original_capability = getattr(prod, "_runtime_capability", None)
    original_route = getattr(market_api, "_prop_route_artifact", None)
    original_resolver = getattr(prop_fitted_provider, "resolve_certified_artifact", None)
    if not callable(original_get_client) or not callable(original_capability) or not callable(original_route) or not callable(original_resolver):
        return False

    def thread_local_get_client() -> Any:
        client = getattr(_THREAD_LOCAL, "supabase_client", None)
        if client is None:
            client = original_get_client()
            _THREAD_LOCAL.supabase_client = client
        return client

    def cached_runtime_capability(capability_key: str) -> dict[str, Any]:
        key = str(capability_key or "").strip().upper()
        return _positive_cached_call(
            "runtime_capability",
            key,
            lambda: original_capability(capability_key),
            lambda value: isinstance(value, dict) and value.get("capability_status") == "AVAILABLE",
        )

    def cached_prop_route_artifact(sport: str, stat_type: str) -> dict[str, Any]:
        key = (str(sport or "").strip().upper(), str(stat_type or "").strip().upper())
        return _positive_cached_call(
            "prop_route_artifact",
            key,
            lambda: original_route(sport, stat_type),
            lambda value: (
                isinstance(value, dict)
                and value.get("ok") is True
                and value.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_READY"
            ),
        )

    def cached_resolve_certified_artifact(
        client: Any,
        *,
        sport: str,
        stat_type: str,
        feature_schema_version: str,
    ) -> Any:
        key = (
            str(os.getenv("SUPABASE_URL") or "").strip(),
            str(sport or "").strip().upper(),
            str(stat_type or "").strip().upper(),
            str(feature_schema_version or "").strip().upper(),
        )
        return _positive_cached_call(
            "certified_artifact",
            key,
            lambda: original_resolver(
                client,
                sport=sport,
                stat_type=stat_type,
                feature_schema_version=feature_schema_version,
            ),
            lambda value: value is not None,
        )

    prod.get_client = thread_local_get_client
    base_api = getattr(prod, "base_api", None)
    if base_api is not None and getattr(base_api, "get_client", None) is original_get_client:
        base_api.get_client = thread_local_get_client
    prod._runtime_capability = cached_runtime_capability
    market_api._prop_route_artifact = cached_prop_route_artifact
    prop_fitted_provider.resolve_certified_artifact = cached_resolve_certified_artifact
    setattr(market_api, _STATE_ATTR, True)
    _LOG.warning(
        "WOW_V17_INTERACTIVE_REGISTRY_CACHE status=INSTALLED ttl_seconds=%s positive_only=true thread_local_db_client=true can_execute=false",
        _ttl_seconds(),
    )
    return True


__all__ = [
    "clear_interactive_runtime_caches",
    "install_interactive_runtime_optimizations",
]
