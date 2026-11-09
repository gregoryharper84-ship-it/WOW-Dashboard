"""Forward (pre-lock) NBA/WNBA pregame feature capture.

Root cause this addresses: basketball_training_replay.persist_feature_replay writes
retrospective rows (as_of = game date 00:00 UTC, upsert on game_id), so no stored row
can prove the features existed before the event. This module builds a capture record
only when the capture time is strictly before the official event start, the event is
still scheduled, the source snapshot predates capture, and history is fresh. Records
are append-only (see migrations/20261109_basketball_forward_pregame_capture.sql); a
changed feature set is a new record that names the one it supersedes.

Pure function: no DB, no network, no probability, no promotion. can_execute=False.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from basketball_team_event_specialist import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MIN_PRIOR_GAMES, TrainingGame, build_pregame_features, _sport,
)

can_execute: bool = False
PROBABILITY_PUBLISHABLE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAPTURE_VERSION = "BASKETBALL_FORWARD_PREGAME_CAPTURE_V1"
MAX_HISTORY_AGE_DAYS = 21
SCHEDULED_STATUSES = frozenset({"scheduled", "pre", "pregame", "not_started"})


class ForwardCaptureError(ValueError):
    """Typed fail-closed capture failure; `code` is the native failure code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code


def _utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ForwardCaptureError("CAPTURE_TIMESTAMP_UNPARSEABLE", field) from exc
    if dt.tzinfo is None:
        raise ForwardCaptureError("CAPTURE_TIMESTAMP_NOT_TZ_AWARE", field)
    return dt.astimezone(timezone.utc)


def _canon(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha(obj: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()


@dataclass(frozen=True)
class ForwardCaptureRecord:
    sport: str
    official_event_id: str
    event_start_time: str
    event_status: str
    captured_at: str
    source_snapshot_id: str
    source_snapshot_timestamp: str
    source_payload_sha256: str
    feature_schema_version: str
    feature_payload: dict
    feature_snapshot_sha256: str
    history_through_date: str
    supersedes_feature_snapshot_sha256: str | None
    capture_version: str = CAPTURE_VERSION
    can_execute: bool = False
    probability_publishable: bool = False

    def as_row(self) -> dict[str, Any]:
        return dict(self.__dict__)


def capture_forward_pregame(
    sport: str,
    event: Mapping[str, Any],
    history: Iterable[TrainingGame],
    captured_at: Any,
    *,
    prior_feature_snapshot_sha256: str | None = None,
    max_history_age_days: int = MAX_HISTORY_AGE_DAYS,
) -> ForwardCaptureRecord:
    s = _sport(sport)
    required = ("official_event_id", "home_team_id", "away_team_id", "event_start_time", "event_status",
                "game_date", "source_snapshot_id", "source_snapshot_timestamp", "source_payload_sha256")
    missing = [k for k in required if not str(event.get(k) or "").strip()]
    if missing:
        raise ForwardCaptureError("CAPTURE_EVENT_IDENTITY_INCOMPLETE", ",".join(missing))
    if str(event["home_team_id"]) == str(event["away_team_id"]):
        raise ForwardCaptureError("CAPTURE_EVENT_IDENTITY_INCOMPLETE", "home_equals_away")
    start, cap, snap = _utc(event["event_start_time"], "event_start_time"), _utc(captured_at, "captured_at"), \
        _utc(event["source_snapshot_timestamp"], "source_snapshot_timestamp")
    if str(event["event_status"]).strip().lower() not in SCHEDULED_STATUSES:
        raise ForwardCaptureError("CAPTURE_EVENT_NOT_SCHEDULED", str(event["event_status"]))
    if cap >= start:
        raise ForwardCaptureError("CAPTURE_AT_OR_AFTER_EVENT_START")
    if snap > cap:
        raise ForwardCaptureError("CAPTURE_SOURCE_SNAPSHOT_AFTER_CAPTURE")
    game_date = date.fromisoformat(str(event["game_date"])[:10])
    hist = sorted((g for g in history if g.sport == s and g.game_date < game_date), key=lambda g: (g.game_date, g.game_id))
    if not hist:
        raise ForwardCaptureError("CAPTURE_HISTORY_EMPTY")
    through = hist[-1].game_date
    if game_date - through > timedelta(days=max_history_age_days):
        raise ForwardCaptureError("CAPTURE_HISTORY_STALE", f"latest={through.isoformat()}")
    # The retrospective builder is reused so forward and training features cannot diverge;
    # the placeholder target never influences its own features (history is strictly earlier).
    placeholder = TrainingGame(str(event["official_event_id"]), s, game_date.year, game_date,
                               str(event["home_team_id"]), str(event["away_team_id"]), 1, 0)
    rows = [r for r in build_pregame_features([*hist, placeholder], s) if r.game_id == placeholder.game_id]
    if not rows:
        raise ForwardCaptureError("CAPTURE_INSUFFICIENT_PRIOR_GAMES", f"min={MIN_PRIOR_GAMES}")
    row = rows[0]
    payload = {
        "sport": s, "official_event_id": placeholder.game_id, "game_date": game_date.isoformat(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "home_games_prior": row.home_games_prior, "away_games_prior": row.away_games_prior,
        "features": {n: float(v) for n, v in zip(FEATURE_NAMES, row.values)},
    }
    digest = _sha(payload)
    return ForwardCaptureRecord(
        sport=s, official_event_id=placeholder.game_id, event_start_time=start.isoformat(),
        event_status=str(event["event_status"]).strip().lower(), captured_at=cap.isoformat(),
        source_snapshot_id=str(event["source_snapshot_id"]), source_snapshot_timestamp=snap.isoformat(),
        source_payload_sha256=str(event["source_payload_sha256"]), feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_payload=payload, feature_snapshot_sha256=digest, history_through_date=through.isoformat(),
        supersedes_feature_snapshot_sha256=(prior_feature_snapshot_sha256 if prior_feature_snapshot_sha256 and prior_feature_snapshot_sha256 != digest else None),
    )
