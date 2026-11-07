"""Canonical NHL source-provenance identity.

A single official game appears in both clubs' schedule payloads, so the raw
payload hash is not a stable per-event identifier. V2 rows therefore use a
canonical event-envelope hash that is independent of which club supplied the
payload. The exact raw payload hash is retained separately in
``source_payload_commitment`` and remains auditable.

The envelope includes only official game identity, settled score, regular-season
game type, neutral-site flag, settled state, and the provenance version. It
never includes market, model, or caller-supplied data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

SOURCE_PROVENANCE_VERSION = "NHL_SOURCE_PROVENANCE_V2"
SOURCE_PAYLOAD_HASH_SCOPE = "PROVENANCE_ENVELOPE_V1"


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_start(value: Any) -> str:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return token
    if parsed.utcoffset() is None:
        return token
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_season(value: Any) -> str:
    token = str(value or "").strip()
    try:
        return str(int(token))
    except (TypeError, ValueError):
        return token


def provenance_envelope_hash(
    *,
    official_event_id: Any,
    season: Any,
    event_start_time: Any,
    home_team: Any,
    away_team: Any,
    home_score: Any,
    away_score: Any,
    game_type: Any,
    game_state: Any,
    neutral_site: Any,
    provenance_version: str = SOURCE_PROVENANCE_VERSION,
) -> str:
    payload = {
        "official_event_id": str(official_event_id or "").strip(),
        "season": _canonical_season(season),
        "event_start_time": _canonical_start(event_start_time),
        "home_team": str(home_team or "").strip().upper(),
        "away_team": str(away_team or "").strip().upper(),
        "home_score": int(home_score),
        "away_score": int(away_score),
        "game_type": int(game_type),
        "game_state": str(game_state or "").strip().upper(),
        "neutral_site": bool(neutral_site),
        "provenance_version": str(provenance_version or "").strip(),
        "payload_hash_scope": SOURCE_PAYLOAD_HASH_SCOPE,
    }
    return canonical_sha256(payload)


__all__ = [
    "SOURCE_PAYLOAD_HASH_SCOPE",
    "SOURCE_PROVENANCE_VERSION",
    "canonical_sha256",
    "provenance_envelope_hash",
]
