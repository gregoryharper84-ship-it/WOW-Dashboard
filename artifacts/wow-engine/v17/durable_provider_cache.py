"""Restart-persistent short-TTL cache for optional V17 provider evidence.

This is an operational quota-saving cache, not an immutable evidence ledger.
Only successful normalized research market evidence is eligible. Cached market
data never becomes sporting-probability authority and can_execute is false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from typing import Any, Hashable, Mapping

CAN_EXECUTE = False
TABLE = "wow_v17_provider_response_cache"


def enabled() -> bool:
    return os.environ.get(
        "WOW_V17_DURABLE_PROVIDER_CACHE_ENABLED", "true"
    ).strip().lower() == "true"


def ttl_seconds() -> int:
    try:
        value = int(os.environ.get("WOW_V17_DURABLE_PROVIDER_CACHE_TTL_SECONDS", "120"))
    except ValueError:
        value = 120
    # Never exceed the existing 15-minute market freshness contract.
    return max(0, min(value, 900))


def stable_cache_key(key: Hashable) -> str:
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _client() -> Any | None:
    if not enabled():
        return None
    url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )
    if not url or not key:
        return None
    try:
        from supabase import create_client
    except ImportError:
        return None
    return create_client(url, key)


def _meta_from_result(result: Any) -> dict[str, Any]:
    return {
        "ok": bool(getattr(result, "ok", False)),
        "provider": str(getattr(result, "provider", "") or ""),
        "capability": str(getattr(result, "capability", "") or ""),
        "status": getattr(result, "status", None),
        "code": getattr(result, "code", None),
        "observed_at": getattr(result, "observed_at", None),
        "endpoint": getattr(result, "endpoint", None),
        "schema_probe": getattr(result, "schema_probe", None),
        "rate_limit": getattr(result, "rate_limit", None),
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }


def load_market_evidence(
    key: Hashable,
    *,
    client: Any | None = None,
    now: datetime | None = None,
) -> Any | None:
    """Return one fresh cached MarketEvidenceResult, or None on miss/degradation."""
    if not enabled() or ttl_seconds() <= 0:
        return None
    db = client if client is not None else _client()
    if db is None:
        return None
    ts = (now or _utcnow()).astimezone(timezone.utc).isoformat()
    try:
        response = (
            db.table(TABLE)
            .select("payload,response_meta,expires_at")
            .eq("cache_key", stable_cache_key(key))
            .gt("expires_at", ts)
            .limit(1)
            .execute()
        )
    except Exception:  # cache failure must never break acquisition
        return None
    rows = list(getattr(response, "data", None) or [])
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        return None
    row = rows[0]
    meta = dict(row.get("response_meta") or {})
    if meta.get("ok") is not True:
        return None
    if (
        meta.get("prediction_authority") is not False
        or meta.get("exact_line_authority") is not False
        or meta.get("research_only") is not True
        or meta.get("can_execute") is not False
    ):
        return None

    from v17.market_evidence_sources import MarketEvidenceResult

    audit = dict(meta.get("request_audit") or {})
    audit["cache_origin"] = "DURABLE_CACHE"
    return MarketEvidenceResult(
        True,
        str(meta.get("provider") or "UNKNOWN"),
        str(meta.get("capability") or "unknown"),
        data=row.get("payload"),
        status=meta.get("status"),
        code=meta.get("code"),
        observed_at=meta.get("observed_at"),
        endpoint=meta.get("endpoint"),
        schema_probe=meta.get("schema_probe"),
        prediction_authority=False,
        exact_line_authority=False,
        research_only=True,
        rate_limit=meta.get("rate_limit"),
        request_audit=audit,
        can_execute=False,
    )


def store_market_evidence(
    key: Hashable,
    result: Any,
    *,
    provider: str,
    capability: str,
    sport_key: str,
    slate_date: str,
    client: Any | None = None,
    now: datetime | None = None,
) -> bool:
    """Best-effort cache write. Only successful non-authoritative rows are stored."""
    ttl = ttl_seconds()
    if not enabled() or ttl <= 0 or getattr(result, "ok", False) is not True:
        return False
    if (
        getattr(result, "prediction_authority", False) is not False
        or getattr(result, "exact_line_authority", False) is not False
        or getattr(result, "research_only", True) is not True
        or getattr(result, "can_execute", False) is not False
    ):
        return False
    db = client if client is not None else _client()
    if db is None:
        return False
    captured = (now or _utcnow()).astimezone(timezone.utc)
    expires = captured + timedelta(seconds=ttl)
    meta = _meta_from_result(result)
    meta["request_audit"] = dict(getattr(result, "request_audit", None) or {})
    row = {
        "cache_key": stable_cache_key(key),
        "provider": str(provider).upper(),
        "capability": str(capability),
        "sport_key": str(sport_key),
        "slate_date": str(slate_date),
        "payload": getattr(result, "data", None),
        "response_meta": meta,
        "captured_at": captured.isoformat(),
        "expires_at": expires.isoformat(),
        "can_execute": False,
    }
    try:
        db.table(TABLE).upsert(row, on_conflict="cache_key").execute()
    except Exception:  # cache failure must never break acquisition
        return False
    return True


__all__ = [
    "CAN_EXECUTE",
    "TABLE",
    "enabled",
    "load_market_evidence",
    "stable_cache_key",
    "store_market_evidence",
    "ttl_seconds",
]
