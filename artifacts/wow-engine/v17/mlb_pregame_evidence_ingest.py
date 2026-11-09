"""Governed ingest for MLB event-specific prelock evidence.

Writes one immutable prelock snapshot into
``wow_mlb_event_pregame_feature_rows`` for an exact fitted MLB version. This is
an evidence producer, never a model, calibrator or publication path.

Governed properties:

* both payload hashes are **derived from the payloads being persisted**, so a
  caller cannot supply a digest that does not describe the row it writes;
* the prelock window is enforced on write: a snapshot whose features freeze at
  or after the official start, or that is written after first pitch, is refused;
* a non-``SCHEDULED`` event is refused, so a postponed or completed game cannot
  acquire pregame evidence after the fact;
* the previous current row is superseded (``is_current=false``) rather than
  rewritten, and payload columns are never updated — replay is idempotent;
* no probability, calibrated bound, rank flag, verification flag or
  certification is written. The scoring boundary still requires a
  ``CERTIFIED``/``PASS`` receipt in
  ``wow_team_event_specialist_certifications`` for this exact event and version
  before any verification flag is trusted.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

TABLE = "wow_mlb_event_pregame_feature_rows"
SPORT = "MLB"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False

PERSISTED = "PERSISTED"
ALREADY_CURRENT = "ALREADY_CURRENT"
REFUSED = "REFUSED"
READ_FAILED = "READ_FAILED"
WRITE_FAILED = "WRITE_FAILED"

# Identity columns the reader filters on, in the order the writer checks them.
_IDENTITY_FIELDS = (
    "model_family",
    "model_artifact_version",
    "feature_schema_version",
)

# The row carries evidence only. This tuple exists so a test can prove the
# writer never grows a probability, approval or verification column.
_ROW_FIELDS = frozenset(
    {
        "sport",
        "model_family",
        "model_artifact_version",
        "feature_schema_version",
        "model_artifact_checksum",
        "official_event_id",
        "event_start_time",
        "event_status",
        "source_snapshot_id",
        "source_snapshot_timestamp",
        "source_payload_sha256",
        "feature_snapshot_id",
        "feature_snapshot_timestamp",
        "feature_snapshot_sha256",
        "is_current",
    }
)


def canonical_sha256(payload: Any) -> str:
    """Deterministic digest of a payload, independent of key order."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(value: Any) -> bool:
    token = _text(value).lower()
    return len(token) == 64 and all(char in "0123456789abcdef" for char in token)


