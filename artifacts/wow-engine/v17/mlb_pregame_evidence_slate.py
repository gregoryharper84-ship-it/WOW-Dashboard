"""Governed slate adapter: one hydrated MLB slate writes its prelock evidence.

``wow_mlb_forward_auto_hydrate_pregame()`` freezes each event's official source
capture and both team feature vectors, but nothing turned those frozen rows into
the event-specific prelock evidence the model-evidence triad reads. This module
bridges that gap for one slate: it reads the served fitted artifact, then, per
hydrated event, derives the source and feature payloads from the persisted
snapshot rows and hands them to the governed writer in
``v17.mlb_pregame_evidence_ingest``.

Governed properties:

* the artifact identity (family, version, feature schema, checksum) comes from
  the served registry row, never from a caller or a constant;
* payload digests are derived inside the writer from the payloads actually
  persisted, so this adapter cannot supply a hash that does not describe a row;
* only the single pair of ``PASS`` feature snapshots for the event is accepted,
  and both sides must carry the same ordered feature schema;
* event status is canonicalized only through the documented pregame alias
  (``PRE-GAME``/``PREGAME`` -> ``SCHEDULED``). ``Warmup`` and ``Delayed Start``
  are pregame in the shadow ledger but are not relabeled ``SCHEDULED`` here, so
  they remain refused by the writer instead of acquiring evidence under a status
  they do not have;
* a slate with no served artifact, an ambiguous artifact, or no hydrated event
  writes nothing and reports the typed reason;
* no probability, calibrated bound, rank flag, verification flag or
  certification is written, and ``can_execute`` is always false.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from v17.mlb_pregame_evidence_ingest import (
    ALREADY_CURRENT,
    PERSISTED,
    canonical_sha256,
    ingest_pregame_snapshot,
)

ARTIFACT_TABLE = "wow_mlb_event_fitted_model_artifacts"
EVENT_TABLE = "wow_mlb_forward_shadow_events"
FEATURE_TABLE = "wow_mlb_forward_feature_snapshots"
PROVIDER_IDENTITY = "WOW_MLB_EVENT_FITTED_MODEL_V1"
CERTIFIED_LIFECYCLES = ("PROSPECTIVE_CERTIFIED", "CHAMPION")
SPORT = "MLB"
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

SLATE_COMPLETE = "COMPLETE"
SLATE_PARTIAL = "PARTIAL"
SLATE_BLOCKED = "BLOCKED"

# A slate is bounded; the loop is per event, not per league.
MAX_SLATE_EVENTS = 200

# Only this documented alias becomes SCHEDULED. The shadow ledger emits
# 'Scheduled', 'Pre-Game', 'Warmup' and 'Delayed Start'; the latter two are
# never rewritten into a status the evidence contract would then trust.
_PREGAME_STATUS_ALIASES = {"PRE-GAME": "SCHEDULED", "PREGAME": "SCHEDULED"}

_EVENT_COLUMNS = (
    "shadow_event_id,official_event_id,official_date,event_start_time,event_status,"
    "home_team,away_team,home_team_id,away_team_id,venue_name,"
    "home_probable_pitcher,away_probable_pitcher,"
    "home_probable_pitcher_id,away_probable_pitcher_id,"
    "snapshot_id,snapshot_timestamp,source_game_json,feature_hydration_status"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _canonical_event_status(value: Any) -> str:
    """Canonicalize only the documented pregame alias; never guess."""
    status = _text(value).upper()
    return _PREGAME_STATUS_ALIASES.get(status, status)


def _artifact_identity(artifact: Mapping[str, Any]) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    blockers: list[str] = []
    identity = {
        "model_family": _text(artifact.get("model_family")),
        "model_artifact_version": _text(artifact.get("model_artifact_version")),
        "feature_schema_version": _text(artifact.get("feature_schema_version")),
        "model_artifact_checksum": _text(artifact.get("artifact_checksum")).lower(),
    }
    for field, value in identity.items():
        if not value:
            blockers.append(f"MLB_PREGAME_EVIDENCE_ARTIFACT_INCOMPLETE:{field}")
    checksum = identity["model_artifact_checksum"]
    if checksum and not (
        len(checksum) == 64 and all(ch in "0123456789abcdef" for ch in checksum)
    ):
        blockers.append("MLB_PREGAME_EVIDENCE_ARTIFACT_CHECKSUM_INVALID")
    if artifact.get("active") is not True or artifact.get("promoted") is not True:
        blockers.append("MLB_PREGAME_EVIDENCE_ARTIFACT_NOT_SERVED")
    lifecycle = _text(artifact.get("lifecycle_state")).upper()
    if lifecycle not in CERTIFIED_LIFECYCLES:
        blockers.append(
            f"MLB_PREGAME_EVIDENCE_ARTIFACT_NOT_CERTIFIED:{lifecycle or 'UNKNOWN'}"
        )
    if blockers:
        return None, tuple(dict.fromkeys(blockers))
    return identity, ()


def served_artifact(db: Any) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    """Read the one served MLB event artifact, or the typed reason there is none.

    Two served artifacts are ambiguous: the writer refuses rather than picking
    whichever row the database happened to return first.
    """
    try:
        rows = _rows(
            db.table(ARTIFACT_TABLE)
            .select("*")
            .eq("provider_identity", PROVIDER_IDENTITY)
            .eq("sport", SPORT)
            .eq("active", True)
            .eq("promoted", True)
            .limit(4)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable registry is not a write
        return None, (f"MLB_PREGAME_EVIDENCE_ARTIFACT_READ_FAILED:{type(exc).__name__}",)

    candidates = [
        row for row in rows
        if _text(row.get("lifecycle_state")).upper() in CERTIFIED_LIFECYCLES
    ]
    if not candidates:
        return None, ("MLB_PREGAME_EVIDENCE_ARTIFACT_UNAVAILABLE",)
    if len(candidates) > 1:
        return None, ("MLB_PREGAME_EVIDENCE_ARTIFACT_AMBIGUOUS",)
    return candidates[0], ()


def slate_events(db: Any, slate_date: str) -> list[dict[str, Any]]:
    """Read the hydrated events for one slate date. Raises on an unreadable store."""
    result = (
        db.table(EVENT_TABLE)
        .select(_EVENT_COLUMNS)
        .eq("official_date", slate_date)
        .eq("feature_hydration_status", "PASS")
        .order("event_start_time")
        .limit(MAX_SLATE_EVENTS)
        .execute()
    )
    return _rows(result)


def _source_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen source capture this event's ``snapshot_id`` points at."""
    fields = (
        "official_event_id",
        "official_date",
        "event_start_time",
        "event_status",
        "home_team",
        "away_team",
        "home_team_id",
        "away_team_id",
        "venue_name",
        "home_probable_pitcher",
        "away_probable_pitcher",
        "home_probable_pitcher_id",
        "away_probable_pitcher_id",
    )
    payload: dict[str, Any] = {
        "snapshot_id": _text(event.get("snapshot_id")),
        "snapshot_timestamp": _text(event.get("snapshot_timestamp")),
    }
    payload.update({field: event.get(field) for field in fields})
    game = event.get("source_game_json")
    if isinstance(game, Mapping) and game:
        payload["source_game_json"] = dict(game)
    return payload


