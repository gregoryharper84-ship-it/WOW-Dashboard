"""Fail-closed read-only cross-sport source inventory integrity check (#1689).

Caller MUST authenticate upstream source and expected SHA256 separately.
This module does not certify a model, publish, deploy, or place wagers.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

_ALLOWED = frozenset({"SCHEDULED", "PRE_GAME", "IN_PROGRESS", "FINAL", "POSTPONED", "CANCELLED", "CANCELED"})
_HEX256 = re.compile(r"^[0-9a-f]{64}$")
MAX_BYTES = 2_000_000
MAX_EVENTS = 2000

class InventoryIntegrityError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

@dataclass(frozen=True)
class VerifiedFixture:
    sport: str
    official_event_id: str
    start_utc: datetime
    status: str

@dataclass(frozen=True)
class VerifiedInventory:
    source_id: str
    payload_sha256: str
    fetched_at: datetime
    fixtures: tuple[VerifiedFixture, ...]

def _timestamp(value):
    if not isinstance(value, str):
        raise InventoryIntegrityError("SOURCE_EVENT_TIMESTAMP_INVALID")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryIntegrityError("SOURCE_EVENT_TIMESTAMP_INVALID") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise InventoryIntegrityError("SOURCE_EVENT_TIMEZONE_MISSING")
    return dt.astimezone(timezone.utc)

def _unique_json_object(pairs):
    """Reject duplicate keys at every object depth, including discarded metadata."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InventoryIntegrityError("SOURCE_PAYLOAD_JSON_DUPLICATE_KEY")
        obj[key] = value
    return obj


def _reject_json_constant(_value):
    """Python otherwise accepts NaN/Infinity, which are not JSON values."""
    raise InventoryIntegrityError("SOURCE_PAYLOAD_JSON_NONFINITE_NUMBER")


def verify_inventory_bytes(payload: bytes, *, expected_sha256: str, source_id: str,
                           fetched_at: datetime, sport: str) -> VerifiedInventory:
    """Check original bytes against independently authenticated expected digest.

    A matching digest alone is NOT provider authenticity or model certification.
    """
    if not isinstance(payload, bytes) or not (0 < len(payload) <= MAX_BYTES):
        raise InventoryIntegrityError("SOURCE_PAYLOAD_SIZE_INVALID")
    if not isinstance(expected_sha256, str) or not _HEX256.fullmatch(expected_sha256):
        raise InventoryIntegrityError("SOURCE_EXPECTED_DIGEST_INVALID")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise InventoryIntegrityError("SOURCE_PAYLOAD_DIGEST_MISMATCH")
    if not isinstance(source_id, str) or not source_id.strip():
        raise InventoryIntegrityError("SOURCE_IDENTITY_MISSING")
    if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise InventoryIntegrityError("SOURCE_FETCH_TIMEZONE_MISSING")
    if not isinstance(sport, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,15}", sport):
        raise InventoryIntegrityError("SOURCE_SPORT_INVALID")
    try:
        obj = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object,
                         parse_constant=_reject_json_constant)
    except InventoryIntegrityError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InventoryIntegrityError("SOURCE_PAYLOAD_JSON_INVALID") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("events"), list):
        raise InventoryIntegrityError("SOURCE_EVENT_LIST_INVALID")
    events = obj["events"]
    if len(events) > MAX_EVENTS:
        raise InventoryIntegrityError("SOURCE_EVENT_COUNT_LIMIT")
    out, seen = [], set()
    for row in events:
        if not isinstance(row, dict):
            raise InventoryIntegrityError("SOURCE_EVENT_ROW_INVALID")
        event_id = row.get("official_event_id")
        if (not isinstance(event_id, str) or not event_id.strip()
                or event_id != event_id.strip() or not event_id.isprintable()
                or len(event_id) > 180):
            raise InventoryIntegrityError("SOURCE_CANONICAL_EVENT_ID_INVALID")
        if event_id in seen:
            raise InventoryIntegrityError("SOURCE_CANONICAL_EVENT_ID_DUPLICATE")
        seen.add(event_id)
        if row.get("sport") != sport:
            raise InventoryIntegrityError("SOURCE_CROSS_SPORT_EVENT")
        status = row.get("status")
        if not isinstance(status, str) or status not in _ALLOWED:
            raise InventoryIntegrityError("SOURCE_EVENT_STATUS_UNKNOWN")
        out.append(VerifiedFixture(sport, event_id, _timestamp(row.get("start_utc")), status))
    return VerifiedInventory(source_id, digest, fetched_at.astimezone(timezone.utc), tuple(out))
