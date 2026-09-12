"""Persist raw research-provider snapshots before candidate identity linkage."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

try:
    from v17.scout_brain_persistence import database_url
    from v17.research_source_adapters import ResearchFetchResult
except ModuleNotFoundError:
    from scout_brain_persistence import database_url
    from research_source_adapters import ResearchFetchResult


def _payload_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def snapshot_id_for(result: ResearchFetchResult) -> str:
    payload_hash = _payload_hash(result.data)
    raw = "|".join((result.provider, result.sport_key, result.capability, payload_hash))
    return "src_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def persist_source_result(result: ResearchFetchResult) -> dict[str, Any]:
    sid = snapshot_id_for(result)
    payload_hash = _payload_hash(result.data)
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into wow_scout.source_snapshots
                (snapshot_id,provider,sport_key,capability,source_class,observed_at,source_status,source_code,
                 source_http_status,payload,payload_hash,prediction_authority,can_execute)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,false,false)
                on conflict (snapshot_id) do update set
                  observed_at=excluded.observed_at,
                  source_status=excluded.source_status,
                  source_code=excluded.source_code,
                  source_http_status=excluded.source_http_status,
                  payload=excluded.payload,
                  prediction_authority=false,
                  can_execute=false
                """,
                (
                    sid,
                    result.provider,
                    result.sport_key,
                    result.capability,
                    result.source_class,
                    result.observed_at,
                    "OK" if result.ok else "BLOCKED",
                    result.code,
                    result.status,
                    json.dumps(result.data, default=str) if result.data is not None else None,
                    payload_hash,
                ),
            )
        conn.commit()
    return {
        "snapshot_id": sid,
        "provider": result.provider,
        "sport_key": result.sport_key,
        "capability": result.capability,
        "source_status": "OK" if result.ok else "BLOCKED",
        "source_code": result.code,
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = ["snapshot_id_for", "persist_source_result"]
