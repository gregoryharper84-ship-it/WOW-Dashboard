"""Nonpublishing NFL week inventory bridge from *independently authenticated* sources.

The trusted caller must authenticate the nflverse bytes, ledger SHA256, capture
timestamp, and separate official kickoff/status evidence BEFORE passing them.
This module checks their consistency; booleans/strings here are not credentials,
provider authentication or authorization. It never fetches, scores or publishes.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from v17.cross_sport_acceptance_factory import Fixture, SourceInventory, SourceWeekWindow

SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
REQUIRED_FIELDS = frozenset({
    "game_id", "season", "game_type", "week", "gameday",
    "home_team", "away_team", "home_score", "away_score",
})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PREGAME = frozenset({"SCHEDULED", "PRE_GAME"})
STATES = PREGAME | frozenset({
    "FINAL", "IN_PROGRESS", "CANCELLED", "CANCELED", "POSTPONED",
})


class NflWeekInventoryHold(ValueError):
    """Typed refusal; never translate a failure into a partial week."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedScheduleCapture:
    source_id: str
    source_url: str
    content_sha256: str
    fetched_at: datetime
    # Must originate from the trusted acquisition boundary, not an API caller.
    authenticated_https: bool


@dataclass(frozen=True)
class VerifiedKickoff:
    game_id: str
    season: int
    week: int
    game_type: str
    start_utc: datetime
    status: str
    home_team: str
    away_team: str


@dataclass(frozen=True)
class VerifiedWeek:
    season: int
    week: int
    game_type: str
    start_utc: datetime
    end_utc: datetime