def _feature_sides(
    db: Any, shadow_event_id: str
) -> tuple[tuple[dict[str, Any], dict[str, Any]] | None, tuple[str, ...]]:
    try:
        rows = _rows(
            db.table(FEATURE_TABLE)
            .select("*")
            .eq("shadow_event_id", shadow_event_id)
            .eq("hydration_status", "PASS")
            .limit(8)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - a failed read is never assumed frozen
        return None, (f"MLB_PREGAME_EVIDENCE_FEATURE_READ_FAILED:{type(exc).__name__}",)

    home = [row for row in rows if _text(row.get("side")).upper() == "HOME"]
    away = [row for row in rows if _text(row.get("side")).upper() == "AWAY"]
    if not home or not away:
        return None, ("MLB_PREGAME_EVIDENCE_FEATURE_PAIR_UNAVAILABLE",)
    if len(home) > 1 or len(away) > 1:
        return None, ("MLB_PREGAME_EVIDENCE_FEATURE_PAIR_AMBIGUOUS",)
    return (home[0], away[0]), ()


def _vector(row: Mapping[str, Any], width: int) -> list[float] | None:
    raw = row.get("feature_vector")
    if not isinstance(raw, (list, tuple)) or len(raw) != width:
        return None
    values: list[float] = []
    for value in raw:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        values.append(parsed)
    return values


def _feature_payload(
    home: Mapping[str, Any], away: Mapping[str, Any]
) -> tuple[dict[str, Any], str, str] | tuple[None, None, tuple[str, ...]]:
    """Build the frozen feature payload, its identity and its freeze time."""
    names = row_names = home.get("feature_names")
    foreign_names = away.get("feature_names")
    if not isinstance(row_names, (list, tuple)) or not row_names:
        return None, None, ("MLB_PREGAME_EVIDENCE_FEATURE_SCHEMA_INCOMPLETE",)
    names = [str(name) for name in row_names]
    if not isinstance(foreign_names, (list, tuple)) or [
        str(name) for name in foreign_names
    ] != names:
        return None, None, ("MLB_PREGAME_EVIDENCE_FEATURE_SCHEMA_MISMATCH",)

    home_vector = _vector(home, len(names))
    away_vector = _vector(away, len(names))
    if home_vector is None or away_vector is None:
        return None, None, ("MLB_PREGAME_EVIDENCE_FEATURE_VECTOR_INVALID",)

    home_id = _text(home.get("feature_snapshot_id"))
    away_id = _text(away.get("feature_snapshot_id"))
    home_at = _when(home.get("created_at"))
    away_at = _when(away.get("created_at"))
    if not home_id or not away_id or home_at is None or away_at is None:
        return None, None, ("MLB_PREGAME_EVIDENCE_FEATURE_SNAPSHOT_INCOMPLETE",)

    source_ids = sorted(
        {
            _text(value)
            for row in (home, away)
            for value in (row.get("source_snapshot_ids") or [])
            if _text(value)
        }
    )
    payload = {
        "feature_names": names,
        "home": {
            "feature_snapshot_id": home_id,
            "scoring_team_id": home.get("scoring_team_id"),
            "probable_starter_id": home.get("probable_starter_id"),
            "vector": home_vector,
        },
        "away": {
            "feature_snapshot_id": away_id,
            "scoring_team_id": away.get("scoring_team_id"),
            "probable_starter_id": away.get("probable_starter_id"),
            "vector": away_vector,
        },
        "source_snapshot_ids": source_ids,
    }
    frozen_at = max(home_at, away_at).isoformat()
    return payload, f"{home_id}:{away_id}", frozen_at


def _event_receipt(
    status: str, *, event: Mapping[str, Any], blockers: Iterable[str] = ()
) -> dict[str, Any]:
    return {
        "status": status,
        "official_event_id": _text(event.get("official_event_id")),
        "event_status": _text(event.get("event_status")),
        "blockers": list(blockers),
        "can_execute": CAN_EXECUTE,
    }


def ingest_event(
    db: Any,
    *,
    artifact: Mapping[str, Any],
    event: Mapping[str, Any],
    now: Any,
) -> dict[str, Any]:
    """Write one event's prelock evidence, or return the typed reason it cannot."""
    identity, blockers = _artifact_identity(artifact)
    if identity is None:
        return _event_receipt(SLATE_BLOCKED, event=event, blockers=blockers)

    sides, blockers = _feature_sides(db, _text(event.get("shadow_event_id")))
    if sides is None:
        return _event_receipt(SLATE_BLOCKED, event=event, blockers=blockers)
    home, away = sides
    feature_payload, feature_snapshot_id, feature_at = _feature_payload(home, away)
    if feature_payload is None or feature_snapshot_id is None:
        return _event_receipt(SLATE_BLOCKED, event=event, blockers=feature_at)

    try:
        receipt = ingest_pregame_snapshot(
            db,
            event={
                "official_event_id": _text(event.get("official_event_id")),
                "event_start_time": _text(event.get("event_start_time")),
                "event_status": _canonical_event_status(event.get("event_status")),
            },
            source_payload=_source_payload(event),
            features=feature_payload,
            source_snapshot_id=_text(event.get("snapshot_id")),
            source_snapshot_timestamp=_text(event.get("snapshot_timestamp")),
            feature_snapshot_id=feature_snapshot_id,
            feature_snapshot_timestamp=feature_at,
            model_family=identity["model_family"],
            model_artifact_version=identity["model_artifact_version"],
            model_artifact_checksum=identity["model_artifact_checksum"],
            feature_schema_version=identity["feature_schema_version"],
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - one event cannot abort the slate
        return _event_receipt(
            "WRITE_FAILED",
            event=event,
            blockers=(f"MLB_PREGAME_EVIDENCE_EVENT_FAILED:{type(exc).__name__}",),
        )
    return {
        **receipt,
        "official_event_id": _text(event.get("official_event_id")),
        "event_status": _text(event.get("event_status")),
        "feature_snapshot_id": feature_snapshot_id,
    }


def run_slate_pregame_evidence(
    db: Any, *, slate_date: Any, now: Any = None
) -> dict[str, Any]:
    """Write prelock evidence for every hydrated event on one slate date."""
    date_text = _text(slate_date)
    decision_at = _text(now) or datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "slate_date": date_text,
        "decision_at": decision_at,
        "status": SLATE_BLOCKED,
        "blockers": [],
        "events": [],
        "written": 0,
        "already_current": 0,
        "refused": 0,
        "pregame_evidence_written": False,
        "probability_written": False,
        "verification_flag_written": False,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
    if db is None:
        return {**base, "blockers": ["MLB_PREGAME_EVIDENCE_DB_UNAVAILABLE"]}
    if not date_text:
        return {**base, "blockers": ["MLB_PREGAME_EVIDENCE_SLATE_DATE_MISSING"]}
    artifact, artifact_blockers = served_artifact(db)
    if artifact is None:
        return {**base, "blockers": list(artifact_blockers)}

    try:
        events = slate_events(db, date_text)
    except Exception as exc:  # noqa: BLE001 - an unreadable slate is not an empty one
        return {
            **base,
            "blockers": [
                f"MLB_PREGAME_EVIDENCE_SLATE_READ_FAILED:{type(exc).__name__}"
            ],
        }
    if not events:
        return {**base, "blockers": ["MLB_PREGAME_EVIDENCE_SLATE_EMPTY"]}

    results = [
        ingest_event(db, artifact=artifact, event=event, now=decision_at)
        for event in events
    ]
    counts = Counter(result["status"] for result in results)
    written = counts[PERSISTED]
    already = counts[ALREADY_CURRENT]
    blocked = sum(
        count
        for status, count in counts.items()
        if status not in (PERSISTED, ALREADY_CURRENT)
    )
    if written or already:
        status = SLATE_PARTIAL if blocked else SLATE_COMPLETE
    else:
        status = SLATE_BLOCKED
    return {
        **base,
        "status": status,
        "artifact": {
            "model_family": _text(artifact.get("model_family")),
            "model_artifact_version": _text(artifact.get("model_artifact_version")),
            "feature_schema_version": _text(artifact.get("feature_schema_version")),
            "model_artifact_checksum": _text(artifact.get("artifact_checksum")).lower(),
        },
        "events": results,
        "written": written,
        "already_current": already,
        "refused": blocked,
        "pregame_evidence_written": bool(written),
    }


def slate_payload_digest(payload: Mapping[str, Any]) -> str:
    """Expose the same canonical digest the writer uses, for audit callers."""
    return canonical_sha256(payload)


__all__ = [
    "ARTIFACT_TABLE",
    "CAN_EXECUTE",
    "CERTIFIED_LIFECYCLES",
    "EVENT_TABLE",
    "FEATURE_TABLE",
    "MAX_SLATE_EVENTS",
    "PROVIDER_IDENTITY",
    "SLATE_BLOCKED",
    "SLATE_COMPLETE",
    "SLATE_PARTIAL",
    "TERMINAL_AUTHORITY",
    "ingest_event",
    "run_slate_pregame_evidence",
    "served_artifact",
    "slate_events",
    "slate_payload_digest",
]
