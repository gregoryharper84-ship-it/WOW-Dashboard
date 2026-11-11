"""Research-only NBA current-pregame feature hydration for forward-shadow evidence.

Builds the exact ``BASKETBALL_TEAM_EVENT_FEATURES_V2`` vector for one upcoming NBA game
from settled NBA history strictly earlier than the game's calendar date AND already
settled at ``source_snapshot_timestamp`` (verifiable settlement instants required), using the same
definitions as training (``basketball_team_event_specialist.build_pregame_features``).

This module is deliberately NOT publication-grade evidence:
  * it never asserts ``RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS`` or a VERIFIED timestamp
    attestation, so the unregistered current-pregame scorer seam still refuses it with
    ``NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED``;
  * it never scores, persists, fetches or publishes a probability;
  * missing or insufficient history returns a typed HOLD, never a guessed feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from basketball_team_event_specialist import (
    BasketballSpecialistError,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MIN_PRIOR_GAMES,
    TrainingGame,
)

CAN_EXECUTE = False
SOURCE_REPLAY_STATUS = "DERIVED_FROM_SETTLED_ROWS_RESEARCH_ONLY"
TIMESTAMP_ATTESTATION = "NOT_VERIFIED"
MAX_REST_DAYS = 7


@dataclass(frozen=True)
class SettledGame:
    """A settled result plus the verifiable UTC instant it became available.

    ``TrainingGame`` carries only a calendar date, which cannot prove a result was known
    at the source snapshot. Callers must supply the settlement instant; without it the
    row cannot be used and the hydrator returns a typed HOLD.
    """

    game: TrainingGame
    settled_at_utc: datetime | None


def _hold(code: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "HOLD",
        "reason_code": code,
        "sport": "NBA",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        **extra,
    }


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _team_history(games: Iterable[TrainingGame], team_id: str, before: date):
    rows = []
    for g in games:
        if g.sport != "NBA":
            continue  # never mix other leagues into NBA history
        if g.game_date >= before:
            continue  # same-day and future finals can never enter pregame features
        if g.home_team_id == team_id:
            rows.append((g.game_date, g.home_score, g.away_score, bool(g.home_win)))
        elif g.away_team_id == team_id:
            rows.append((g.game_date, g.away_score, g.home_score, not bool(g.home_win)))
    rows.sort(key=lambda x: x[0])
    return rows


def current_feature_values(
    games: Iterable[TrainingGame], home_team_id: str, away_team_id: str, game_date: date
) -> tuple[float, ...] | None:
    """Same arithmetic as training; None when either team lacks MIN_PRIOR_GAMES."""
    games = list(games)
    hh = _team_history(games, home_team_id, game_date)
    ah = _team_history(games, away_team_id, game_date)
    if len(hh) < MIN_PRIOR_GAMES or len(ah) < MIN_PRIOR_GAMES:
        return None
    hwr = sum(x[3] for x in hh) / len(hh)
    awr = sum(x[3] for x in ah) / len(ah)
    hpd = sum(x[1] - x[2] for x in hh) / len(hh)
    apd = sum(x[1] - x[2] for x in ah) / len(ah)
    hrest = max(0, (game_date - hh[-1][0]).days - 1)
    arest = max(0, (game_date - ah[-1][0]).days - 1)
    return (
        float(hwr), float(awr), float(hpd), float(apd),
        float(min(hrest, MAX_REST_DAYS)), float(min(arest, MAX_REST_DAYS)),
        float(hrest == 0), float(arest == 0),
    )


def _dataset_id(included: Iterable[SettledGame]) -> str:
    used = sorted(
        (
            r.game.game_id, r.game.game_date.isoformat(), r.game.home_team_id, r.game.away_team_id,
            r.game.home_score, r.game.away_score, r.settled_at_utc.astimezone(timezone.utc).isoformat(),
        )
        for r in included
    )
    return hashlib.sha256(json.dumps(used, separators=(",", ":")).encode()).hexdigest()


def hydrate_nba_current_pregame_features(
    *,
    official_event_id: str,
    home_team_id: str,
    away_team_id: str,
    event_start_time_utc: str,
    settled_games: Iterable[SettledGame],
    feature_timestamp: str,
    source_snapshot_id: str,
    source_snapshot_timestamp: str,
) -> dict[str, Any]:
    """Return a research-only feature package or a typed HOLD. Never a probability."""
    try:
        start = datetime.fromisoformat(str(event_start_time_utc).replace("Z", "+00:00"))
        feature_at = datetime.fromisoformat(str(feature_timestamp).replace("Z", "+00:00"))
        source_at = datetime.fromisoformat(str(source_snapshot_timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return _hold("NBA_FORWARD_SHADOW_TIMESTAMP_INVALID")
    if start.utcoffset() is None or feature_at.utcoffset() is None or source_at.utcoffset() is None:
        return _hold("NBA_FORWARD_SHADOW_TIMESTAMP_INVALID")
    if not (official_event_id and home_team_id and away_team_id and source_snapshot_id):
        return _hold("NBA_FORWARD_SHADOW_IDENTITY_MISSING")
    if home_team_id == away_team_id:
        return _hold("NBA_FORWARD_SHADOW_IDENTITY_INVALID")
    if feature_at >= start or source_at >= start:
        return _hold("NBA_FORWARD_SHADOW_NOT_PREGAME")
    if source_at > feature_at:
        return _hold("NBA_FORWARD_SHADOW_SOURCE_AFTER_FEATURE")
    game_date = start.astimezone(timezone.utc).date()
    teams = {home_team_id, away_team_id}
    relevant = [
        r for r in settled_games
        if r.game.sport == "NBA"
        and r.game.game_date < game_date
        and teams & {r.game.home_team_id, r.game.away_team_id}
    ]
    ids = [r.game.game_id for r in relevant]
    if len(ids) != len(set(ids)):
        return _hold("NBA_FORWARD_SHADOW_DUPLICATE_GAME_ID", official_event_id=official_event_id)
    for r in relevant:
        t = r.settled_at_utc
        if not isinstance(t, datetime) or t.utcoffset() is None:
            return _hold("NBA_FORWARD_SHADOW_SETTLEMENT_TIME_UNVERIFIABLE", official_event_id=official_event_id)
    # Only results already settled at the source snapshot were knowable then.
    included = [r for r in relevant if r.settled_at_utc <= source_at]
    games = [r.game for r in included]
    try:
        values = current_feature_values(games, home_team_id, away_team_id, game_date)
    except BasketballSpecialistError:  # tied settled score; unrelated errors propagate
        return _hold("NBA_FORWARD_SHADOW_TIED_SETTLED_SCORE", official_event_id=official_event_id)
    if values is None:
        return _hold("NBA_FORWARD_SHADOW_PRIOR_GAMES_INSUFFICIENT", official_event_id=official_event_id)
    features = {name: float(value) for name, value in zip(FEATURE_NAMES, values)}
    canonical = json.dumps(
        {"id": official_event_id, "home": home_team_id, "away": away_team_id, "features": features},
        sort_keys=True, separators=(",", ":"),
    )
    return {
        "status": "PASS",
        "sport": "NBA",
        "official_event_id": official_event_id,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "event_start_time_utc": event_start_time_utc,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": features,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_timestamp": source_snapshot_timestamp,
        "feature_audit": {
            "feature_timestamp": feature_timestamp,
            "feature_as_of": game_date.isoformat(),
            "dataset_id": _dataset_id(included),
            "feature_payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "source_replay_status": SOURCE_REPLAY_STATUS,
            "timestamp_attestation": TIMESTAMP_ATTESTATION,
        },
        "evidence_grade": "RESEARCH_ONLY_NOT_FOR_PUBLICATION",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": CAN_EXECUTE,
    }