def _aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _int(value: Any, code: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(str(value).strip())
    except (ValueError, TypeError) as exc:
        raise NflWeekInventoryHold(code) from exc


def _game_key(season: int, week: int, game_type: str) -> tuple[int, int, str]:
    if not (2000 <= season <= 2100 and 1 <= week <= 23 and re.fullmatch(r"[A-Z]{2,4}", game_type)):
        raise NflWeekInventoryHold("NFL_WEEK_DECLARED_IDENTITY_INVALID")
    return season, week, game_type


def build_verified_nfl_week_inventory(
    schedule_csv: bytes,
    *,
    capture: VerifiedScheduleCapture,
    kickoff_evidence: Mapping[str, VerifiedKickoff],
    declared_weeks: tuple[VerifiedWeek, ...],
    trusted_now: datetime,
) -> tuple[SourceInventory, tuple[SourceWeekWindow, ...]]:
    """Build strict cross-checked evidence for #1718's week-selection preflight.

    No synthetic kickoff from gameday/gametime, no alias->official ID promotion,
    no missing fixture exclusion, no model/terminal or release certification.
    """
    if not _aware(trusted_now):
        raise NflWeekInventoryHold("NFL_WEEK_TRUSTED_CLOCK_REQUIRED")
    if (
        not isinstance(capture, VerifiedScheduleCapture)
        or capture.authenticated_https is not True
        or capture.source_url != SCHEDULE_URL
        or not isinstance(capture.source_id, str)
        or not capture.source_id.strip()
        or not _aware(capture.fetched_at)
    ):
        raise NflWeekInventoryHold("NFL_WEEK_AUTHENTICATED_CAPTURE_REQUIRED")
    if not timedelta(0) <= trusted_now - capture.fetched_at <= timedelta(hours=6):
        raise NflWeekInventoryHold("NFL_WEEK_SOURCE_STALE_OR_FUTURE")
    if (
        not isinstance(schedule_csv, bytes) or not schedule_csv
        or not isinstance(capture.content_sha256, str)
        or not SHA256_RE.fullmatch(capture.content_sha256)
        or not hmac.compare_digest(hashlib.sha256(schedule_csv).hexdigest(), capture.content_sha256)
    ):
        raise NflWeekInventoryHold("NFL_WEEK_SOURCE_DIGEST_UNVERIFIED")
    if not isinstance(declared_weeks, tuple) or not 1 <= len(declared_weeks) <= 3:
        raise NflWeekInventoryHold("NFL_WEEK_DECLARED_WINDOWS_INVALID")
    if not isinstance(kickoff_evidence, Mapping):
        raise NflWeekInventoryHold("NFL_WEEK_KICKOFF_EVIDENCE_INVALID")

    keyed_weeks: dict[tuple[int, int, str], VerifiedWeek] = {}
    result_weeks: list[SourceWeekWindow] = []
    for week in declared_weeks:
        if not isinstance(week, VerifiedWeek):
            raise NflWeekInventoryHold("NFL_WEEK_DECLARED_WINDOWS_INVALID")
        key = _game_key(week.season, week.week, week.game_type)
        if key in keyed_weeks:
            raise NflWeekInventoryHold("NFL_WEEK_DUPLICATE_DECLARED_WEEK")
        if (
            not _aware(week.start_utc) or not _aware(week.end_utc)
            or not timedelta(0) < week.end_utc - week.start_utc <= timedelta(days=9)
        ):
            raise NflWeekInventoryHold("NFL_WEEK_DECLARED_WINDOW_TIME_INVALID")
        keyed_weeks[key] = week
        result_weeks.append(SourceWeekWindow(
            sport="NFL", season_key=str(week.season),
            week_key=f"{week.game_type}:{week.week:02d}",
            window_start=week.start_utc, window_end=week.end_utc,
        ))
    if any(
        a.start_utc < b.end_utc and b.start_utc < a.end_utc
        for i, a in enumerate(declared_weeks) for b in declared_weeks[i + 1:]
    ):
        raise NflWeekInventoryHold("NFL_WEEK_DECLARED_WINDOWS_OVERLAP")

    try:
        decoded = schedule_csv.decode("utf-8-sig", errors="strict")
        reader = csv.DictReader(io.StringIO(decoded))
        if not reader.fieldnames or not REQUIRED_FIELDS.issubset(reader.fieldnames):
            raise NflWeekInventoryHold("NFL_WEEK_SCHEDULE_SCHEMA_INVALID")
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise NflWeekInventoryHold("NFL_WEEK_SCHEDULE_BYTES_INVALID") from exc

    selected: dict[str, dict[str, str]] = {}
    for row in rows:
        try:
            year = _int(row.get("season"), "NFL_WEEK_SCHEDULE_SEASON_INVALID")
            number = _int(row.get("week"), "NFL_WEEK_SCHEDULE_WEEK_INVALID")
        except NflWeekInventoryHold:
            # Historical / out-of-window damaged rows cannot certify a fixture.
            continue
        game_type = str(row.get("game_type") or "").strip().upper()
        key = (year, number, game_type)
        if key not in keyed_weeks:
            continue
        game_id = str(row.get("game_id") or "").strip()
        if not game_id or game_id in selected:
            raise NflWeekInventoryHold("NFL_WEEK_DUPLICATE_OR_MISSING_CANONICAL_ID")
        selected[game_id] = row
    if not selected:
        raise NflWeekInventoryHold("NFL_WEEK_CANONICAL_SCHEDULE_EMPTY")
    if set(kickoff_evidence) != set(selected):
        raise NflWeekInventoryHold("NFL_WEEK_KICKOFF_INVENTORY_INCOMPLETE_OR_EXTRA")
    fixtures: list[Fixture] = []
    for game_id, row in selected.items():
        verified = kickoff_evidence[game_id]
        key = (_int(row.get("season"), "NFL_WEEK_SCHEDULE_SEASON_INVALID"),
               _int(row.get("week"), "NFL_WEEK_SCHEDULE_WEEK_INVALID"),
               str(row.get("game_type") or "").strip().upper())
        if not isinstance(verified, VerifiedKickoff) or (
            verified.game_id != game_id
            or (verified.season, verified.week, verified.game_type) != key
            or str(row.get("home_team") or "").strip().upper() != verified.home_team
            or str(row.get("away_team") or "").strip().upper() != verified.away_team
            or verified.home_team == verified.away_team
        ):
            raise NflWeekInventoryHold("NFL_WEEK_KICKOFF_CANONICAL_IDENTITY_CONFLICT")
        if not _aware(verified.start_utc) or verified.status not in STATES:
            raise NflWeekInventoryHold("NFL_WEEK_KICKOFF_TIME_OR_STATUS_UNVERIFIED")
        if not keyed_weeks[key].start_utc <= verified.start_utc < keyed_weeks[key].end_utc:
            raise NflWeekInventoryHold("NFL_WEEK_KICKOFF_OUTSIDE_DECLARED_WEEK")
        try:
            game_day = date.fromisoformat(str(row.get("gameday") or "")[:10])
        except ValueError as exc:
            raise NflWeekInventoryHold("NFL_WEEK_CANONICAL_GAMEDAY_INVALID") from exc
        if verified.start_utc.astimezone(ZoneInfo("America/New_York")).date() != game_day:
            raise NflWeekInventoryHold("NFL_WEEK_GAMEDAY_KICKOFF_CONFLICT")
        scored = any(str(row.get(k) or "").strip() for k in ("home_score", "away_score"))
        if scored and verified.status in PREGAME:
            raise NflWeekInventoryHold("NFL_WEEK_SCORE_VS_PREGAME_CONFLICT")
        fixtures.append(Fixture("NFL", game_id, verified.start_utc, verified.status))

    return (
        SourceInventory(capture.source_id, capture.content_sha256, capture.fetched_at, tuple(fixtures)),
        tuple(result_weeks),
    )
