"""Write-once research capture of real pre-inference features; not certification.

Caller must be trusted evidence hydration before model inference. This is an
offline research utility until independently reviewed production integration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class CaptureError(ValueError):
    pass


def utc_time(value):
    if not isinstance(value, str):
        raise CaptureError("TIMESTAMP_REQUIRED")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError("TIMESTAMP_INVALID") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise CaptureError("TIMESTAMP_NOT_AWARE")
    return dt.astimezone(timezone.utc)


def digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def build_capture(event: Mapping[str, Any], *, observed_now: datetime | None = None):
    """Use actual capture clock, not retrospective event-as-of timestamps.

    Source identities and hashes require independent later verification against
    the original provider archive. A receipt alone cannot certify them.
    """
    now = observed_now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise CaptureError("CAPTURE_CLOCK_NOT_AWARE")
    now = now.astimezone(timezone.utc)
    if event.get("sport") not in ("MLB", "NFL", "NBA", "NHL", "WNBA", "NCAAF", "NCAAB"):
        raise CaptureError("SPORT_UNSUPPORTED")
    for key in ("official_event_id", "feature_schema_version", "model_family"):
        if not isinstance(event.get(key), str) or not event[key].strip():
            raise CaptureError("CANONICAL_IDENTITY_INCOMPLETE")
    kickoff = utc_time(event.get("event_start_utc"))
    if now >= kickoff:
        raise CaptureError("PREGAME_CAPTURE_CLOSED")
    if event.get("event_status") != "SCHEDULED":
        raise CaptureError("EVENT_NOT_VERIFIED_SCHEDULED")
    if event.get("historical_reconstruction") is not False:
        raise CaptureError("RETROSPECTIVE_CAPTURE_FORBIDDEN")
    features = event.get("features")
    if not isinstance(features, dict) or not features:
        raise CaptureError("FEATURE_VECTOR_EMPTY")
    if any(not isinstance(k, str) or not k or isinstance(v, bool) or
           not isinstance(v, (float, int)) or not isfinite(v)
           for k, v in features.items()):
        raise CaptureError("FEATURE_VECTOR_INVALID")
    evidence = event.get("source_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise CaptureError("SOURCE_EVIDENCE_MISSING")
    for source in evidence:
        if not isinstance(source, dict):
            raise CaptureError("SOURCE_EVIDENCE_INVALID")
        for field in ("provider", "source_ref", "payload_sha256"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise CaptureError("SOURCE_IDENTITY_INCOMPLETE")
        if (len(source["payload_sha256"]) != 64 or
            any(c not in "0123456789abcdef" for c in source["payload_sha256"].lower())):
            raise CaptureError("SOURCE_HASH_INVALID")
        seen = utc_time(source.get("observed_at_utc"))
        fetched = utc_time(source.get("retrieved_at_utc"))
        if not seen <= fetched <= now:
            raise CaptureError("SOURCE_CHRONOLOGY_INVALID")
    archive = {
        "status": "CAPTURED_REQUIRES_INDEPENDENT_SOURCE_VERIFICATION",
        "sport": event["sport"], "official_event_id": event["official_event_id"],
        "event_start_utc": kickoff.isoformat(),
        "feature_schema_version": event["feature_schema_version"],
        "model_family": event["model_family"],
        "features": features, "feature_snapshot_sha256": digest(features),
        "captured_at_utc": now.isoformat(), "source_evidence": evidence,
        "historical_reconstruction": False, "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
    archive["capture_envelope_sha256"] = digest(archive)
    return archive


def write_once(path: Path, envelope: Mapping[str, Any]):
    """Create-only local audit file; NOT immutable trusted production storage."""
    if path.suffix != ".json" or not path.parent.is_dir():
        raise CaptureError("INVALID_LOCAL_CAPTURE_DESTINATION")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(envelope, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return {"status": "LOCAL_CAPTURE_CREATED", "sha256": digest(envelope), "can_execute": False}