def _when(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def build_pregame_snapshot(
    *,
    event: Mapping[str, Any],
    source_payload: Mapping[str, Any],
    features: Mapping[str, Any],
    source_snapshot_id: Any,
    source_snapshot_timestamp: Any,
    feature_snapshot_id: Any,
    feature_snapshot_timestamp: Any,
    model_family: Any,
    model_artifact_version: Any,
    model_artifact_checksum: Any,
    feature_schema_version: Any,
    now: Any,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Build one exact prelock row, or return the typed reasons it cannot exist.

    ``now`` is the ingest decision time. It is passed in rather than read from
    the clock so a job's window check is reproducible and testable.
    """
    event = dict(event or {})
    blockers: list[str] = []

    for field, value in (
        ("model_family", model_family),
        ("model_artifact_version", model_artifact_version),
        ("feature_schema_version", feature_schema_version),
    ):
        if not _text(value):
            blockers.append(f"MLB_PREGAME_EVIDENCE_IDENTITY_INCOMPLETE:{field}")

    checksum = _text(model_artifact_checksum).lower()
    if not _sha256(checksum):
        blockers.append("MLB_PREGAME_EVIDENCE_ARTIFACT_CHECKSUM_INVALID")

    official_event_id = _text(event.get("official_event_id"))
    if not official_event_id:
        blockers.append("MLB_PREGAME_EVIDENCE_IDENTITY_INCOMPLETE:official_event_id")

    status = _text(event.get("event_status")).upper()
    if status != "SCHEDULED":
        blockers.append(
            f"MLB_PREGAME_EVIDENCE_EVENT_NOT_SCHEDULED:{status or 'UNKNOWN'}"
        )

    if not isinstance(source_payload, Mapping) or not source_payload:
        blockers.append("MLB_PREGAME_EVIDENCE_PAYLOAD_NOT_OBJECT:source")
    if not isinstance(features, Mapping) or not features:
        blockers.append("MLB_PREGAME_EVIDENCE_PAYLOAD_NOT_OBJECT:features")
    if not _text(source_snapshot_id):
        blockers.append("MLB_PREGAME_EVIDENCE_IDENTITY_INCOMPLETE:source_snapshot_id")
    if not _text(feature_snapshot_id):
        blockers.append("MLB_PREGAME_EVIDENCE_IDENTITY_INCOMPLETE:feature_snapshot_id")

    start_at = _when(event.get("event_start_time"))
    source_at = _when(source_snapshot_timestamp)
    feature_at = _when(feature_snapshot_timestamp)
    decision_at = _when(now)
    for field, parsed in (
        ("event_start_time", start_at),
        ("source_snapshot_timestamp", source_at),
        ("feature_snapshot_timestamp", feature_at),
        ("now", decision_at),
    ):
        if parsed is None:
            blockers.append(f"MLB_PREGAME_EVIDENCE_TIMESTAMP_INVALID:{field}")

    if blockers:
        return None, tuple(dict.fromkeys(blockers))

    assert start_at is not None and source_at is not None
    assert feature_at is not None and decision_at is not None

    if not (source_at <= feature_at < start_at):
        blockers.append("MLB_PREGAME_EVIDENCE_PRELOCK_ORDER_INVALID")
    if decision_at >= start_at:
        blockers.append("MLB_PREGAME_EVIDENCE_WINDOW_CLOSED")
    if blockers:
        return None, tuple(dict.fromkeys(blockers))

    try:
        source_digest = canonical_sha256(source_payload)
        feature_digest = canonical_sha256(features)
    except (TypeError, ValueError):
        return None, ("MLB_PREGAME_EVIDENCE_PAYLOAD_NON_CANONICAL",)

    row = {
        "sport": SPORT,
        "model_family": _text(model_family),
        "model_artifact_version": _text(model_artifact_version),
        "feature_schema_version": _text(feature_schema_version),
        "model_artifact_checksum": checksum,
        "official_event_id": official_event_id,
        "event_start_time": start_at.isoformat(),
        "event_status": "SCHEDULED",
        "source_snapshot_id": _text(source_snapshot_id),
        "source_snapshot_timestamp": source_at.isoformat(),
        "source_payload_sha256": source_digest,
        "feature_snapshot_id": _text(feature_snapshot_id),
        "feature_snapshot_timestamp": feature_at.isoformat(),
        "feature_snapshot_sha256": feature_digest,
        "is_current": True,
    }
    return row, ()


def _receipt(
    status: str,
    *,
    row: Mapping[str, Any] | None = None,
    blockers: tuple[str, ...] = (),
    superseded_row_id: Any = None,
) -> dict[str, Any]:
    row = row or {}
    return {
        "status": status,
        "sport": SPORT,
        "official_event_id": row.get("official_event_id"),
        "model_artifact_version": row.get("model_artifact_version"),
        "feature_schema_version": row.get("feature_schema_version"),
        "source_snapshot_id": row.get("source_snapshot_id"),
        "feature_snapshot_id": row.get("feature_snapshot_id"),
        "source_payload_sha256": row.get("source_payload_sha256"),
        "feature_snapshot_sha256": row.get("feature_snapshot_sha256"),
        "superseded_row_id": superseded_row_id,
        "blockers": list(blockers),
        "probability_written": False,
        "verification_flag_written": False,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _current_row(db: Any, row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    query = db.table(TABLE).select("*")
    query = query.eq("official_event_id", row["official_event_id"])
    for field in _IDENTITY_FIELDS:
        query = query.eq(field, row[field])
    result = query.eq("is_current", "true").limit(1).execute()
    rows = list(getattr(result, "data", None) or [])
    return dict(rows[0]) if rows else None


def persist_pregame_snapshot(
    db: Any, row: Mapping[str, Any]
) -> dict[str, Any]:
    """Supersede the prior current row, then insert this one. Never rewrite."""
    if db is None:
        return _receipt(
            REFUSED, row=row, blockers=("MLB_PREGAME_EVIDENCE_DB_UNAVAILABLE",)
        )

    try:
        current = _current_row(db, row)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not writable
        return _receipt(
            READ_FAILED,
            row=row,
            blockers=(f"MLB_PREGAME_EVIDENCE_READ_FAILED:{type(exc).__name__}",),
        )

    if current is not None:
        same_snapshot = (
            _text(current.get("source_payload_sha256")) == row["source_payload_sha256"]
            and _text(current.get("feature_snapshot_sha256")) == row["feature_snapshot_sha256"]
            and _text(current.get("source_snapshot_id")) == row["source_snapshot_id"]
            and _text(current.get("feature_snapshot_id")) == row["feature_snapshot_id"]
        )
        if same_snapshot:
            return _receipt(ALREADY_CURRENT, row=row)
        prior_id = current.get("id")
        try:
            db.table(TABLE).update({"is_current": False}).eq("id", prior_id).execute()
        except Exception as exc:  # noqa: BLE001 - partial supersede must not insert
            return _receipt(
                WRITE_FAILED,
                row=row,
                blockers=(
                    f"MLB_PREGAME_EVIDENCE_SUPERSEDE_FAILED:{type(exc).__name__}",
                ),
            )
    else:
        prior_id = None

    try:
        db.table(TABLE).insert(dict(row)).execute()
    except Exception as exc:  # noqa: BLE001 - a failed write is a typed receipt
        return _receipt(
            WRITE_FAILED,
            row=row,
            blockers=(f"MLB_PREGAME_EVIDENCE_WRITE_FAILED:{type(exc).__name__}",),
            superseded_row_id=prior_id,
        )
    return _receipt(PERSISTED, row=row, superseded_row_id=prior_id)


def ingest_pregame_snapshot(
    db: Any,
    *,
    event: Mapping[str, Any],
    source_payload: Mapping[str, Any],
    features: Mapping[str, Any],
    source_snapshot_id: Any,
    source_snapshot_timestamp: Any,
    feature_snapshot_id: Any,
    feature_snapshot_timestamp: Any,
    model_family: Any,
    model_artifact_version: Any,
    model_artifact_checksum: Any,
    feature_schema_version: Any,
    now: Any,
) -> dict[str, Any]:
    """Build and persist one governed prelock snapshot for one exact event."""
    row, blockers = build_pregame_snapshot(
        event=event,
        source_payload=source_payload,
        features=features,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_timestamp=source_snapshot_timestamp,
        feature_snapshot_id=feature_snapshot_id,
        feature_snapshot_timestamp=feature_snapshot_timestamp,
        model_family=model_family,
        model_artifact_version=model_artifact_version,
        model_artifact_checksum=model_artifact_checksum,
        feature_schema_version=feature_schema_version,
        now=now,
    )
    if row is None:
        return _receipt(
            REFUSED,
            row={
                "official_event_id": _text((event or {}).get("official_event_id")),
                "model_artifact_version": _text(model_artifact_version),
                "feature_schema_version": _text(feature_schema_version),
            },
            blockers=blockers,
        )
    return persist_pregame_snapshot(db, row)


__all__ = [
    "ALREADY_CURRENT",
    "CAN_EXECUTE",
    "PERSISTED",
    "READ_FAILED",
    "REFUSED",
    "SPORT",
    "TABLE",
    "TERMINAL_AUTHORITY",
    "WRITE_FAILED",
    "build_pregame_snapshot",
    "canonical_sha256",
    "ingest_pregame_snapshot",
    "persist_pregame_snapshot",
]
