"""TheSportsDB V1 free-API discovery challenger for WOW V17.

This module is discovery-only. It uses TheSportsDB's documented official API
surface; it never scrapes TheSportsDB web pages and never supplies sporting
probability, market probability, exact-line authority, ranking, staking, or
execution.

Provider event identifiers are aliases only. They are deliberately *not* mapped
to WOW ``official_event_id`` here. A downstream canonical resolver must prove
that mapping before a discovered row may be treated as canonically identified.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CAN_EXECUTE = False
PREDICTION_AUTHORITY = False
EXACT_LINE_AUTHORITY = False
RESEARCH_ONLY = True
PROVIDER = "THESPORTSDB"
SOURCE_CLASS = "FREE_OFFICIAL_API_DISCOVERY"
BASE_URL = "https://www.thesportsdb.com/api/v1/json/123"
USER_AGENT = "WOW-V17-Scout-Research/1.0 (schedule-discovery; contact=repo-owner)"
TIMEOUT_SECONDS = 10.0
DOCUMENTED_FREE_SCHEDULE_DAY_LIMIT = 3


@dataclass(frozen=True)
class FreeDiscoveryResult:
    ok: bool
    rows: tuple[dict[str, Any], ...] = ()
    code: str = "THESPORTSDB_DISCOVERY_OK"
    http_status: int | None = None
    coverage_complete: bool = False
    documented_free_response_limit: int = DOCUMENTED_FREE_SCHEDULE_DAY_LIMIT
    can_execute: bool = False


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _commence_time(raw: Mapping[str, Any]) -> str | None:
    """Return only a provider-supplied timezone-aware timestamp.

    The live free endpoint can emit a naive ``strTimestamp`` such as
    ``2026-09-25T16:00:00``. That is not a globally ordered instant. Preserve it
    only in provider fields; do not promote it to WOW ``commence_time`` unless
    the provider explicitly supplies ``Z`` or an offset.
    """
    timestamp = _text(raw.get("strTimestamp"))
    if not timestamp:
        return None
    candidate = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return timestamp


def normalize_event(raw: Mapping[str, Any], *, requested_sport: str) -> dict[str, Any] | None:
    """Translate one TheSportsDB event to a value-safe WOW discovery alias row."""
    if not isinstance(raw, Mapping):
        return None
    provider_event_id = _text(raw.get("idEvent"))
    home = _text(raw.get("strHomeTeam"))
    away = _text(raw.get("strAwayTeam"))
    if not provider_event_id or not home or not away:
        return None

    commence = _commence_time(raw)
    return {
        # IMPORTANT: no `id`, `event_id`, or `official_event_id` field. The
        # cross-sport canonical layer must not promote this provider alias.
        "provider_event_id": provider_event_id,
        "provider_event_id_type": "THESPORTSDB_IDEVENT_ALIAS",
        "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "provider_timestamp": _text(raw.get("strTimestamp")),
        "provider_date": _text(raw.get("dateEvent")),
        "provider_time": _text(raw.get("strTime") or raw.get("strEventTime")),
        "status": _text(raw.get("strStatus")) or "UNKNOWN",
        "league": _text(raw.get("strLeague")),
        "provider_league_id": _text(raw.get("idLeague")),
        "sport": _text(raw.get("strSport")) or requested_sport,
        "discovery_provider": PROVIDER,
        "source_provider": PROVIDER,
        "source_class": SOURCE_CLASS,
        "research_only": RESEARCH_ONLY,
        "prediction_authority": PREDICTION_AUTHORITY,
        "exact_line_authority": EXACT_LINE_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


def rows_from_payload(payload: Any, *, requested_sport: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        return ()
    events = payload.get("events")
    if events is None:
        return ()
    if not isinstance(events, list):
        return ()
    rows: list[dict[str, Any]] = []
    for raw in events:
        if not isinstance(raw, Mapping):
            continue
        normalized = normalize_event(raw, requested_sport=requested_sport)
        if normalized is not None:
            rows.append(normalized)
    return tuple(rows)


def fetch_day(
    *,
    slate_date: str,
    sport: str,
    league_id: str | None = None,
    opener: Any = None,
) -> FreeDiscoveryResult:
    """Fetch one documented V1 Schedule Day request from the official API.

    This challenger intentionally performs no retry fanout. The documented free
    Schedule Day response limit is three events, so even HTTP 200 must remain
    ``coverage_complete=False``. Production promotion requires a source/route
    that proves complete competition coverage plus canonical identity.
    """
    params: dict[str, str] = {"d": str(slate_date), "s": str(sport)}
    if league_id:
        params["l"] = str(league_id)
    url = f"{BASE_URL}/eventsday.php?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with (opener or urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None) or getattr(response, "code", None)
            body = response.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - discovery challenger must fail closed
        return FreeDiscoveryResult(False, code=f"THESPORTSDB_{type(exc).__name__}")

    try:
        payload = json.loads(body)
    except ValueError:
        return FreeDiscoveryResult(False, code="THESPORTSDB_INVALID_JSON", http_status=status)

    rows = rows_from_payload(payload, requested_sport=sport)
    return FreeDiscoveryResult(True, rows=rows, http_status=status)


__all__ = [
    "BASE_URL",
    "CAN_EXECUTE",
    "DOCUMENTED_FREE_SCHEDULE_DAY_LIMIT",
    "EXACT_LINE_AUTHORITY",
    "FreeDiscoveryResult",
    "PREDICTION_AUTHORITY",
    "PROVIDER",
    "RESEARCH_ONLY",
    "fetch_day",
    "normalize_event",
    "rows_from_payload",
]
