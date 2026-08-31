"""
gate_engine/runtime_capability_probe.py
WOW Runtime Capability Probe v1.1 (WOW-PATCH-2026-08-19-RUNTIME-PROVENANCE)

Server-side derivation of capability evidence via bounded OPERATIONAL health
checks — configuration presence alone is never sufficient.  Routes must NEVER
forward caller-supplied capability_evidence into the provenance builder.

Fail-closed: a capability with no registered probe, or whose probe fails,
times out, or raises, yields NO evidence (→ unverified).  Probes never
fabricate evidence.

Probes are bounded (short timeouts) and success-cached for a short TTL so
governed routes do not hammer external backends; failures are NOT cached, so
a recovering backend is re-checked immediately and an unavailable backend is
re-verified on every run (fail-closed freshness).
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

_SERVICE = "legacy_platform_PRODUCTION_SERVICE"

_PROBE_TIMEOUT_SECONDS = 4
_SUCCESS_CACHE_TTL_SECONDS = 120

# capability -> (expires_at_epoch, evidence)  — successes only
_success_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _verified(detail: str) -> dict[str, Any]:
    return {"status": "VERIFIED", "verification_source": _SERVICE, "detail": detail}


def _probe_engine_health() -> dict[str, Any] | None:
    # In-process probe: this code executes inside the configured Flask
    # scoring service itself, so worker liveness is directly attested.
    return _verified("in-process flask worker alive")


def _probe_odds_gateway() -> dict[str, Any] | None:
    """
    Live reachability + authentication check against the Odds API using the
    configured key.  GET /v4/sports is a quota-free listing endpoint; a 200
    proves the gateway is up AND the key authenticates.  Anything else
    (401/429/5xx, timeout, DNS failure) yields no evidence.
    """
    import requests  # local import — never module-level in app-adjacent code

    for key_name in ("ODDS_API_KEY_100K", "ODDS_API_PAID_KEY", "ODDS_API_FREE_KEY"):
        api_key = (os.environ.get(key_name) or "").strip()
        if not api_key:
            continue
        try:
            resp = requests.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": api_key},
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except Exception:
            continue
        if resp.status_code == 200:
            return _verified(f"odds api reachable + authenticated ({key_name})")
    return None


def _probe_database() -> dict[str, Any] | None:
    """Live connectivity check: connect + SELECT 1 with a bounded timeout."""
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=_PROBE_TIMEOUT_SECONDS)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                if cur.fetchone() is None:
                    return None
        finally:
            conn.close()
    except Exception:
        return None
    return _verified("database connect + SELECT 1 ok")


_PROBE_REGISTRY: dict[str, Callable[[], dict[str, Any] | None]] = {
    "engine_health": _probe_engine_health,
    "odds_gateway":  _probe_odds_gateway,
    "database":      _probe_database,
}


def _run_probe(name: str) -> dict[str, Any] | None:
    now = time.monotonic()
    cached = _success_cache.get(name)
    if cached is not None and cached[0] > now:
        return cached[1]
    probe = _PROBE_REGISTRY.get(name)
    if probe is None:
        return None
    try:
        result = probe()
    except Exception:
        result = None
    if isinstance(result, dict):
        _success_cache[name] = (now + _SUCCESS_CACHE_TTL_SECONDS, result)
        return result
    _success_cache.pop(name, None)   # never cache failures
    return None


def probe_required_capabilities(required: Any) -> dict[str, dict[str, Any]]:
    """
    Return server-derived evidence for each required capability whose bounded
    operational probe succeeds.  Unknown capabilities and failed/raising/
    timed-out probes are simply absent from the result (fail-closed).
    """
    evidence: dict[str, dict[str, Any]] = {}
    if not isinstance(required, (list, tuple, set, frozenset)):
        return evidence
    for cap in required:
        result = _run_probe(str(cap).strip())
        if isinstance(result, dict):
            evidence[str(cap).strip()] = result
    return evidence
