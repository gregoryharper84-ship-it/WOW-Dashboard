"""Server-owned canonical hydration for V17 MLB TEAM_EVENT requests.

Direct team/event ingress prefers the canonical MLB forward-shadow ledger for
venue/starter identity. When that ledger has no usable snapshot (or is missing
only the scorer's venue/starter fields), the request may supply a complete,
explicitly timestamped evidence package as a bounded fallback. Caller evidence
never overrides contradictory canonical identity and never substitutes market
odds or narrative for fitted-model inputs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_REQUIRED_CANONICAL_FIELDS = (
    "venue_name",
    "home_probable_pitcher",
    "away_probable_pitcher",
)
_CANONICAL_TO_CALLER = {
    "venue_name": "venue",
    "home_probable_pitcher": "home_starting_pitcher",
    "away_probable_pitcher": "away_starting_pitcher",
}
_CALLER_REQUIRED_FIELDS = tuple(_CANONICAL_TO_CALLER.values())


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


def _caller_snapshot_metadata(req: Any) -> tuple[str, datetime] | None:
    source_snapshot_id = str(getattr(req, "source_snapshot_id", "") or "").strip()
    snapshot_time = _aware(getattr(req, "latest_material_update_timestamp", None))
    now = datetime.now(timezone.utc)
    if not source_snapshot_id or snapshot_time is None or snapshot_time > now:
        return None
    return source_snapshot_id, snapshot_time


def _caller_fallback(req: Any, *, blocker_code: str) -> dict[str, Any] | None:
    caller = dict(getattr(req, "sport_specific_evidence", None) or {})
    missing = [name for name in _CALLER_REQUIRED_FIELDS if not str(caller.get(name) or "").strip()]
    if missing:
        return None

    metadata = _caller_snapshot_metadata(req)
    if metadata is None:
        return None
    source_snapshot_id, snapshot_time = metadata

    evidence = {
        **caller,
        "venue": str(caller["venue"]).strip(),
        "home_starting_pitcher": str(caller["home_starting_pitcher"]).strip(),
        "away_starting_pitcher": str(caller["away_starting_pitcher"]).strip(),
        "home_starter_status": str(caller.get("home_starter_status") or "PROBABLE").strip(),
        "away_starter_status": str(caller.get("away_starter_status") or "PROBABLE").strip(),
        "home_lineup_status": str(caller.get("home_lineup_status") or "PROJECTED").strip(),
        "away_lineup_status": str(caller.get("away_lineup_status") or "PROJECTED").strip(),
    }
    return {
        "ok": True,
        "code": "MLB_TEAM_EVENT_CALLER_EVIDENCE_FALLBACK_READY",
        "fallback_reason": blocker_code,
        "evidence": evidence,
        "canonical_source_snapshot_id": source_snapshot_id,
        "canonical_snapshot_timestamp": snapshot_time.isoformat(),
        "caller_source_snapshot_id": source_snapshot_id,
        "evidence_authority": "EXPLICIT_REQUEST_FALLBACK",
        "can_execute": False,
    }


def resolve_mlb_team_event_evidence(req: Any, *, event_api: Any) -> dict[str, Any]:
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        fallback = _caller_fallback(req, blocker_code="MLB_TEAM_EVENT_CANONICAL_CLIENT_UNAVAILABLE")
        return fallback or {"ok": False, "code": "MLB_TEAM_EVENT_CANONICAL_CLIENT_UNAVAILABLE", "missing_fields": []}

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
        fallback = _caller_fallback(req, blocker_code="MLB_TEAM_EVENT_CANONICAL_QUERY_FAILED")
        return fallback or {
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
        fallback = _caller_fallback(req, blocker_code="MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE")
        return fallback or {
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

    caller = dict(getattr(req, "sport_specific_evidence", None) or {})
    canonical = {
        "venue": row.get("venue_name"),
        "official_event_status": row.get("event_status"),
        "home_starting_pitcher": row.get("home_probable_pitcher"),
        "away_starting_pitcher": row.get("away_probable_pitcher"),
        "home_starter_status": "PROBABLE",
        "away_starter_status": "PROBABLE",
        "home_lineup_status": "PROJECTED",
        "away_lineup_status": "PROJECTED",
    }

    # Shadow snapshot is authoritative whenever it has a value. Caller evidence
    # may only fill a missing canonical scorer input; a conflicting supplied
    # value always fails closed before any fallback merge occurs.
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

    missing_canonical = [name for name in _REQUIRED_CANONICAL_FIELDS if not str(row.get(name) or "").strip()]
    fallback_fields: list[str] = []
    if missing_canonical:
        missing_caller = [
            _CANONICAL_TO_CALLER[name]
            for name in missing_canonical
            if not str(caller.get(_CANONICAL_TO_CALLER[name]) or "").strip()
        ]
        metadata = _caller_snapshot_metadata(req)
        if missing_caller or metadata is None:
            return {
                "ok": False,
                "code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_INCOMPLETE",
                "missing_fields": missing_caller or [_CANONICAL_TO_CALLER[name] for name in missing_canonical],
            }

        for canonical_name in missing_canonical:
            caller_name = _CANONICAL_TO_CALLER[canonical_name]
            canonical[caller_name] = str(caller[caller_name]).strip()
            fallback_fields.append(caller_name)

    return {
        "ok": True,
        "code": (
            "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_WITH_CALLER_FALLBACK_READY"
            if fallback_fields
            else "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_READY"
        ),
        "evidence": canonical,
        "canonical_source_snapshot_id": str(row["snapshot_id"]),
        "canonical_snapshot_timestamp": snap_time.isoformat(),
        "caller_source_snapshot_id": str(req.source_snapshot_id),
        "fallback_fields": fallback_fields,
        "evidence_authority": (
            "CANONICAL_MLB_LEDGER_WITH_EXPLICIT_REQUEST_FALLBACK"
            if fallback_fields
            else "CANONICAL_MLB_LEDGER"
        ),
        "can_execute": False,
    }
