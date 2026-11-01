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
from postgrest.exceptions import APIError as PostgrestAPIError

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
                .limit(2).execute().data or [])
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
    except Exception as exc:
        # Only PostgreSQL 23505 is an expected insert race. Authentication,
        # transport and schema failures are not a duplicate proof.
        if not isinstance(exc, PostgrestAPIError) or str(exc.code) != "23505":
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_INSERT_FAILED") from exc
        existing = _read(db, snapshot_id)
        if existing is None or not _matching(existing, snapshot):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_IMMUTABLE_SNAPSHOT_CONFLICT") from exc
        return {"status": "ALREADY_PERSISTED_RESEARCH_ONLY", "source_snapshot_id": snapshot_id,
                "write_performed": False, "probability_publishable": False, "can_execute": False}
    persisted = _read(db, snapshot_id)
    if persisted is None or not _matching(persisted, snapshot):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_READBACK_MISMATCH")
    return {"status": "PERSISTED_RESEARCH_ONLY", "source_snapshot_id": snapshot_id,
            "write_performed": True, "probability_publishable": False, "can_execute": False}


def capture_nba_points_snapshot(
    db: Any, *, espn_event_id: str, nba_game_id: str, player_name: str,
    collect_source_inputs: Any, clock: Any = None,
) -> dict[str, Any]:
    """Fetch independent official roster before freezing exact-board evidence.

    The trusted backend's collector must run after the official observations.
    A caller-provided verified_roster_receipt is not admissible on this path.
    No probability, market offer or role is invented from roster membership.
    """
    from httpx import get as official_http_get
    from v17.nba_points_pregame_roster import acquire_nba_points_current_roster
    from v17.nba_points_snapshot_bridge import build_nba_points_snapshot

    if not callable(collect_source_inputs):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_TRUSTED_SOURCE_COLLECTOR_REQUIRED")
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    if not callable(clock):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_CAPTURE_CLOCK_INVALID")
    official = acquire_nba_points_current_roster(
        espn_event_id=espn_event_id, nba_game_id=nba_game_id,
        player_name=player_name, http_get=official_http_get, clock=clock,
    )
    if official.get("source_acquisition") != {
        "provider_calls": 2, "transport": "READ_ONLY_FIXED_ORIGINS",
        "source_names": ["ESPN_GAME_SUMMARY", "NBA_CDN_LIVE_BOXSCORE"],
    }:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ACQUISITION_REQUIRED")
    try:
        source_inputs = collect_source_inputs(official)
    except Exception as exc:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_COLLECTION_FAILED") from exc
    if (not isinstance(source_inputs, Mapping)
        or any(k in source_inputs for k in (
            "verified_roster_receipt", "now", "clock", "resolve_identity",
            "validate_evidence", "snapshot_factory", "row_factory", "evidence_factory"
        ))):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_UNTRUSTED_SOURCE_OVERRIDE")
    if source_inputs.get("roster") != official.get("roster"):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_CONTRADICTION")
    target = source_inputs.get("target")
    if not isinstance(target, Mapping) or str(target.get("player") or "").strip() != player_name:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_CONTRADICTION")
    captured_now = clock()
    if not isinstance(captured_now, datetime) or captured_now.utcoffset() is None:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_CAPTURE_CLOCK_INVALID")
    receipt = build_nba_points_snapshot(
        verified_roster_receipt=official, now=captured_now, **source_inputs,
    )
    return persist_validated_nba_points_snapshot(db, receipt, now=captured_now)


__all__ = ["NBAPointsSnapshotPersistenceError", "persist_validated_nba_points_snapshot",
           "capture_nba_points_snapshot", "CAN_EXECUTE"]
