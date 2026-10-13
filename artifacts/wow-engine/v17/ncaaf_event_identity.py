"""Canonical NCAAF current-event reconciliation for V17 Free-Core.

ESPN discovery ids remain provider aliases. WOW assigns the canonical event id
only after the existing CFBD /games source proves a unique participant + start
match. This mirrors the source identity already persisted in
wow_ncaaf_training_games.

No market price, sporting probability, calibration, ranking or execution
authority exists in this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
import unicodedata
from typing import Any, Mapping

from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable

CAN_EXECUTE = False
SOURCE_PROVIDER = "CFBD:/games"
START_TOLERANCE_MINUTES = 60.0
_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[int, tuple[float, list[Mapping[str, Any]]]] = {}


class NCAAFEventIdentityError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


# CFBD may use abbreviated official school names while ESPN adds a mascot.
# Broad prefixes for 3-4 character names are unsafe (e.g. Iowa vs Iowa State),
# so accept short names only when the complete known school + mascot identity
# is present. This is identity matching only; it is not a sporting feature.
_VERIFIED_SHORT_SCHOOL_MASCOTS: dict[str, frozenset[str]] = {
    "iowa": frozenset({"iowahawkeyes"}),
    "byu": frozenset({"byucougars"}),
}


def _name_match(provider_name: Any, canonical_name: Any) -> bool:
    left = _norm(provider_name)
    right = _norm(canonical_name)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    # CFBD lists the school while ESPN commonly appends the mascot. Short
    # schools require explicit verified aliases to avoid prefix collisions.
    if len(shorter) < 5:
        return longer in _VERIFIED_SHORT_SCHOOL_MASCOTS.get(shorter, ())
    return longer.startswith(shorter)


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NCAAFEventIdentityError("NCAAF_EVENT_TIME_INVALID", str(value)) from exc
    if parsed.utcoffset() is None:
        raise NCAAFEventIdentityError("NCAAF_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _start_value(row: Mapping[str, Any]) -> Any:
    return (
        row.get("startDate")
        or row.get("start_date")
        or row.get("startTime")
        or row.get("start_time")
        or row.get("date")
    )


def _home_name(row: Mapping[str, Any]) -> Any:
    return row.get("homeTeam") or row.get("home_team")


def _away_name(row: Mapping[str, Any]) -> Any:
    return row.get("awayTeam") or row.get("away_team")


def _season_rows(
    year: int,
    *,
    client: CFBDClient | None = None,
    now_monotonic: float | None = None,
) -> list[Mapping[str, Any]]:
    if client is not None:
        return list(client.games(year=year, classification="fbs").rows)

    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _CACHE_LOCK:
        cached = _CACHE.get(int(year))
        if cached is not None and (now_value - cached[0]) <= _CACHE_TTL_SECONDS:
            return list(cached[1])

    try:
        rows = list(CFBDClient.from_environment().games(
            year=int(year),
            classification="fbs",
        ).rows)
    except CFBDUnavailable as exc:
        code = str(getattr(exc, "code", None) or "CFBD_UNAVAILABLE")
        raise NCAAFEventIdentityError(code, str(exc)) from exc

    with _CACHE_LOCK:
        _CACHE[int(year)] = (now_value, list(rows))
    return rows


def resolve_ncaaf_current_event_identity(
    *,
    event_start_time: str,
    home_team: str,
    away_team: str,
    client: CFBDClient | None = None,
) -> dict[str, Any]:
    event_time = _aware(event_start_time)
    rows = _season_rows(event_time.year, client=client)

    matches: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_id = str(row.get("id") or row.get("event_id") or "").strip()
        if not event_id:
            continue
        if not _name_match(home_team, _home_name(row)):
            continue
        if not _name_match(away_team, _away_name(row)):
            continue
        try:
            start = _aware(_start_value(row))
        except NCAAFEventIdentityError:
            continue
        delta = abs((event_time - start).total_seconds()) / 60.0
        if delta <= START_TOLERANCE_MINUTES:
            matches.append(row)

    ids = {
        str(row.get("id") or row.get("event_id") or "").strip()
        for row in matches
        if str(row.get("id") or row.get("event_id") or "").strip()
    }
    if not ids:
        raise NCAAFEventIdentityError("NCAAF_CANONICAL_EVENT_NOT_FOUND")
    if len(ids) != 1:
        raise NCAAFEventIdentityError("NCAAF_CANONICAL_EVENT_AMBIGUOUS")

    selected_id = next(iter(ids))
    selected = next(
        row
        for row in matches
        if str(row.get("id") or row.get("event_id") or "").strip() == selected_id
    )
    return {
        "event_id": selected_id,
        "event_start_time": _aware(_start_value(selected)).isoformat(),
        "home_team": str(_home_name(selected) or ""),
        "away_team": str(_away_name(selected) or ""),
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = [
    "CAN_EXECUTE",
    "NCAAFEventIdentityError",
    "SOURCE_PROVIDER",
    "resolve_ncaaf_current_event_identity",
    "reset_cache",
]
