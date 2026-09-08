"""Server-owned canonical hydration for V17 MLB TEAM_EVENT requests.

Direct team/event ingress must not depend on a Custom GPT caller to reproduce
venue/starter/lineup-status fields that already exist in WOW's canonical MLB
forward-shadow ledger.  This module resolves the latest still-pregame PASS
snapshot for the exact official event and validates immutable event identity
before handing evidence to the fitted event scorer.

Caller-provided sport_specific_evidence is cross-check context only.  It never
overrides contradictory canonical identity or creates model inputs when the
canonical record is missing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_REQUIRED_CANONICAL_FIELDS = (
    "venue_name",
    "home_probable_pitcher",
    "away_probable_pitcher",
)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _same_text(left: Any, right: Any) -> bool:
    return " ".join(str(left or "").casefold().split()) == " ".join(str(right or "").casefold().split())


def resolve_mlb_team_event_evidence(req: Any, *, event_api: Any) -> dict[str, Any]:
    """Return a typed canonical-evidence resolution for one MLB event.

    Selection is event-id exact and only considers PASS snapshots.  The newest
    snapshot at or before request time is used.  Event participants and start
    time are cross-checked against the caller contract; mismatches fail closed.
    """
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return {"ok": False, "code": "MLB_TEAM_EVENT_CANONICAL_CLIENT_UNAVAILABLE", "missing_fields": []}

    try:
        rows = (
            get_client()
            .table("wow_mlb_forward_shadow_events")
            .select(
                "official_event_id,event_start_time,event_status,home_team,away_team,venue_name,"
                "home_probable_pitcher,away_probable_pitcher,snapshot_id,"
                "snapshot_timestamp,feature_hydration_status"
            )
            .eq("official_event_id", str(req.official_event_id))
            .eq("feature_hydration_status", "PASS")
            .order("snapshot_timestamp", desc=True)
            .limit(8)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_QUERY_FAILED",
            "error_type": type(exc).__name__,
            "missing_fields": [],
        }

    now = datetime.now(timezone.utc)
    usable = []
    for raw in rows:
        row = dict(raw)
        snap_time = _aware(row.get("snapshot_timestamp"))
        event_start = _aware(row.get("event_start_time"))
        if snap_time is None or event_start is None:
            continue
        if snap_time > now:
            continue
        usable.append((snap_time, event_start, row))

    if not usable:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": list(_REQUIRED_CANONICAL_FIELDS),
        }

    usable.sort(key=lambda item: item[0], reverse=True)
    snap_time, event_start, row = usable[0]
    requested_start = _aware(req.event_start_time_utc)
    if requested_start is None or abs((requested_start - event_start).total_seconds()) > 1:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_IDENTITY_MISMATCH",
            "identity_mismatches": ["event_start_time_utc"],
            "missing_fields": [],
        }

    identity_mismatches = []
    if not _same_text(row.get("home_team"), req.home_team):
        identity_mismatches.append("home_team")
    if not _same_text(row.get("away_team"), req.away_team):
        identity_mismatches.append("away_team")
    if identity_mismatches:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_IDENTITY_MISMATCH",
            "identity_mismatches": identity_mismatches,
            "missing_fields": [],
        }

    missing = [name for name in _REQUIRED_CANONICAL_FIELDS if not str(row.get(name) or "").strip()]
    if missing:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_INCOMPLETE",
            "missing_fields": missing,
        }

    canonical = {
        "venue": row["venue_name"],
        "official_event_status": row.get("event_status"),
        "home_starting_pitcher": row["home_probable_pitcher"],
        "away_starting_pitcher": row["away_probable_pitcher"],
        "home_starter_status": "PROBABLE",
        "away_starter_status": "PROBABLE",
        "home_lineup_status": "PROJECTED",
        "away_lineup_status": "PROJECTED",
    }

    caller = dict(getattr(req, "sport_specific_evidence", None) or {})
    contradictions = []
    for key, value in canonical.items():
        supplied = caller.get(key)
        if value not in (None, "") and supplied not in (None, "") and not _same_text(supplied, value):
            contradictions.append(key)
    if contradictions:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CALLER_EVIDENCE_CONTRADICTS_CANONICAL",
            "identity_mismatches": contradictions,
            "missing_fields": [],
        }

    return {
        "ok": True,
        "code": "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_READY",
        "evidence": canonical,
        "canonical_source_snapshot_id": str(row["snapshot_id"]),
        "canonical_snapshot_timestamp": snap_time.isoformat(),
        "caller_source_snapshot_id": str(req.source_snapshot_id),
        "can_execute": False,
    }
