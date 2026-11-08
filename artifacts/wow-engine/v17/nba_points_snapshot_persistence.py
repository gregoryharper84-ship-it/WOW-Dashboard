"""Write-once persistence for authenticated NBA POINTS research snapshots.

The only public capture entrypoint accepts a persisted request-row identity.
This module refuses non-service-role clients, validates the sealed official-source
receipt, performs INSERT/DO NOTHING semantics, and requires canonical readback.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

TABLE = "wow_prop_evidence_snapshots"
TRANSPORT = "stdlib-urllib-https-no-redirect-v1"
_ALLOWED_HOST_PATHS = {
    "site.api.espn.com": {
        "/apis/site/v2/sports/basketball/nba/scoreboard",
        "/apis/site/v2/sports/basketball/nba/summary",
    },
    "stats.nba.com": {"/stats/playergamelog"},
}
_REQUIRED_SOURCE_NAMES = {
    "ESPN_SCOREBOARD",
    "ESPN_GAME_SUMMARY",
    "NBA_CDN_CURRENT_BOXSCORE",
    "NBA_STATS_PLAYERGAMELOG",
    "NBA_CDN_SETTLED_BOXSCORE",
}


class NBAPointsSnapshotPersistenceError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        write_may_have_committed: bool = False,
    ) -> None:
        self.code = str(code)
        self.write_may_have_committed = bool(write_may_have_committed)
        super().__init__(detail or self.code)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _aware(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp is timezone-naive")
    return parsed


def seal_source_provenance(
    responses: list[Mapping[str, Any]],
    *,
    captured_at: str,
) -> dict[str, Any]:
    """Seal hashes of the actual fixed-origin HTTP responses used by capture."""
    names = sorted({str(item.get("source") or "") for item in responses if item.get("source")})
    payload = {
        "transport": TRANSPORT,
        "captured_at": str(captured_at),
        "response_count": len(responses),
        "source_names": names,
        "responses": [dict(item) for item in responses],
    }
    payload["digest"] = _digest(payload)
    return payload


def _require_service_role_client(db: Any) -> None:
    expected = os.environ.get("SUPABASE_SERVICE_KEY")
    actual = getattr(db, "supabase_key", None)
    if (
        not isinstance(expected, str)
        or not expected
        or not isinstance(actual, str)
        or not actual
        or not hmac.compare_digest(expected, actual)
    ):
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_WRITER_NOT_AUTHORIZED",
            "The writer is not the configured server-side Supabase service client.",
        )


def _validate_sealed_provenance(snapshot: Mapping[str, Any]) -> None:
    role = snapshot.get("role_status")
    if not isinstance(role, Mapping):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    identity = role.get("official_source_identity")
    provenance = role.get("official_source_provenance")
    if not isinstance(identity, Mapping) or not isinstance(provenance, Mapping):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    if provenance.get("transport") != TRANSPORT:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    responses = provenance.get("responses")
    if not isinstance(responses, list) or not 13 <= len(responses) <= 32:
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    source_names = {str(value) for value in provenance.get("source_names", [])}
    if not _REQUIRED_SOURCE_NAMES.issubset(source_names):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")

    for response in responses:
        if not isinstance(response, Mapping):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
        parsed = urlsplit(str(response.get("url") or ""))
        allowed = _ALLOWED_HOST_PATHS.get(parsed.hostname or "")
        if parsed.scheme != "https" or not (
            (parsed.hostname == "cdn.nba.com" and re.fullmatch(
                r"/static/json/liveData/boxscore/boxscore_[0-9]{10}\.json", parsed.path
            ))
            or (allowed is not None and parsed.path in allowed)
        ):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
        if int(response.get("status_code") or 0) != 200:
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
        if not re.fullmatch(r"[0-9a-f]{64}", str(response.get("sha256") or "")):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
        try:
            observed = _aware(response.get("captured_at"))
            frozen = _aware(snapshot.get("captured_at"))
            event_start = _aware(snapshot.get("event_start_time"))
        except (TypeError, ValueError):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
        if observed > frozen or observed >= event_start:
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")

    supplied_digest = str(provenance.get("digest") or "")
    unsigned = {key: value for key, value in provenance.items() if key != "digest"}
    if not re.fullmatch(r"[0-9a-f]{64}", supplied_digest) or not hmac.compare_digest(
        supplied_digest, _digest(unsigned)
    ):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")

    game_id = str(identity.get("nba_game_id") or "")
    person_id = str(identity.get("nba_person_id") or "")
    espn_event_id = str(identity.get("espn_event_id") or "")
    canonical_event_id = str(identity.get("canonical_event_id") or "")
    role_team_id = str(role.get("team_id") or "")
    role_team_tricode = str(role.get("team") or "").upper()
    opportunity = snapshot.get("opportunity_ledger")
    source_times = snapshot.get("source_timestamps")
    try:
        identity_start = _aware(identity.get("event_start_time"))
        snapshot_start = _aware(snapshot.get("event_start_time"))
        identity_market = _aware(identity.get("market_capture_at"))
        snapshot_market = _aware((source_times or {}).get("market_board_capture"))
    except (TypeError, ValueError):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    if (
        not re.fullmatch(r"[0-9]{10}", game_id)
        or not re.fullmatch(r"[1-9][0-9]{0,9}", person_id)
        or not re.fullmatch(r"espn-[0-9]{6,12}", espn_event_id)
        or canonical_event_id != str(snapshot.get("event_id") or "")
        or espn_event_id != canonical_event_id
        or str(identity.get("player") or "").strip().casefold()
        != str(snapshot.get("player") or "").strip().casefold()
        or not role_team_id
        or str(identity.get("team_id") or "") != role_team_id
        or str(identity.get("team_tricode") or "").upper() != role_team_tricode
        or float(identity.get("line")) != float(snapshot.get("line"))
        or str(identity.get("period") or "") != "FULL_GAME"
        or str(identity.get("stat_type") or "") != "POINTS"
        or identity_start != snapshot_start
        or identity_market != snapshot_market
        or str(identity.get("request_run_id") or "").strip() == ""
        or str(identity.get("request_row_key") or "").strip() == ""
        or not isinstance(opportunity, Mapping)
        or str(opportunity.get("status") or "").upper() not in {"READY", "PASS", "COMPLETE"}
        or str(role.get("status") or "").upper() != "ACTIVE"
    ):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")


def _read_snapshot(db: Any, snapshot_id: str) -> dict[str, Any] | None:
    try:
        result = (
            db.table(TABLE)
            .select("*")
            .eq("source_snapshot_id", snapshot_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_READBACK_UNAVAILABLE",
            type(exc).__name__,
            write_may_have_committed=False,
        ) from exc
    rows = getattr(result, "data", None) or []
    if not isinstance(rows, list):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_READBACK_UNAVAILABLE")
    return dict(rows[0]) if rows else None


def _matches_snapshot(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    for key, value in expected.items():
        if key not in actual or _canonical(value) != _canonical(actual.get(key)):
            return False
    return True


def persist_validated_nba_points_snapshot(
    *,
    db: Any,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a fully validated snapshot with no overwrite path."""
    _require_service_role_client(db)
    frozen = dict(snapshot)
    snapshot_id = str(frozen.get("source_snapshot_id") or "")
    try:
        uuid.UUID(snapshot_id)
    except (ValueError, TypeError, AttributeError):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_IDEMPOTENCY_CONFLICT")

    if (
        frozen.get("sport") != "NBA"
        or frozen.get("stat_type") != "POINTS"
        or frozen.get("hydration_status") != "PASS"
        or list(frozen.get("blockers") or [])
        or frozen.get("can_execute") is not False
        or not isinstance(frozen.get("game_log"), list)
        or len(frozen.get("game_log") or []) < 10
        or not isinstance(frozen.get("box_score_log"), list)
        or len(frozen.get("box_score_log") or []) < 10
    ):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    if any(
        key in frozen
        for key in ("probability", "calibrated_probability", "prediction_id", "model_artifact_version")
    ):
        raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
    _validate_sealed_provenance(frozen)

    existing = _read_snapshot(db, snapshot_id)
    if existing is not None:
        if not _matches_snapshot(frozen, existing):
            raise NBAPointsSnapshotPersistenceError("NBA_POINTS_SNAPSHOT_IDEMPOTENCY_CONFLICT")
        return {
            "status": "ALREADY_PERSISTED",
            "source_snapshot_id": snapshot_id,
            "readback_verified": True,
            "can_execute": False,
        }

    try:
        # resolution=ignore-duplicates is INSERT/DO NOTHING, never an overwrite.
        db.table(TABLE).upsert(
            frozen,
            on_conflict="source_snapshot_id",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_WRITE_FAILED",
            type(exc).__name__,
            write_may_have_committed=True,
        ) from exc

    stored = _read_snapshot_after_write(db, snapshot_id)
    if stored is None:
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_READBACK_UNAVAILABLE",
            "The insert may have committed; canonical readback did not return a row.",
            write_may_have_committed=True,
        )
    if not _matches_snapshot(frozen, stored):
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_IDEMPOTENCY_CONFLICT",
            "The source_snapshot_id exists with different frozen content.",
            write_may_have_committed=True,
        )
    return {
        "status": "PERSISTED",
        "source_snapshot_id": snapshot_id,
        "readback_verified": True,
        "can_execute": False,
    }


def _read_snapshot_after_write(db: Any, snapshot_id: str) -> dict[str, Any] | None:
    try:
        result = (
            db.table(TABLE)
            .select("*")
            .eq("source_snapshot_id", snapshot_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_READBACK_UNAVAILABLE",
            type(exc).__name__,
            write_may_have_committed=True,
        ) from exc
    rows = getattr(result, "data", None) or []
    if not isinstance(rows, list):
        raise NBAPointsSnapshotPersistenceError(
            "NBA_POINTS_SNAPSHOT_READBACK_UNAVAILABLE",
            write_may_have_committed=True,
        )
    return dict(rows[0]) if rows else None


__all__ = [
    "NBAPointsSnapshotPersistenceError",
    "persist_validated_nba_points_snapshot",
    "seal_source_provenance",
]
