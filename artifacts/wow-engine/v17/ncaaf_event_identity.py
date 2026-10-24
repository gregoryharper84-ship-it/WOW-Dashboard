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
    # Every FBS school whose CFBD name normalizes to fewer than five
    # characters, mapped to its exact ESPN "<school> <mascot>" display name.
    "army": frozenset({"armyblackknights"}),
    "byu": frozenset({"byucougars"}),
    "duke": frozenset({"dukebluedevils"}),
    "iowa": frozenset({"iowahawkeyes"}),
    "lsu": frozenset({"lsutigers"}),
    "navy": frozenset({"navymidshipmen"}),
    "ohio": frozenset({"ohiobobcats"}),
    "rice": frozenset({"riceowls"}),
    "smu": frozenset({"smumustangs"}),
    "tcu": frozenset({"tcuhornedfrogs"}),
    "troy": frozenset({"troytrojans"}),
    "uab": frozenset({"uabblazers"}),
    "ucf": frozenset({"ucfknights"}),
    "ucla": frozenset({"uclabruins"}),
    "unlv": frozenset({"unlvrebels"}),
    "usc": frozenset({"usctrojans"}),
    "utah": frozenset({"utahutes"}),
    "utep": frozenset({"utepminers"}),
    "utsa": frozenset({"utsaroadrunners"}),
}


_SCHOOL_ALIASES = {
    "byu": "brighamyoung",
    "brighamyounguniversity": "brighamyoung",
    "iowast": "iowastate",
    "iastate": "iowastate",
    "iowahawkeyes": "iowa",
}


_ALIAS_MASCOTS = {
    "byu": "cougars",
    "brighamyounguniversity": "cougars",
    "iowast": "cyclones",
    "iastate": "cyclones",
    "iowahawkeyes": "",
}


def _canonical_school_name(value: Any) -> str:
    normalized = _norm(value)
    # Never treat an arbitrary string beginning with an alias as the same
    # school (for example "Iowa Starlings" is not "Iowa State").
    for alias, canonical in _SCHOOL_ALIASES.items():
        if normalized == alias:
            return canonical
        mascot = _ALIAS_MASCOTS[alias]
        if normalized == alias + mascot:
            return canonical + mascot
    return normalized


def _name_match(provider_name: Any, canonical_name: Any) -> bool:
    left = _canonical_school_name(provider_name)
    right = _canonical_school_name(canonical_name)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    # Preserve the independently verified short-school mascot allowlist.
    # Never treat arbitrary extensions of e.g. UCF or Iowa as the same school.
    if len(shorter) < 5:
        return longer in _VERIFIED_SHORT_SCHOOL_MASCOTS.get(shorter, ())
    if not longer.startswith(shorter):
        return False
    # A school prefix can also name a DISTINCT university, not a mascot.
    # These ambiguous cases must hold rather than assign a false CFBD ID.
    qualifiers = (
        "state", "southern", "northern", "eastern", "western",
        "central", "tech", "am", "international", "atlantic",
    )
    suffix = longer[len(shorter):]
    return not any(suffix.startswith(token) for token in qualifiers)


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
    # Do not impute absent/nonboolean CFBD neutralSite; downstream model
    # features must be reconciled to a real source boolean.
    neutral_raw = selected.get("neutralSite", selected.get("neutral_site"))
    neutral_site = neutral_raw if type(neutral_raw) is bool else None
    return {
        "neutral_site": neutral_site,
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
