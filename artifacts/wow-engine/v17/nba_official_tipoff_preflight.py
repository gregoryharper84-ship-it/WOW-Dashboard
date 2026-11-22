"""Bounded, read-only ESPN corroboration of NBA historical scheduled starts.

SportsDataverse's game_date_time may be naive. The settled result/score is
still useful, but an ET guess is not verified scheduled-start evidence.
This preflight correlates a second official ESPN source using event ID,
participants, score, status and an explicit-offset source timestamp.

It never updates stored training rows or certifies model/lineup probability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

import requests

CAN_EXECUTE = False
SOURCE = "ESPN_NBA_SITE_V2_SCOREBOARD"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
MAX_ROWS = 50
MAX_DAYS = 7
_ZONE = ZoneInfo("America/New_York")


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return int(text)


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    game = str(row.get("game_id") or "")
    day = str(row.get("game_date") or "")
    home = str(row.get("home_team_id") or "")
    away = str(row.get("away_team_id") or "")
    if (
        not game.startswith("espn-") or not game[5:].isdigit()
        or not (home.startswith("espn-") and home[5:].isdigit())
        or not (away.startswith("espn-") and away[5:].isdigit())
        or home == away
    ):
        return None
    try:
        if datetime.fromisoformat(day).date().isoformat() != day:
            return None
    except (TypeError, ValueError):
        return None
    return game, day, home, away


def _verify_event(
    stored: Mapping[str, Any], event: Mapping[str, Any], *,
    observed_at: datetime,
) -> tuple[str, str | None]:
    identity = _identity(stored)
    if identity is None:
        return "NBA_STORED_IDENTITY_INVALID", None
    game, day, home_id, away_id = identity
    if str(event.get("id") or "") != game[5:]:
        return "NBA_ESPN_EVENT_ID_MISMATCH", None
    start = _utc(event.get("date"))
    if start is None:
        return "NBA_ESPN_EVENT_TIME_UNVERIFIED", None
    if start >= observed_at or start.astimezone(_ZONE).date().isoformat() != day:
        return "NBA_ESPN_EVENT_CHRONOLOGY_CONFLICT", None

    status = event.get("status") or {}
    state = status.get("type") if isinstance(status, Mapping) else {}
    if not (
        isinstance(state, Mapping) and state.get("completed") is True
        and str(state.get("state") or "").lower() == "post"
    ):
        return "NBA_ESPN_SETTLEMENT_NOT_FINAL", None

    # Untrusted remote JSON must never abort an entire research batch.
    # Fail closed on wrong collection types before indexing nested values.
    comps = event.get("competitions")
    if not isinstance(comps, list) or len(comps) != 1 or not isinstance(comps[0], Mapping):
        return "NBA_ESPN_COMPETITION_AMBIGUOUS", None
    participants = comps[0].get("competitors")
    if not isinstance(participants, list) or len(participants) != 2:
        return "NBA_ESPN_PARTICIPANTS_INVALID", None
    found: dict[str, tuple[str, int]] = {}
    for entry in participants:
        if not isinstance(entry, Mapping):
            return "NBA_ESPN_PARTICIPANTS_INVALID", None
        place = entry.get("homeAway")
        team = entry.get("team")
        if (
            place not in ("home", "away") or place in found
            or not isinstance(team, Mapping)
        ):
            return "NBA_ESPN_PARTICIPANTS_INVALID", None
        raw_team = str(team.get("id") or "")
        score = _score(entry.get("score"))
        if not raw_team.isdigit() or score is None:
            return "NBA_ESPN_PARTICIPANTS_INVALID", None
        found[place] = (f"espn-{raw_team}", score)
    if (
        found.get("home") != (home_id, _score(stored.get("home_score")))
        or found.get("away") != (away_id, _score(stored.get("away_score")))
        or _score(stored.get("home_score")) == _score(stored.get("away_score"))
    ):
        return "NBA_ESPN_TEAMS_OR_SCORE_CONFLICT", None
    if not isinstance(stored.get("home_win"), bool):
        return "NBA_STORED_RESULT_INVALID", None
    if stored["home_win"] != (found["home"][1] > found["away"][1]):
        return "NBA_ESPN_WINNER_CONFLICT", None
    return "NBA_OFFICIAL_TIPOFF_CORROBORATED", start.isoformat()


def _fetch_day(
    day: str, *, http_get: Callable[..., Any],
) -> tuple[list[Mapping[str, Any]], str | None]:
    try:
        result = http_get(
            SCOREBOARD_URL,
            params={"dates": day.replace("-", ""), "limit": 100},
            headers={
                "Accept": "application/json",
                "User-Agent": "WOW-V17-NBA-Research/1.0",
            },
            timeout=12,
        )
        status = int(getattr(result, "status_code", 0))
        if status != 200:
            return [], f"NBA_ESPN_SOURCE_HTTP_{status}"
        payload = result.json()
    except Exception as exc:
        return [], f"NBA_ESPN_SOURCE_UNOBTAINABLE:{type(exc).__name__}"
    if not isinstance(payload, Mapping):
        return [], "NBA_ESPN_SOURCE_INVALID_RESPONSE"
    events = payload.get("events")
    if (
        not isinstance(events, list)
        or len(events) >= 100
        or any(not isinstance(row, Mapping) for row in events)
    ):
        return [], "NBA_ESPN_SOURCE_INVALID_RESPONSE"
    return events, None


def preflight(
    rows: Iterable[Mapping[str, Any]], *,
    now: datetime | None = None,
    http_get: Callable[..., Any] = requests.get,
    max_rows: int = 25,
    max_days: int = 4,
) -> dict[str, Any]:
    """Reconcile all supplied rows with no writes and no synthesized timestamps.

    Bounded first max_rows and max_days are attempted. The remainder are
    explicit DEFERRED_* receipts, never silently omitted. Source evidence is
    observed at this call, not at historical pregame cutoffs.
    """
    if any(type(v) is not int or v < 1 or v > limit for v, limit in (
        (max_rows, MAX_ROWS), (max_days, MAX_DAYS),
    )):
        raise ValueError("NBA_PREFLIGHT_BUDGET_INVALID")
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("NBA_PREFLIGHT_CLOCK_NAIVE")
    current = current.astimezone(timezone.utc)
    supplied = list(rows)
    receipts: list[dict[str, Any]] = []
    keys: set[str] = set()
    day_cache: dict[str, tuple[list[Mapping[str, Any]], str | None]] = {}
    for i, raw in enumerate(supplied):
        if not isinstance(raw, Mapping):
            raise ValueError("NBA_PREFLIGHT_ROW_INVALID")
        game_id = str(raw.get("game_id") or "")
        row = {
            "row_index": i,
            "game_id": game_id,
            "status": "",
            "verified_scheduled_start_utc": None,
            "source": SOURCE,
            "source_observed_at": None,
            "source_payload_sha256": None,
            "model_invoked": False,
            "probability_publishable": False,
            "can_execute": False,
        }
        receipts.append(row)
        if not game_id or game_id in keys:
            row["status"] = "NBA_PREFLIGHT_DUPLICATE_OR_MISSING_ID"
            continue
        keys.add(game_id)
        if raw.get("scheduled_start_utc") is not None:
            row["status"] = "NBA_START_ALREADY_PRESENT"
            continue
        if raw.get("source_provider") != "SPORTSDATAVERSE_ESPN":
            row["status"] = "NBA_PREFLIGHT_PROVIDER_NOT_IN_SCOPE"
            continue
        identity = _identity(raw)
        if identity is None:
            row["status"] = "NBA_STORED_IDENTITY_INVALID"
            continue
        if i >= max_rows:
            row["status"] = "NBA_PREFLIGHT_ROW_BUDGET_DEFERRED"
            continue
        day = identity[1]
        if day not in day_cache:
            if len(day_cache) >= max_days:
                row["status"] = "NBA_PREFLIGHT_DAY_BUDGET_DEFERRED"
                continue
            day_cache[day] = _fetch_day(day, http_get=http_get)
        events, source_error = day_cache[day]
        if source_error:
            row["status"] = source_error
            continue
        matches = [event for event in events if str(event.get("id") or "") == game_id[5:]]
        if len(matches) != 1:
            row["status"] = (
                "NBA_ESPN_EVENT_NOT_FOUND" if not matches
                else "NBA_ESPN_EVENT_AMBIGUOUS"
            )
            continue
        event = matches[0]
        verdict, verified_time = _verify_event(raw, event, observed_at=current)
        row["status"] = verdict
        if verified_time is not None:
            # Deterministic JSON digest: actual source content and observation
            # must remain separately attributable, never rewritten into the
            # older SportsDataverse row's immutable payload hash.
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
            row["verified_scheduled_start_utc"] = verified_time
            row["source_observed_at"] = current.isoformat()
            row["source_payload_sha256"] = sha256(payload.encode()).hexdigest()
    count = len(receipts)
    verified = sum(r["status"] == "NBA_OFFICIAL_TIPOFF_CORROBORATED" for r in receipts)
    deferred = sum("BUDGET_DEFERRED" in r["status"] for r in receipts)
    return {
        "status": "RESEARCH_PREFLIGHT_ONLY",
        "source": SOURCE,
        "rows_in": len(supplied),
        "rows_accounted": count,
        "balanced": count == len(supplied),
        "verified": verified,
        "deferred": deferred,
        "unverified": count - verified - deferred,
        "source_days_attempted": len(day_cache),
        "receipts": receipts,
        "db_writes": 0,
        "model_invoked": False,
        "probability_publishable": False,
        "can_execute": False,
    }
