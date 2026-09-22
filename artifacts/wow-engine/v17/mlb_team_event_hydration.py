"""Server-owned canonical hydration for V17 MLB TEAM_EVENT requests.

Direct team/event ingress prefers the canonical MLB forward-shadow ledger for
venue/starter identity. Provider discovery ids are not automatically MLB
canonical event ids, so the resolver first tries the supplied id and then, only
when that exact lookup is empty, performs a bounded server-owned identity join
on slate date + participants + scheduled start. The fallback must resolve to
exactly one canonical MLB event before any request id is rewritten.

When the canonical ledger has no usable snapshot (or is missing only the
scorer's venue/starter fields), the request may supply a complete, explicitly
timestamped evidence package as a bounded fallback. Caller evidence never
overrides contradictory canonical identity and never substitutes market odds or
narrative for fitted-model inputs.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


_REQUIRED_CANONICAL_FIELDS = (
    "venue_name",
    "home_probable_pitcher",
    "away_probable_pitcher",
)
_CALLER_REQUIRED_FIELDS = (
    "venue",
    "home_starting_pitcher",
    "away_starting_pitcher",
)
_IDENTITY_START_TOLERANCE_SECONDS = 60
_CANONICAL_SELECT = (
    "official_event_id,official_date,event_start_time,event_status,home_team,away_team,venue_name,"
    "home_probable_pitcher,away_probable_pitcher,snapshot_id,"
    "snapshot_timestamp,feature_hydration_status"
)

# Provider event feeds frequently use city-only or nickname-only MLB participant
# labels while the canonical forward ledger stores full club names. Identity
# resolution must compare MLB-scoped aliases rather than raw display strings.
# This map is intentionally league-scoped so shared names such as Rangers,
# Giants, Cardinals, and Royals cannot collide with another sport.
_MLB_TEAM_ALIASES: dict[str, str] = {
    "yankees": "NYY", "new york yankees": "NYY", "nyy": "NYY",
    "red sox": "BOS", "boston red sox": "BOS", "boston": "BOS", "bos": "BOS",
    "blue jays": "TOR", "toronto blue jays": "TOR", "toronto": "TOR", "tor": "TOR",
    "orioles": "BAL", "baltimore orioles": "BAL", "baltimore": "BAL", "bal": "BAL",
    "rays": "TB", "tampa bay rays": "TB", "tampa bay": "TB", "tb": "TB",
    "white sox": "CWS", "chicago white sox": "CWS", "cws": "CWS",
    "guardians": "CLE", "cleveland guardians": "CLE", "cleveland": "CLE", "cle": "CLE",
    "tigers": "DET", "detroit tigers": "DET", "detroit": "DET", "det": "DET",
    "royals": "KC", "kansas city royals": "KC", "kansas city": "KC", "kc": "KC",
    "twins": "MIN", "minnesota twins": "MIN", "minnesota": "MIN", "min": "MIN",
    "astros": "HOU", "houston astros": "HOU", "houston": "HOU", "hou": "HOU",
    "angels": "LAA", "los angeles angels": "LAA", "la angels": "LAA", "laa": "LAA",
    "athletics": "ATH", "oakland athletics": "ATH", "oakland": "ATH", "ath": "ATH", "oak": "ATH",
    "mariners": "SEA", "seattle mariners": "SEA", "seattle": "SEA", "sea": "SEA",
    "rangers": "TEX", "texas rangers": "TEX", "texas": "TEX", "tex": "TEX",
    "braves": "ATL", "atlanta braves": "ATL", "atlanta": "ATL", "atl": "ATL",
    "marlins": "MIA", "miami marlins": "MIA", "miami": "MIA", "mia": "MIA",
    "mets": "NYM", "new york mets": "NYM", "nym": "NYM",
    "phillies": "PHI", "philadelphia phillies": "PHI", "philadelphia": "PHI", "phi": "PHI",
    "nationals": "WSH", "washington nationals": "WSH", "washington": "WSH", "wsh": "WSH",
    "cubs": "CHC", "chicago cubs": "CHC", "chc": "CHC",
    "reds": "CIN", "cincinnati reds": "CIN", "cincinnati": "CIN", "cin": "CIN",
    "brewers": "MIL", "milwaukee brewers": "MIL", "milwaukee": "MIL", "mil": "MIL",
    "pirates": "PIT", "pittsburgh pirates": "PIT", "pittsburgh": "PIT", "pit": "PIT",
    "cardinals": "STL", "st louis cardinals": "STL", "st. louis cardinals": "STL", "st louis": "STL", "st. louis": "STL", "stl": "STL",
    "diamondbacks": "ARI", "arizona diamondbacks": "ARI", "arizona": "ARI", "ari": "ARI",
    "rockies": "COL", "colorado rockies": "COL", "colorado": "COL", "col": "COL",
    "dodgers": "LAD", "los angeles dodgers": "LAD", "la dodgers": "LAD", "lad": "LAD",
    "padres": "SD", "san diego padres": "SD", "san diego": "SD", "sd": "SD",
    "giants": "SF", "san francisco giants": "SF", "san francisco": "SF", "sf": "SF",
}


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


def _mlb_team_key(value: Any) -> str:
    key = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").casefold())
    key = " ".join(key.split())
    if not key:
        return ""
    exact = _MLB_TEAM_ALIASES.get(key)
    if exact:
        return exact
    best: tuple[int, str] | None = None
    for alias, canonical in _MLB_TEAM_ALIASES.items():
        if alias in key and (best is None or len(alias) > best[0]):
            best = (len(alias), canonical)
    return best[1] if best else key


def _same_mlb_team(left: Any, right: Any) -> bool:
    left_key = _mlb_team_key(left)
    right_key = _mlb_team_key(right)
    return bool(left_key and right_key and left_key == right_key)


def _caller_fallback(req: Any, *, blocker_code: str) -> dict[str, Any] | None:
    caller = dict(getattr(req, "sport_specific_evidence", None) or {})
    missing = [name for name in _CALLER_REQUIRED_FIELDS if not str(caller.get(name) or "").strip()]
    if missing:
        return None

    source_snapshot_id = str(getattr(req, "source_snapshot_id", "") or "").strip()
    snapshot_time = _aware(getattr(req, "latest_material_update_timestamp", None))
    now = datetime.now(timezone.utc)
    if not source_snapshot_id or snapshot_time is None or snapshot_time > now:
        return None

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
        "canonical_official_event_id": str(getattr(req, "official_event_id", "") or ""),
        "caller_source_snapshot_id": source_snapshot_id,
        "evidence_authority": "EXPLICIT_REQUEST_FALLBACK",
        "canonical_identity_resolution": "CALLER_ID_PRESERVED",
        "can_execute": False,
    }


def _usable_rows(rows: list[dict[str, Any]], *, now: datetime) -> list[tuple[datetime, datetime, dict[str, Any]]]:
    usable: list[tuple[datetime, datetime, dict[str, Any]]] = []
    for raw in rows:
        row = dict(raw)
        snap_time = _aware(row.get("snapshot_timestamp"))
        event_start = _aware(row.get("event_start_time"))
        if snap_time is None or event_start is None:
            continue
        if snap_time > now:
            continue
        usable.append((snap_time, event_start, row))
    return usable


def _identity_join_rows(req: Any, *, client: Any, now: datetime) -> dict[str, Any]:
    """Resolve a provider event id to exactly one server-owned MLB identity.

    The provider id itself is ignored here. The join is deliberately bounded to
    the requested slate and requires both participants plus start-time agreement.
    Multiple snapshots of the same official event collapse to the newest one;
    multiple *event ids* remain ambiguous and fail closed.
    """
    requested_start = _aware(getattr(req, "event_start_time_utc", None))
    requested_slate_date = str(getattr(req, "requested_slate_date", "") or "").strip()
    if requested_start is None or not requested_slate_date:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": list(_REQUIRED_CANONICAL_FIELDS),
        }

    try:
        rows = (
            client
            .table("wow_mlb_forward_shadow_events")
            .select(_CANONICAL_SELECT)
            .eq("official_date", requested_slate_date)
            .eq("feature_hydration_status", "PASS")
            .order("snapshot_timestamp", desc=True)
            .limit(128)
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

    matches_by_id: dict[str, tuple[datetime, datetime, dict[str, Any]]] = {}
    for snap_time, event_start, row in _usable_rows(list(rows), now=now):
        if not _same_mlb_team(row.get("home_team"), getattr(req, "home_team", None)):
            continue
        if not _same_mlb_team(row.get("away_team"), getattr(req, "away_team", None)):
            continue
        if abs((requested_start - event_start).total_seconds()) > _IDENTITY_START_TOLERANCE_SECONDS:
            continue
        canonical_id = str(row.get("official_event_id") or "").strip()
        if not canonical_id:
            continue
        prior = matches_by_id.get(canonical_id)
        if prior is None or snap_time > prior[0]:
            matches_by_id[canonical_id] = (snap_time, event_start, row)

    if not matches_by_id:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": list(_REQUIRED_CANONICAL_FIELDS),
        }
    if len(matches_by_id) > 1:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_IDENTITY_AMBIGUOUS",
            "identity_mismatches": ["official_event_id"],
            "candidate_count": len(matches_by_id),
            "missing_fields": [],
        }

    snap_time, event_start, row = next(iter(matches_by_id.values()))
    return {
        "ok": True,
        "snap_time": snap_time,
        "event_start": event_start,
        "row": row,
        "identity_resolution": "PARTICIPANTS_START_SLATE",
    }


def resolve_mlb_team_event_evidence(req: Any, *, event_api: Any) -> dict[str, Any]:
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        fallback = _caller_fallback(req, blocker_code="MLB_TEAM_EVENT_CANONICAL_CLIENT_UNAVAILABLE")
        return fallback or {"ok": False, "code": "MLB_TEAM_EVENT_CANONICAL_CLIENT_UNAVAILABLE", "missing_fields": []}

    try:
        client = get_client()
        rows = (
            client
            .table("wow_mlb_forward_shadow_events")
            .select(_CANONICAL_SELECT)
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
    usable = _usable_rows(list(rows), now=now)
    identity_resolution = "EXACT_OFFICIAL_EVENT_ID"
    if not usable:
        joined = _identity_join_rows(req, client=client, now=now)
        if joined.get("ok") is not True:
            fallback = _caller_fallback(req, blocker_code=str(joined.get("code") or "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"))
            return fallback or joined
        usable = [(joined["snap_time"], joined["event_start"], joined["row"])]
        identity_resolution = str(joined["identity_resolution"])

    usable.sort(key=lambda item: item[0], reverse=True)
    snap_time, event_start, row = usable[0]
    requested_start = _aware(req.event_start_time_utc)
    if requested_start is None or abs((requested_start - event_start).total_seconds()) > _IDENTITY_START_TOLERANCE_SECONDS:
        return {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_IDENTITY_MISMATCH",
            "identity_mismatches": ["event_start_time_utc"],
            "missing_fields": [],
        }

    identity_mismatches = []
    if not _same_mlb_team(row.get("home_team"), req.home_team):
        identity_mismatches.append("home_team")
    if not _same_mlb_team(row.get("away_team"), req.away_team):
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
        fallback = _caller_fallback(req, blocker_code="MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_INCOMPLETE")
        return fallback or {
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
        "canonical_official_event_id": str(row["official_event_id"]),
        "canonical_source_snapshot_id": str(row["snapshot_id"]),
        "canonical_snapshot_timestamp": snap_time.isoformat(),
        "caller_official_event_id": str(getattr(req, "official_event_id", "") or ""),
        "caller_source_snapshot_id": str(req.source_snapshot_id),
        "evidence_authority": "CANONICAL_MLB_LEDGER",
        "canonical_identity_resolution": identity_resolution,
        "can_execute": False,
    }