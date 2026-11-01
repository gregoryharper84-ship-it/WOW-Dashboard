"""Immutable, research-only persistence of source-validated NBA POINTS evidence.

This adapter consumes build_nba_points_snapshot output; it never supplies absent
line/role/player evidence, fits a model, calibrates or publishes probabilities.
The caller must use an authenticated server-side database client. Only a true
pregame, canonical-validated bridge receipt may be written.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

CAN_EXECUTE = False
TABLE = "wow_prop_evidence_snapshots"
FIELDS = (
    "source_snapshot_id", "captured_at", "event_id", "event_start_time",
    "sport", "player", "stat_type", "line", "game_log", "box_score_log",
    "role_status", "role_timestamp", "opportunity_ledger", "source_timestamps",
    "hydration_status", "blockers", "evidence_version", "can_execute",
)
OPTIONAL_FIELDS = ("opponent_context", "detailed_evidence", "detailed_evidence_fingerprint")


class NBAPointsSnapshotPersistenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _equal(a: Any, b: Any, field: str) -> bool:
    if field in {"captured_at", "event_start_time", "role_timestamp"}:
        return _timestamp(a) is not None and _timestamp(a) == _timestamp(b)
    if field == "line":
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return False
    return a == b


def _read(db: Any, snapshot_id: str) -> dict[str, Any] | None:
    try:
        rows = (db.table(TABLE).select("*").eq("source_snapshot_id", snapshot_id)
                .limit(1).execute().data or [])
    except Exception as exc:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_READ_FAILED") from exc
    if len(rows) > 1:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_IDENTITY_AMBIGUOUS")
    return dict(rows[0]) if rows else None


def _matching(existing: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    return all(_equal(existing.get(k), value, k) for k, value in candidate.items())


def persist_validated_nba_points_snapshot(
    db: Any, receipt: Mapping[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    """Create once and read back; identical retries never update historical data."""
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_NOW_NAIVE")
    now = now.astimezone(timezone.utc)
    if (receipt.get("status") != "SOURCE_VALIDATED_SNAPSHOT_NOT_PERSISTED"
            or receipt.get("write_performed") is not False
            or receipt.get("can_execute") is not False
            or receipt.get("probability_publishable") is not False
            or receipt.get("rank_eligible") is not False):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_VALIDATED_RECEIPT_REQUIRED")
    original = receipt.get("snapshot")
    if not isinstance(original, Mapping):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_MISSING")
    if any(key not in original for key in FIELDS):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_FIELDS_MISSING")
    snapshot = {key: original[key] for key in FIELDS + OPTIONAL_FIELDS if key in original}
    snapshot_id = str(snapshot["source_snapshot_id"])
    try:
        UUID(snapshot_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_ID_INVALID") from exc
    if (snapshot_id != str(receipt.get("source_snapshot_id"))
            or snapshot.get("sport") != "NBA" or snapshot.get("stat_type") != "POINTS"
            or not str(snapshot.get("event_id") or "").strip()
            or not str(snapshot.get("player") or "").strip()
            or snapshot.get("hydration_status") != "PASS"
            or snapshot.get("blockers") != [] or snapshot.get("can_execute") is not False):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_NOT_VALIDATED")
    identity = receipt.get("source_identity")
    if (not isinstance(identity, Mapping)
            or identity.get("canonical_event_id") != snapshot["event_id"]
            or not str(identity.get("player_id") or "").strip()
            or not str(identity.get("board_line_provider") or "").strip()):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_MISMATCH")
    if receipt.get("official_roster_verified_for_snapshot") is not True:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ROSTER_PROOF_REQUIRED")
    role = snapshot.get("role_status")
    offer = snapshot.get("opportunity_ledger")
    proof = role.get("source_identity") if isinstance(role, Mapping) else None
    if not isinstance(proof, Mapping) or not isinstance(offer, Mapping):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ROSTER_PROOF_REQUIRED")
    if (not re.fullmatch(r"[0-9]{10}", str(proof.get("nba_game_id") or ""))
            or not re.fullmatch(r"[1-9][0-9]{0,9}", str(proof.get("nba_person_id") or ""))
            or not re.fullmatch(r"[A-Z]{2,4}", str(proof.get("team_tricode") or ""))
            or proof.get("espn_event_id") != snapshot["event_id"]
            or str(role.get("official_game_id")) != str(proof.get("nba_game_id"))
            or str(role.get("player_id")) != str(proof.get("nba_person_id"))
            or identity.get("nba_game_id") != proof.get("nba_game_id")
            or str(identity.get("nba_person_id")) != str(proof.get("nba_person_id"))
            or not isinstance(proof.get("source_names"), list)
            or not {"ESPN_GAME_SUMMARY", "NBA_CDN_LIVE_BOXSCORE"}.issubset(
                {x for x in proof["source_names"] if isinstance(x, str)}
            )):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ROSTER_PROOF_REQUIRED")
    if (offer.get("period") != "FULL_GAME"
            or offer.get("threshold_type") != "EXACT_NUMERIC"):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED")
    threshold = offer.get("literal_threshold")
    try:
        offered = Decimal(str(threshold))
        expected = Decimal(str(snapshot["line"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED") from exc
    if not offered.is_finite() or not expected.is_finite() or offered != expected:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED")
    try:
        line = float(snapshot["line"])
    except (TypeError, ValueError) as exc:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_EXACT_LINE_INVALID") from exc
    if not isfinite(line) or line <= 0:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_EXACT_LINE_INVALID")
    start, captured, role_time = (_timestamp(snapshot[key]) for key in
                                  ("event_start_time", "captured_at", "role_timestamp"))
    board_time = _timestamp(identity.get("board_captured_at"))
    roster_time = _timestamp(identity.get("roster_captured_at"))
    if (start is None or captured is None or role_time is None or board_time is None
            or roster_time is None or not (role_time <= captured < now < start)
            or board_time > captured or roster_time > captured):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_NO_LONGER_PREGAME")
    if (_timestamp(proof.get("event_start_time")) != start
            or any((_timestamp(proof.get(k)) is None
                    or _timestamp(proof.get(k)) > captured
                    or _timestamp(proof.get(k)) >= start)
                   for k in ("espn_captured_at", "nba_captured_at"))):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ROSTER_PROOF_REQUIRED")
    source_times = snapshot.get("source_timestamps")
    if (not isinstance(source_times, dict) or not source_times
            or any((_timestamp(ts) is None or _timestamp(ts) > captured) for ts in source_times.values())):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_TIMESTAMP_INVALID")
    existing = _read(db, snapshot_id)
    if existing is not None:
        if not _matching(existing, snapshot):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_IMMUTABLE_SNAPSHOT_CONFLICT")
        return {"status": "ALREADY_PERSISTED_RESEARCH_ONLY", "source_snapshot_id": snapshot_id,
                "write_performed": False, "probability_publishable": False, "can_execute": False}
    try:
        db.table(TABLE).insert(snapshot).execute()
    except Exception:
        # A simultaneous first writer can race us. Read back; never overwrite.
        existing = _read(db, snapshot_id)
        if existing is None or not _matching(existing, snapshot):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_INSERT_FAILED")
        return {"status": "ALREADY_PERSISTED_RESEARCH_ONLY", "source_snapshot_id": snapshot_id,
                "write_performed": False, "probability_publishable": False, "can_execute": False}
    persisted = _read(db, snapshot_id)
    if persisted is None or not _matching(persisted, snapshot):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_READBACK_MISMATCH")
    return {"status": "PERSISTED_RESEARCH_ONLY", "source_snapshot_id": snapshot_id,
            "write_performed": True, "probability_publishable": False, "can_execute": False}


def capture_nba_points_snapshot(db: Any, *, now: datetime | None = None, **source_inputs: Any) -> dict[str, Any]:
    """Use existing exact-source V17 validation, then persist its frozen result."""
    from v17.nba_points_snapshot_bridge import build_nba_points_snapshot
    result = build_nba_points_snapshot(now=now, **source_inputs)
    return persist_validated_nba_points_snapshot(db, result, now=now)


__all__ = ["NBAPointsSnapshotPersistenceError", "persist_validated_nba_points_snapshot",
           "capture_nba_points_snapshot", "CAN_EXECUTE"]
