"""Research-only pregame *source capture* for NBA and NHL V17 work.

The derivation and verification modules in this package require server-owned
pregame evidence, but nothing in the repository actually reads a first-party
source and freezes what it published. This module closes that acquisition gap
without inventing anything:

- NBA: reads the public ESPN scoreboard + game summary for one event, proves it
  is genuinely pregame, and freezes event identity, the published injury report,
  the last-five completed margins and rest days. ESPN does *not* publish a
  projected rotation, player values, shot-quality profile, pace or travel
  distance pregame, so the derived NBA context vector still holds closed and
  the receipt names exactly which required inputs the source lacks.
- NHL: reads the official gamecenter landing payload and freezes every published
  goalie candidate for the exact event. It only claims a CONFIRMED starter when
  the source itself marks one; today's pregame payload does not, so the receipt
  stays at candidate evidence rather than manufacturing a confirmation.

Every capture is an immutable receipt: source URLs, HTTP ``Date``, wire-payload
SHA-256, capture time, typed blockers and a self-digest. Captures never fit,
calibrate, rank, publish, persist to a governed table, or execute anything.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from v17.nba_pregame_context_features import derive_nba_pregame_context_features
from v17.nhl_starting_goalie_evidence import (
    GAMECENTER_PREFIX,
    SOURCE_PROVIDER as NHL_SOURCE_PROVIDER,
    verify_nhl_starting_goalie_evidence,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_ONLY = True
FITTED_MODEL = False
CAPTURE_SCHEMA = "wow.v17.pregame_source_capture.v1"
CAPTURE_USER_AGENT = "WOW-V17-pregame-capture/1.0 (research-only; can_execute=false)"
CAPTURE_TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 8_000_000
MIN_MARGIN_OBSERVATIONS = 5
PREGAME_STATES = frozenset({"pre", "fut", "scheduled"})
NBA_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
NBA_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}"
)
NHL_LANDING_URL = GAMECENTER_PREFIX + "{event_id}/landing"
NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"
STATUS_COMPLETE = "CAPTURED_PREGAME_EVIDENCE_COMPLETE"
STATUS_INSUFFICIENT = "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
STATUS_CANDIDATES = "CAPTURED_GOALIE_CANDIDATES_ONLY"
STATUS_STARTERS_CONFIRMED = "CAPTURED_GOALIE_STARTERS_CONFIRMED"
STATUS_STARTERS_INCOMPLETE = "CAPTURED_GOALIE_EVIDENCE_INCOMPLETE"
STATUS_VERIFIED = "VERIFIED_RESEARCH_CAPTURE"
ALLOWED_CAPTURE_STATUSES = frozenset(
    {
        STATUS_COMPLETE,
        STATUS_INSUFFICIENT,
        STATUS_CANDIDATES,
        STATUS_STARTERS_CONFIRMED,
        STATUS_STARTERS_INCOMPLETE,
    }
)
_SIDES = ("home", "away")
_ESPN_EVENT_ID = re.compile(r"[0-9]{6,12}")
_ESPN_TEAM_ID = re.compile(r"[0-9]{1,12}")
_NHL_EVENT_ID = re.compile(r"[0-9]{10}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class CaptureTransportError(RuntimeError):
    """A first-party source could not be read; callers fail closed."""


@dataclass(frozen=True)
class SourceFetch:
    """One first-party HTTP response, frozen at receipt time."""

    url: str
    status: int
    received_at_utc: datetime
    body: bytes
    http_date: str | None = None
    content_type: str | None = None

    def payload(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CaptureTransportError(f"non-JSON source payload from {self.url}") from exc

    def payload_sha256(self) -> str:
        return sha256(self.body).hexdigest()


def fetch_source(
    url: str, *, timeout: float = CAPTURE_TIMEOUT_SECONDS
) -> SourceFetch:
    """Read one first-party JSON endpoint with a bounded, identified request."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": CAPTURE_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = int(getattr(response, "status", 0) or 0)
            headers = response.headers
            received = datetime.now(timezone.utc)
    except urllib.error.HTTPError as exc:
        raise CaptureTransportError(f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CaptureTransportError(f"transport failure for {url}: {exc}") from exc
    if status != 200:
        raise CaptureTransportError(f"HTTP {status} for {url}")
    if len(body) > MAX_RESPONSE_BYTES:
        raise CaptureTransportError(f"response exceeds capture bound for {url}")
    return SourceFetch(
        url=url,
        status=status,
        received_at_utc=received,
        body=body,
        http_date=headers.get("Date") if headers is not None else None,
        content_type=headers.get("Content-Type") if headers is not None else None,
    )


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _require_aware(value: Any) -> datetime:
    parsed = _aware(value if isinstance(value, str) else None)
    if parsed is None:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("now must be a timezone-aware datetime")
        parsed = value.astimezone(timezone.utc)
    return parsed


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _score(value: Any) -> int | None:
    """ESPN emits completed scores as decimal strings; accept both shapes."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _seal(receipt: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(receipt)
    sealed.pop("capture_sha256", None)
    sealed["capture_sha256"] = _digest(sealed)
    return sealed


def _base_receipt(
    *,
    capture_kind: str,
    sport: str,
    official_event_id: str | None,
    captured_at: datetime,
    status: str,
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "schema": CAPTURE_SCHEMA,
        "capture_kind": capture_kind,
        "sport": sport,
        "official_event_id": official_event_id,
        "captured_at_utc": captured_at.isoformat(),
        "status": status,
        "blockers": sorted(set(blockers)),
        "source_responses": [],
        "research_only": RESEARCH_ONLY,
        "fitted_model": FITTED_MODEL,
        "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "can_execute": False,
    }


def _response_record(fetch: SourceFetch) -> dict[str, Any]:
    return {
        "url": fetch.url,
        "status": fetch.status,
        "http_date": fetch.http_date,
        "content_type": fetch.content_type,
        "payload_sha256": fetch.payload_sha256(),
        "payload_bytes": len(fetch.body),
        "received_at_utc": fetch.received_at_utc.isoformat(),
    }


def _espn_competition(event: Mapping[str, Any]) -> Mapping[str, Any]:
    competitions = event.get("competitions")
    if isinstance(competitions, list) and competitions:
        comp = _mapping(competitions[0])
        if comp is not None:
            return comp
    return {}


def _espn_state(event: Mapping[str, Any], comp: Mapping[str, Any]) -> str:
    for block in (comp.get("status"), event.get("status")):
        status = _mapping(block)
        type_block = _mapping(status.get("type")) if status is not None else None
        state = type_block.get("state") if type_block is not None else None
        if isinstance(state, str) and state.strip():
            return state.strip().lower()
    return ""


def _espn_side(competitors: Any, side: str) -> Mapping[str, Any] | None:
    for item in competitors if isinstance(competitors, list) else []:
        entry = _mapping(item)
        if entry is None:
            continue
        if str(entry.get("homeAway") or "").strip().lower() == side:
            return entry
    return None


def _espn_team(entry: Mapping[str, Any], side: str) -> dict[str, Any] | None:
    team = _mapping(entry.get("team"))
    if team is None:
        return None
    team_id = str(team.get("id") or "").strip()
    abbrev = str(team.get("abbreviation") or "").strip().upper()
    if not _ESPN_TEAM_ID.fullmatch(team_id) or not abbrev:
        return None
    return {
        "team_id": team_id,
        "abbreviation": abbrev,
        "display_name": team.get("displayName"),
        "side": side,
    }


def _nba_rest_days(
    summary: Mapping[str, Any], team_id: str, event_id: str, start: datetime
) -> int | None:
    """Completed-game rest days in the Eastern game calendar (0 means B2B)."""
    latest: datetime | None = None
    for group in summary.get("lastFiveGames") or []:
        block = _mapping(group)
        if block is None:
            continue
        group_team = _mapping(block.get("team")) or {}
        if str(group_team.get("id") or "") != team_id:
            continue
        for item in block.get("events") or []:
            entry = _mapping(item)
            if entry is None or str(entry.get("id") or "") == event_id:
                continue
            played = _aware(entry.get("gameDate"))
            if played is not None and (latest is None or played > latest):
                latest = played
    if latest is None:
        return None
    eastern = ZoneInfo("America/New_York")
    return max(
        (start.astimezone(eastern).date() - latest.astimezone(eastern).date()).days - 1,
        0,
    )


def _nba_injuries(
    summary: Mapping[str, Any],
    team_ids: Mapping[str, str],
    now: datetime,
    blockers: list[str],
) -> dict[str, list[dict[str, Any]]]:
    report: dict[str, list[dict[str, Any]]] = {}
    for block in summary.get("injuries") or []:
        entry = _mapping(block)
        if entry is None:
            continue
        team = _mapping(entry.get("team")) or {}
        team_id = str(team.get("id") or "").strip()
        if team_id not in set(team_ids.values()):
            continue
        items: list[dict[str, Any]] = []
        for raw in entry.get("injuries") or []:
            injury = _mapping(raw)
            if injury is None:
                blockers.append("NBA_CAPTURE_INJURY_ENTRY_INVALID")
                continue
            athlete = _mapping(injury.get("athlete")) or {}
            name = str(athlete.get("displayName") or "").strip()
            status = str(injury.get("status") or "").strip()
            observed = _aware(injury.get("date"))
            if not name or not status:
                blockers.append("NBA_CAPTURE_INJURY_ENTRY_INVALID")
                continue
            if observed is None:
                blockers.append("NBA_CAPTURE_INJURY_TIMESTAMP_INVALID")
                continue
            if observed > now:
                blockers.append("NBA_CAPTURE_INJURY_TIMESTAMP_IN_FUTURE")
            items.append(
                {
                    "player": name,
                    "player_id": str(athlete.get("id") or "").strip() or None,
                    "status": status,
                    "observed_at": observed.isoformat(),
                }
            )
        report[team_id] = sorted(items, key=lambda item: item["player"])
    return report


def _nba_margins(
    summary: Mapping[str, Any],
    team_ids: Mapping[str, str],
    event_id: str,
    blockers: list[str],
) -> dict[str, list[float]]:
    groups = {
        str((_mapping(group) or {}).get("team", {}).get("id") or ""): _mapping(group)
        for group in summary.get("lastFiveGames") or []
    }
    margins: dict[str, list[float]] = {}
    for side in _SIDES:
        team_id = team_ids.get(side)
        group = groups.get(str(team_id)) if team_id else None
        events: list[Mapping[str, Any]] = []
        for raw in (group or {}).get("events") or []:
            item = _mapping(raw)
            if item is not None and str(item.get("id") or "") != event_id:
                events.append(item)
        events = events[:MIN_MARGIN_OBSERVATIONS]
        if len(events) < MIN_MARGIN_OBSERVATIONS:
            blockers.append(f"NBA_CAPTURE_{side.upper()}_MARGIN_HISTORY_INSUFFICIENT")
            continue
        values: list[float] = []
        for item in events:
            home_score = _score(item.get("homeTeamScore"))
            away_score = _score(item.get("awayTeamScore"))
            home_id = str(item.get("homeTeamId") or "").strip()
            away_id = str(item.get("awayTeamId") or "").strip()
            if (
                home_score is None
                or away_score is None
                or not home_id
                or not away_id
                or home_id == away_id
            ):
                values = []
                break
            if home_id == team_id:
                values.append(float(home_score - away_score))
            elif away_id == team_id:
                values.append(float(away_score - home_score))
            else:
                values = []
                break
        if len(values) != MIN_MARGIN_OBSERVATIONS:
            blockers.append(f"NBA_CAPTURE_{side.upper()}_MARGIN_HISTORY_INVALID")
            continue
        margins[side] = values
    return margins


def capture_nba_pregame(
    event_id: str,
    *,
    now: datetime,
    fetcher: Callable[[str], SourceFetch] = fetch_source,
    scoreboard_url: str = NBA_SCOREBOARD_URL,
    summary_url: str = NBA_SUMMARY_URL,
) -> dict[str, Any]:
    """Capture genuine pregame NBA evidence for one ESPN event.

    The returned receipt is research evidence only. It never asserts that the
    fitted basketball model has a projection for this event.
    """
    captured_at = _require_aware(now)
    provider_id = str(event_id).strip()
    if not _ESPN_EVENT_ID.fullmatch(provider_id):
        raise ValueError("event_id must be an ESPN numeric event id")
    official_id = f"espn-{provider_id}"

    blockers: list[str] = []
    opens = [scoreboard_url, summary_url.format(event_id=provider_id)]
    fetches: list[SourceFetch] = []
    try:
        board_fetch = fetcher(opens[0])
        board = board_fetch.payload()
        summary_fetch = fetcher(opens[1])
        summary = summary_fetch.payload()
    except CaptureTransportError:
        receipt = _base_receipt(
            capture_kind="NBA_PREGAME",
            sport="NBA",
            official_event_id=official_id,
            captured_at=captured_at,
            status=STATUS_INSUFFICIENT,
            blockers=["PREGAME_CAPTURE_SOURCE_UNAVAILABLE"],
        )
        receipt["source_responses"] = [_response_record(f) for f in fetches]
        return _seal(receipt)
    fetches = [board_fetch, summary_fetch]
    captured_at = max(captured_at, board_fetch.received_at_utc, summary_fetch.received_at_utc)

    board_map = _mapping(board) or {}
    summary_map = _mapping(summary) or {}
    event = next(
        (
            entry
            for entry in board_map.get("events") or []
            if str((_mapping(entry) or {}).get("id") or "") == provider_id
        ),
        None,
    )
    receipt = _base_receipt(
        capture_kind="NBA_PREGAME",
        sport="NBA",
        official_event_id=official_id,
        captured_at=captured_at,
        status=STATUS_INSUFFICIENT,
        blockers=blockers,
    )
    receipt["provider_event_id"] = provider_id
    receipt["source_responses"] = [_response_record(f) for f in fetches]
    event_map = _mapping(event)
    if event_map is None:
        blockers.append("NBA_CAPTURE_EVENT_NOT_FOUND")
        receipt["blockers"] = sorted(blockers)
        return _seal(receipt)

    comp = _espn_competition(event_map)
    state = _espn_state(event_map, comp)
    start = _aware(event_map.get("date")) or _aware(comp.get("date"))
    home = _espn_team(_espn_side(comp.get("competitors"), "home") or {}, "home")
    away = _espn_team(_espn_side(comp.get("competitors"), "away") or {}, "away")
    venue = _mapping(comp.get("venue")) or {}
    receipt["event"] = {
        "start_time_utc": start.isoformat() if start is not None else None,
        "state": state.upper() or None,
        "home": home,
        "away": away,
        "venue": venue.get("fullName"),
    }
    if state not in PREGAME_STATES:
        blockers.append("NBA_CAPTURE_EVENT_NOT_PREGAME")
    if start is None:
        blockers.append("NBA_CAPTURE_EVENT_TIME_INVALID")
    elif start <= captured_at:
        blockers.append("NBA_CAPTURE_KICKOFF_NOT_FUTURE")
    if home is None or away is None:
        blockers.append("NBA_CAPTURE_TEAM_IDENTITY_INVALID")
    elif home["team_id"] == away["team_id"]:
        blockers.append("NBA_CAPTURE_DUPLICATE_OPPONENT_IDENTITY")
    if blockers:
        receipt["blockers"] = sorted(set(blockers))
        return _seal(receipt)

    team_ids = {"home": home["team_id"], "away": away["team_id"]}
    header_comp = _espn_competition(
        {"competitions": (_mapping(summary_map.get("header")) or {}).get("competitions")}
    )
    header_start = _aware(header_comp.get("date"))
    summary_ids = {
        str((_mapping(_espn_side(header_comp.get("competitors"), side)) or {}).get("id") or "")
        for side in _SIDES
    }
    if (
        header_start is None
        or abs((header_start - start).total_seconds()) > 60
        or summary_ids != set(team_ids.values())
        or str(header_comp.get("id") or provider_id) != provider_id
    ):
        blockers.append("NBA_CAPTURE_SOURCE_IDENTITY_MISMATCH")

    injuries = _nba_injuries(summary_map, team_ids, captured_at, blockers)
    margins = _nba_margins(summary_map, team_ids, provider_id, blockers)
    rest_days = {
        side: _nba_rest_days(summary_map, team_ids[side], provider_id, start)
        for side in _SIDES
    }

    evidence: dict[str, Any] = {
        "sport": "NBA",
        "official_event_id": official_id,
        "home_team_id": f"espn-{team_ids['home']}",
        "away_team_id": f"espn-{team_ids['away']}",
        "event_start_time_utc": start.isoformat(),
        "source_snapshot_id": "sha256:" + _digest(
            [record["payload_sha256"] for record in receipt["source_responses"]]
        ),
        "source_snapshot_timestamp": max(
            fetch.received_at_utc for fetch in fetches
        ).isoformat(),
    }
    if set(margins) == set(_SIDES):
        evidence["recent_margins"] = margins

    derivation = derive_nba_pregame_context_features(evidence, as_of_utc=captured_at)
    receipt["evidence_package"] = evidence
    receipt["injury_report"] = injuries
    receipt["rest_days"] = rest_days
    receipt["available_evidence"] = sorted(
        [
            "event_identity",
            "event_start_time",
            "event_state",
            "injury_report",
            "last_five_completed_games",
            "rest_days",
        ]
        + (["recent_margins"] if "recent_margins" in evidence else [])
    )
    receipt["missing_required_inputs"] = list(derivation["blockers"])
    receipt["derivation"] = {
        "status": derivation["status"],
        "blockers": list(derivation["blockers"]),
        "features_available": derivation["features"] is not None,
        "calibration_status": derivation["calibration_status"],
    }
    receipt["status"] = (
        STATUS_COMPLETE if derivation["status"] == "PASS" else STATUS_INSUFFICIENT
    )
    blockers = blockers + list(derivation["blockers"])
    receipt["blockers"] = sorted(set(blockers))
    return _seal(receipt)


def _nhl_candidates(team_block: Any, blockers: list[str], side: str) -> list[dict[str, Any]]:
    block = _mapping(team_block) or {}
    candidates: list[dict[str, Any]] = []
    for raw in block.get("leaders") or []:
        item = _mapping(raw)
        if item is None:
            continue
        player_id = item.get("playerId")
        if isinstance(player_id, bool) or not isinstance(player_id, int) or player_id <= 0:
            blockers.append(f"NHL_CAPTURE_{side.upper()}_GOALIE_ID_INVALID")
            continue
        name_block = _mapping(item.get("name")) or {}
        explicit = False
        for key in ("starting", "isStarting", "starter", "startingGoalie"):
            if key in item:
                if item.get(key) is True:
                    explicit = True
                elif item.get(key) is not False:
                    blockers.append(f"NHL_CAPTURE_{side.upper()}_STARTER_FLAG_INVALID")
        if str(item.get("status") or "").strip().upper() == "CONFIRMED":
            explicit = True
        candidates.append(
            {
                "goalie_id": str(player_id),
                "name": name_block.get("default"),
                "games_played": item.get("gamesPlayed"),
                "save_pctg": item.get("savePctg"),
                "gaa": item.get("gaa"),
                "record": item.get("record"),
                "explicit_starter": explicit,
            }
        )
    return candidates


def _nhl_identity(
    fetch: SourceFetch,
    *,
    event_id: str,
    start: datetime,
    home: str,
    away: str,
    blockers: list[str],
) -> dict[str, Any] | None:
    payload = _mapping(fetch.payload()) or {}
    game = None
    for day in payload.get("gameWeek") or []:
        for raw in (_mapping(day) or {}).get("games") or []:
            entry = _mapping(raw) or {}
            if str(entry.get("id") or "") == event_id:
                game = entry
                break
        if game is not None:
            break
    if game is None:
        blockers.append("NHL_CAPTURE_SCHEDULE_IDENTITY_MISSING")
        return None
    schedule_start = _aware(game.get("startTimeUTC"))
    schedule_home = str((_mapping(game.get("homeTeam")) or {}).get("abbrev") or "").upper()
    schedule_away = str((_mapping(game.get("awayTeam")) or {}).get("abbrev") or "").upper()
    if (
        schedule_start is None
        or abs((schedule_start - start).total_seconds()) > 30 * 60
        or (schedule_home, schedule_away) != (home, away)
    ):
        blockers.append("NHL_CAPTURE_IDENTITY_MISMATCH")
        return None
    return {
        "event_id": event_id,
        "event_start_time": schedule_start.isoformat(),
        "home_team": home,
        "away_team": away,
        "identity_provider": NHL_SOURCE_PROVIDER,
        "identity_source": fetch.url,
        "identity_verified_at": fetch.received_at_utc.isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def capture_nhl_goalie_evidence(
    event_id: str,
    *,
    now: datetime,
    fetcher: Callable[[str], SourceFetch] = fetch_source,
    landing_url: str = NHL_LANDING_URL,
    schedule_url: str = NHL_SCHEDULE_URL,
) -> dict[str, Any]:
    """Capture published NHL goalie identity for one exact gamecenter event."""
    captured_at = _require_aware(now)
    canonical_id = str(event_id).strip()
    if not _NHL_EVENT_ID.fullmatch(canonical_id):
        raise ValueError("event_id must be a 10-digit NHL game id")

    blockers: list[str] = []
    url = landing_url.format(event_id=canonical_id)
    try:
        landing_fetch = fetcher(url)
        landing = _mapping(landing_fetch.payload()) or {}
    except CaptureTransportError:
        receipt = _base_receipt(
            capture_kind="NHL_STARTING_GOALIE",
            sport="NHL",
            official_event_id=canonical_id,
            captured_at=captured_at,
            status=STATUS_CANDIDATES,
            blockers=["PREGAME_CAPTURE_SOURCE_UNAVAILABLE"],
        )
        return _seal(receipt)

    captured_at = max(captured_at, landing_fetch.received_at_utc)
    receipt = _base_receipt(
        capture_kind="NHL_STARTING_GOALIE",
        sport="NHL",
        official_event_id=canonical_id,
        captured_at=captured_at,
        status=STATUS_CANDIDATES,
        blockers=blockers,
    )
    receipt["source_responses"] = [_response_record(landing_fetch)]
    state = str(landing.get("gameState") or "").strip().upper()
    start = _aware(landing.get("startTimeUTC"))
    home_team = _mapping(landing.get("homeTeam")) or {}
    away_team = _mapping(landing.get("awayTeam")) or {}
    home = str(home_team.get("abbrev") or "").strip().upper()
    away = str(away_team.get("abbrev") or "").strip().upper()
    receipt["event"] = {
        "start_time_utc": start.isoformat() if start is not None else None,
        "state": state or None,
        "home_team": home or None,
        "away_team": away or None,
        "venue": (_mapping(landing.get("venue")) or {}).get("default"),
        "season": landing.get("season"),
    }
    if str(landing.get("id") or "") != canonical_id:
        blockers.append("NHL_CAPTURE_EVENT_IDENTITY_MISMATCH")
    if state not in {"FUT", "PRE"}:
        blockers.append("NHL_CAPTURE_EVENT_NOT_PREGAME")
    if start is None:
        blockers.append("NHL_CAPTURE_EVENT_TIME_INVALID")
    elif start <= captured_at:
        blockers.append("NHL_CAPTURE_KICKOFF_NOT_FUTURE")
    if not home or not away:
        blockers.append("NHL_CAPTURE_TEAM_IDENTITY_INVALID")
    elif home == away:
        blockers.append("NHL_CAPTURE_DUPLICATE_OPPONENT_IDENTITY")

    matchup = _mapping(landing.get("matchup")) or {}
    comparison = _mapping(matchup.get("goalieComparison")) or {}
    candidates = {
        "home": _nhl_candidates(comparison.get("homeTeam"), blockers, "home"),
        "away": _nhl_candidates(comparison.get("awayTeam"), blockers, "away"),
    }
    receipt["goalie_candidates"] = candidates
    if blockers:
        receipt["blockers"] = sorted(set(blockers))
        return _seal(receipt)

    explicit = {
        side: [item for item in candidates[side] if item["explicit_starter"]]
        for side in _SIDES
    }
    for side in _SIDES:
        if len(explicit[side]) > 1:
            blockers.append("NHL_CAPTURE_MULTIPLE_CONFIRMED_GOALIES")
    if not any(explicit.values()):
        blockers.append("NHL_CAPTURE_STARTERS_NOT_PUBLISHED")
        receipt["blockers"] = sorted(set(blockers))
        receipt["confirmed_starter_ids"] = {"home": None, "away": None}
        receipt["identity_receipt"] = None
        receipt["goalie_evidence_package"] = None
        receipt["verification"] = None
        receipt["missing_required_inputs"] = sorted(set(blockers))
        receipt["status"] = STATUS_CANDIDATES
        return _seal(receipt)
    if any(not explicit[side] for side in _SIDES):
        blockers.append("NHL_CAPTURE_PARTIAL_STARTER_CONFIRMATION")

    confirmed = {
        side: (explicit[side][0]["goalie_id"] if len(explicit[side]) == 1 else None)
        for side in _SIDES
    }
    receipt["confirmed_starter_ids"] = confirmed
    replacements: dict[str, dict[str, Any]] = {}
    for side in _SIDES:
        others = [item["goalie_id"] for item in candidates[side] if item["goalie_id"] != confirmed[side]]
        if others:
            replacements[side] = {
                "goalie_id": others[0],
                "team": home if side == "home" else away,
                "status": "BACKUP_ROSTERED",
            }
        else:
            blockers.append(f"NHL_CAPTURE_{side.upper()}_REPLACEMENT_UNIDENTIFIED")

    identity_fetch: SourceFetch | None = None
    season = landing.get("season")
    try:
        if isinstance(season, int) and not isinstance(season, bool) and home:
            identity_fetch = fetcher(
                schedule_url.format(team=home, season=str(season))
            )
            receipt["source_responses"].append(_response_record(identity_fetch))
            captured_at = max(captured_at, identity_fetch.received_at_utc)
            receipt["captured_at_utc"] = captured_at.isoformat()
    except CaptureTransportError:
        blockers.append("NHL_CAPTURE_IDENTITY_SOURCE_UNAVAILABLE")
    identity = (
        _nhl_identity(
            identity_fetch,
            event_id=canonical_id,
            start=start,
            home=home,
            away=away,
            blockers=blockers,
        )
        if identity_fetch is not None
        else None
    )
    receipt["identity_receipt"] = identity

    confirmation = {
        "source_provider": NHL_SOURCE_PROVIDER,
        "source_url": url,
        "method": "OFFICIAL_GAMECENTER_STARTER_ANNOUNCEMENT",
        "snapshot_id": "sha256:" + landing_fetch.payload_sha256()[:40],
        "captured_at": landing_fetch.received_at_utc.isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
        "starters": {
            side: {
                "goalie_id": confirmed[side],
                "team": home if side == "home" else away,
                "status": "CONFIRMED",
                "observed_at": landing_fetch.received_at_utc.isoformat(),
            }
            for side in _SIDES
            if confirmed[side] is not None
        },
        "replacements": replacements,
    }
    package = {
        "sport": "NHL",
        "official_event_id": canonical_id,
        "home_team": home,
        "away_team": away,
        "event_start_time_utc": start.isoformat(),
        "source_snapshot_id": "sha256:" + landing_fetch.payload_sha256(),
        "source_snapshot_timestamp": landing_fetch.received_at_utc.isoformat(),
        "confirmation": confirmation,
    }
    verification = verify_nhl_starting_goalie_evidence(
        package, as_of_utc=captured_at, resolved_identity=identity
    )
    receipt["goalie_evidence_package"] = package
    receipt["verification"] = {
        "status": verification["status"],
        "blockers": list(verification["blockers"]),
    }
    receipt["missing_required_inputs"] = list(verification["blockers"])
    blockers.extend(verification["blockers"])
    receipt["status"] = (
        STATUS_STARTERS_CONFIRMED
        if verification["status"] == "GOALIE_EVIDENCE_PASS_NOT_CERTIFICATION"
        else STATUS_STARTERS_INCOMPLETE
    )
    receipt["blockers"] = sorted(set(blockers))
    return _seal(receipt)


def verify_capture_receipt(receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    """Re-derive a stored capture's digest and, where present, its evidence."""
    if not isinstance(receipt, Mapping):
        return {
            "status": "HELD",
            "blockers": ["PREGAME_CAPTURE_RECEIPT_MISSING"],
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    blockers: list[str] = []
    if receipt.get("schema") != CAPTURE_SCHEMA:
        blockers.append("PREGAME_CAPTURE_SCHEMA_MISMATCH")
    body = {key: value for key, value in receipt.items() if key != "capture_sha256"}
    if _digest(body) != receipt.get("capture_sha256"):
        blockers.append("PREGAME_CAPTURE_RECEIPT_DIGEST_MISMATCH")
    if (
        receipt.get("research_only") is not True
        or receipt.get("fitted_model") is not False
        or receipt.get("probability_publishable") is not False
        or receipt.get("rank_eligible") is not False
        or receipt.get("can_execute") is not False
        or receipt.get("calibration_status") != "NOT_CALIBRATED_RESEARCH_ONLY"
    ):
        blockers.append("PREGAME_CAPTURE_AUTHORITY_CLAIMED")
    if receipt.get("status") not in ALLOWED_CAPTURE_STATUSES:
        blockers.append("PREGAME_CAPTURE_STATUS_INVALID")
    responses = receipt.get("source_responses")
    if not isinstance(responses, list) or not responses:
        blockers.append("PREGAME_CAPTURE_PROVENANCE_INVALID")
    else:
        for response in responses:
            record = _mapping(response)
            if (
                record is None
                or not isinstance(record.get("url"), str)
                or not str(record.get("url")).startswith("https://")
                or not isinstance(record.get("payload_sha256"), str)
                or not _SHA256.fullmatch(str(record.get("payload_sha256")))
                or _aware(record.get("received_at_utc")) is None
            ):
                blockers.append("PREGAME_CAPTURE_PROVENANCE_INVALID")
                break

    captured_at = _aware(receipt.get("captured_at_utc"))
    if captured_at is None:
        blockers.append("PREGAME_CAPTURE_TIMESTAMP_INVALID")
    package = _mapping(receipt.get("evidence_package"))
    if receipt.get("capture_kind") == "NBA_PREGAME" and package is not None and captured_at:
        rederived = derive_nba_pregame_context_features(package, as_of_utc=captured_at)
        recorded = _mapping(receipt.get("derivation")) or {}
        if (
            rederived["status"] != recorded.get("status")
            or list(rederived["blockers"]) != list(recorded.get("blockers") or [])
        ):
            blockers.append("PREGAME_CAPTURE_DERIVATION_DRIFT")
    nhl_package = _mapping(receipt.get("goalie_evidence_package"))
    if nhl_package is not None and captured_at:
        reverified = verify_nhl_starting_goalie_evidence(
            nhl_package,
            as_of_utc=captured_at,
            resolved_identity=_mapping(receipt.get("identity_receipt")),
        )
        recorded = _mapping(receipt.get("verification")) or {}
        if (
            reverified["status"] != recorded.get("status")
            or list(reverified["blockers"]) != list(recorded.get("blockers") or [])
        ):
            blockers.append("PREGAME_CAPTURE_VERIFICATION_DRIFT")

    return {
        "status": STATUS_VERIFIED if not blockers else "HELD",
        "blockers": sorted(set(blockers)),
        "capture_sha256": receipt.get("capture_sha256"),
        "official_event_id": receipt.get("official_event_id"),
        "research_only": RESEARCH_ONLY,
        "probability_publishable": False,
        "can_execute": False,
    }


def write_capture_artifact(receipt: Mapping[str, Any], root: str | Path) -> Path:
    """Persist a verified capture as deterministic, content-addressed JSON."""
    verification = verify_capture_receipt(receipt)
    if verification["status"] != STATUS_VERIFIED:
        raise ValueError(
            "capture receipt failed verification: "
            + ",".join(verification["blockers"] or ["UNKNOWN"])
        )
    base = Path(root)
    event_id = str(receipt.get("official_event_id") or "").replace("espn-", "")
    if not event_id:
        raise ValueError("capture receipt has no event identity")
    directory = base / str(receipt.get("sport") or "").lower()
    path = directory / f"{event_id}-{str(receipt.get('capture_sha256'))[:12]}.json"
    payload = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError(f"immutable capture artifact already differs: {path}")
        return path
    path.write_text(payload, encoding="utf-8")
    return path


def _summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": receipt.get("status"),
        "official_event_id": receipt.get("official_event_id"),
        "blockers": receipt.get("blockers"),
        "capture_sha256": receipt.get("capture_sha256"),
        "can_execute": receipt.get("can_execute"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sport", choices=("nba", "nhl"))
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc) if args.now is None else _require_aware(args.now)
    capture = (
        capture_nba_pregame(args.event_id, now=now)
        if args.sport == "nba"
        else capture_nhl_goalie_evidence(args.event_id, now=now)
    )
    path = write_capture_artifact(capture, args.out)
    print(json.dumps({**_summary(capture), "artifact": str(path)}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
