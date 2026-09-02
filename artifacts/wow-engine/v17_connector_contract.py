"""Shared fail-closed intake contract for V17 external evidence connectors."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ConnectorPolicy:
    source_identity: str
    access_licensing_classification: str
    freshness_limit_seconds: int
    allowed_model_fields: tuple[str, ...] = ()
    allowed_evidence_only_fields: tuple[str, ...] = ()
    fallback_sources: tuple[str, ...] = ()
    fail_closed_behavior: str = "REJECT_AND_RETAIN_AUDIT"

    def __post_init__(self) -> None:
        if self.freshness_limit_seconds <= 0:
            raise ValueError("FRESHNESS_LIMIT_INVALID")


def immutable_evidence_snapshot(
    *, policy: ConnectorPolicy, payload: Any, request_timestamp: datetime,
    source_published_timestamp: datetime | None, event_id: str | None = None,
    player_id: str | None = None, completeness_score: float = 1.0,
) -> dict:
    if request_timestamp.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED:request_timestamp")
    if source_published_timestamp is not None and source_published_timestamp.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED:source_published_timestamp")
    if not 0 <= completeness_score <= 1:
        raise ValueError("COMPLETENESS_OUT_OF_RANGE")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    keys = sorted(payload.keys()) if isinstance(payload, dict) else [type(payload).__name__]
    schema_fingerprint = hashlib.sha256("\n".join(keys).encode()).hexdigest()
    return {
        **asdict(policy),
        "request_timestamp": request_timestamp.astimezone(timezone.utc).isoformat(),
        "source_published_timestamp": None if source_published_timestamp is None else source_published_timestamp.astimezone(timezone.utc).isoformat(),
        "event_id": event_id,
        "player_id": player_id,
        "schema_version": "v17.1",
        "schema_fingerprint": schema_fingerprint,
        "completeness_score": completeness_score,
        "raw_payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "immutable_raw_snapshot": True,
        "model_authoritative": bool(policy.allowed_model_fields),
        "can_execute": False,
        "raw_payload": payload,
    }
