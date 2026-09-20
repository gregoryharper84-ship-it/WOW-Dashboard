"""NHL Skater Shots on Goal -- Phase 2 / 2.1: leakage-safe feature hydration.

Builds candidate feature snapshots on top of the immutable player x game SOG
records produced by nhl_skater_sog_ingestion.py. This module produces
CANDIDATE MODEL INPUTS, not predictions: it exposes raw rolling windows and
measurable deltas for a future fitted specialist to select among (Phase 3
does the fitting/ablation/window selection). It contains no fitted
coefficients, no probability, no calibration, no capability-manifest entry,
and no scorer.

Phase 2.1 hardening over the original Phase 2 cut:
  1. Leakage gating uses each record's `available_at` (settlement/retrieval
     timestamp), never `game_start_time` -- a game starting is not the same
     fact as its stats being published.
  2. Opponent shot-suppression evidence is taken only from team-games whose
     `team_shot_reconciliation_status` is MATCHED or
     UNOFFICIAL_FULL_ROSTER_ASSERTED (ingestion-time reconciliation against
     an official total or an explicit full-roster assertion) -- never from
     an unverified partial skater sum.
  3. Rest/back-to-back are derived from the TEAM's canonical schedule
     history (any record for a game the team played in), not the
     individual player's own row history -- correct for call-ups, trades,
     and players with no prior individual record. Back-to-back is a
     calendar-date adjacency rule, not an elapsed-hours threshold.
  4. `sample_sufficiency_status` (how much history exists) is reported
     separately from `freshness_status` (how recent that history is
     relative to `as_of`) -- a full 5-game window of old games is
     sufficient but not fresh.
  5. `snapshot_hash` covers the complete canonical snapshot content
     (identities, as_of, event_start, versions, feature values/sample
     counts, source_ids, evidence_ids, missing features/reasons,
     freshness, sample sufficiency) -- excluding only the hash field
     itself -- so identical numeric features from different evidence
     never collide.

Deferred to a later phase, not built here: projected line combinations,
projected PP units, unofficial lineup feeds, individual xG, teammate-effect
models, travel-distance models, sportsbook/team-total features, goalie-
quality adjustments, any fitted probability, calibration, capability-manifest
publication, /score-pick-request wiring, market/value logic.

can_execute=false unconditionally. PROBABILITY_PUBLISHABLE=False.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from nhl_skater_sog_ingestion import (
    PARTICIPATION_DRESSED_PLAYED,
    RECONCILIATION_MATCHED,
    RECONCILIATION_UNOFFICIAL_FULL_ROSTER_ASSERTED,
    SkaterGameSogRecord,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_EVIDENCE_ONLY = True

SCHEMA_VERSION = "NHL_SOG_FEATURE_SNAPSHOT_V1"
TRANSFORMATION_VERSION = "NHL_SOG_FEATURE_TRANSFORM_V1"

ROLLING_WINDOWS = (5, 10, 20)
OPPONENT_WINDOWS = (5, 10)

TRUSTED_TEAM_SHOT_STATUSES = {RECONCILIATION_MATCHED, RECONCILIATION_UNOFFICIAL_FULL_ROSTER_ASSERTED}

# Sample-sufficiency thresholds: purely "how much qualifying history exists,"
# independent of how recent it is.
SAMPLE_SUFFICIENT = "SUFFICIENT"
SAMPLE_PARTIAL = "PARTIAL"
SAMPLE_INSUFFICIENT = "INSUFFICIENT"

# Freshness thresholds: how recent the newest contributing evidence is
# relative to as_of, independent of sample count.
FRESHNESS_FRESH = "FRESH"
FRESHNESS_DEGRADED = "DEGRADED"
FRESHNESS_STALE = "STALE"
FRESHNESS_FRESH_MAX_DAYS = 10.0
FRESHNESS_DEGRADED_MAX_DAYS = 30.0

HOME = "HOME"
AWAY = "AWAY"


class NHLSogFeatureError(RuntimeError):
    """Typed internal hydration error. Reuses EVENT_ALREADY_STARTED (the
    existing canonical V17 code) where applicable; every other code here is
    a module-local diagnostic, not a canonical failure-taxonomy code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise NHLSogFeatureError("NHL_SOG_FEATURE_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _parse_toi_minutes(toi: str | None) -> float | None:
    """"MM:SS" -> minutes as float. Malformed/missing -> None (never guessed)."""
    if not toi:
        return None
    parts = str(toi).strip().split(":")
    if len(parts) != 2:
        return None
    try:
        minutes, seconds = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if minutes < 0 or not (0 <= seconds < 60):
        return None
    return minutes + seconds / 60.0


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


@dataclass(frozen=True)
class FeatureResult:
    value: float | None
    sample_count: int
    reason: str | None = None  # structured reason metadata when value is None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rolling_feature(values: Sequence[float], window: int) -> FeatureResult:
    windowed = values[:window]
    if not windowed:
        return FeatureResult(None, 0, "NO_QUALIFYING_GAMES_IN_WINDOW")
    return FeatureResult(_round(_mean(windowed)), len(windowed))


def _available_before(record: SkaterGameSogRecord, *, as_of_dt: datetime) -> bool:
    return _aware(record.available_at) <= as_of_dt


def _started_before_target(record: SkaterGameSogRecord, *, target_start_dt: datetime) -> bool:
    return _aware(record.game_start_time) < target_start_dt


def _qualifying_player_games(
    player_id: str,
    history: Sequence[SkaterGameSogRecord],
    *,
    event_id: str,
    as_of_dt: datetime,
    target_start_dt: datetime,
) -> list[SkaterGameSogRecord]:
    """Strictly-prior, strictly-available, dressed-with-recorded-value games
    for this player, most-recent-first (by game_start_time). A record
    qualifies only when its game started before the target game AND its
    stats had actually become available (`available_at <= as_of`) --
    settlement availability, not puck-drop time, gates evidence entry.
    Excludes the target event_id itself as a defense-in-depth guard against
    current-game leakage, independent of either timestamp check."""
    qualifying = [
        r
        for r in history
        if r.player_id == player_id
        and r.canonical_game_id != event_id
        and r.participation_status == PARTICIPATION_DRESSED_PLAYED
        and r.actual_value is not None
        and _started_before_target(r, target_start_dt=target_start_dt)
        and _available_before(r, as_of_dt=as_of_dt)
    ]
    return sorted(qualifying, key=lambda r: r.game_start_time, reverse=True)


def _team_schedule(
    team_id: str,
    history: Sequence[SkaterGameSogRecord],
    *,
    event_id: str,
    target_start_dt: datetime,
) -> list[tuple[str, datetime]]:
    """[(canonical_game_id, game_start_time)] for every strictly-prior game
    this TEAM played (home or away), from any record of that game --
    independent of any specific player's own row history, so it is correct
    for call-ups, trades, and players with zero individual prior games.

    Schedule/occurrence facts (a team played on date X) are not settlement
    facts -- the schedule is public before puck drop -- so this is gated
    only by chronological ordering against the target game, not by
    `available_at`.
    """
    seen_games: dict[str, datetime] = {}
    for r in history:
        if r.canonical_game_id == event_id:
            continue
        if team_id not in (r.home_team.team_id, r.away_team.team_id):
            continue
        start_dt = _aware(r.game_start_time)
        if not _started_before_target(r, target_start_dt=target_start_dt):
            continue
        seen_games.setdefault(r.canonical_game_id, start_dt)
    return sorted(seen_games.items(), key=lambda item: item[1], reverse=True)


def _trusted_team_game_shots(
    history: Sequence[SkaterGameSogRecord],
) -> dict[tuple[str, str], int]:
    """(game_id, team_id) -> the team's officially-reconciled SOG total for
    that game. Only MATCHED / UNOFFICIAL_FULL_ROSTER_ASSERTED team-games
    contribute -- a MISMATCH or INCOMPLETE_SKATER_COVERAGE team-game is
    absent here entirely, never approximated from a partial skater sum."""
    totals: dict[tuple[str, str], int] = {}
    for r in history:
        if r.team_shot_reconciliation_status not in TRUSTED_TEAM_SHOT_STATUSES:
            continue
        if r.official_team_sog_total is None:
            continue
        totals[(r.canonical_game_id, r.team_id)] = r.official_team_sog_total
    return totals


def _trusted_team_game_dressed_counts(
    history: Sequence[SkaterGameSogRecord],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for r in history:
        if r.team_shot_reconciliation_status not in TRUSTED_TEAM_SHOT_STATUSES:
            continue
        if r.participation_status != PARTICIPATION_DRESSED_PLAYED or r.actual_value is None:
            continue
        key = (r.canonical_game_id, r.team_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _game_team_ids_and_start(history: Sequence[SkaterGameSogRecord]) -> dict[str, tuple[str, str, str]]:
    """canonical_game_id -> (home_team_id, away_team_id, game_start_time)."""
    out: dict[str, tuple[str, str, str]] = {}
    for r in history:
        out.setdefault(r.canonical_game_id, (r.home_team.team_id, r.away_team.team_id, r.game_start_time))
    return out


def _opponent_prior_shots_allowed(
    opponent_team_id: str,
    history: Sequence[SkaterGameSogRecord],
    *,
    event_id: str,
    as_of_dt: datetime,
    target_start_dt: datetime,
) -> list[tuple[str, int, int | None]]:
    """[(game_id, shots_allowed, attacking_team_dressed_skater_count)],
    most-recent-first, for opponent_team_id's strictly-prior, strictly-
    available, RECONCILIATION-TRUSTED games (either home or away)."""
    trusted_totals = _trusted_team_game_shots(history)
    trusted_counts = _trusted_team_game_dressed_counts(history)
    game_teams = _game_team_ids_and_start(history)
    availability_by_game_team: dict[tuple[str, str], list[datetime]] = {}
    for r in history:
        availability_by_game_team.setdefault((r.canonical_game_id, r.team_id), []).append(_aware(r.available_at))

    rows: list[tuple[str, str, int, int | None]] = []
    for game_id, (home_id, away_id, start) in game_teams.items():
        if game_id == event_id:
            continue
        if opponent_team_id not in (home_id, away_id):
            continue
        start_dt = _aware(start)
        if not (start_dt < target_start_dt):
            continue
        attacking_team_id = away_id if opponent_team_id == home_id else home_id
        shots_allowed = trusted_totals.get((game_id, attacking_team_id))
        if shots_allowed is None:
            continue  # untrusted/absent team-shot evidence -- never approximated
        # The attacking team's evidence must itself have become available by
        # as_of -- a trusted total is still settlement evidence.
        availabilities = availability_by_game_team.get((game_id, attacking_team_id), [])
        if not availabilities or max(availabilities) > as_of_dt:
            continue
        attacking_count = trusted_counts.get((game_id, attacking_team_id))
        rows.append((game_id, start, shots_allowed, attacking_count))
    rows.sort(key=lambda row: row[1], reverse=True)
    return [(g, s, c) for g, _start, s, c in rows]


@dataclass(frozen=True)
class FeatureSnapshot:
    schema_version: str
    event_id: str
    player_id: str
    team_id: str
    opponent_team_id: str
    as_of: str
    event_start: str
    transformation_version: str
    source_ids: tuple[str, ...]
    feature_values: Mapping[str, float | None]
    feature_sample_counts: Mapping[str, int]
    freshness_status: str
    sample_sufficiency_status: str
    missing_features: tuple[str, ...]
    missing_feature_reasons: Mapping[str, str]
    evidence_ids: tuple[str, ...]
    snapshot_hash: str
    can_execute: bool = False
    research_evidence_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def hydrate_pregame_snapshot(
    *,
    event_id: str,
    player_id: str,
    team_id: str,
    opponent_team_id: str,
    is_home: bool,
    target_provider_season_id: str,
    event_start: str,
    as_of: str,
    history: Sequence[SkaterGameSogRecord],
) -> FeatureSnapshot:
    """Build a leakage-safe candidate feature snapshot for one upcoming
    player x game NHL SOG evaluation.

    `history` is the full pool of already-ingested SkaterGameSogRecord
    evidence (this player's, this team's, and the opponent's games); this
    function applies its own leakage filtering rather than trusting a
    pre-filtered input.
    """
    as_of_dt = _aware(as_of)
    target_start_dt = _aware(event_start)
    if as_of_dt >= target_start_dt:
        raise NHLSogFeatureError("EVENT_ALREADY_STARTED", f"as_of={as_of} >= event_start={event_start}")

    player_games = _qualifying_player_games(
        player_id, history, event_id=event_id, as_of_dt=as_of_dt, target_start_dt=target_start_dt
    )

    sog_values = [float(r.actual_value) for r in player_games]  # type: ignore[arg-type]
    toi_values_by_game = [(r, _parse_toi_minutes(r.toi)) for r in player_games]
    toi_values = [t for _r, t in toi_values_by_game if t is not None]
    shots_per_60_values = [
        (float(r.actual_value) / t) * 60.0  # type: ignore[operator]
        for r, t in toi_values_by_game
        if t is not None and t > 0
    ]

    feature_values: dict[str, float | None] = {}
    feature_sample_counts: dict[str, int] = {}
    missing_reasons: dict[str, str] = {}

    def _record(name: str, result: FeatureResult) -> None:
        feature_values[name] = result.value
        feature_sample_counts[name] = result.sample_count
        if result.value is None and result.reason:
            missing_reasons[name] = result.reason

    for window in ROLLING_WINDOWS:
        _record(f"rolling_sog_per_game_{window}", _rolling_feature(sog_values, window))
        _record(f"rolling_shots_per_60_{window}", _rolling_feature(shots_per_60_values, window))
        _record(f"rolling_toi_minutes_{window}", _rolling_feature(toi_values, window))

    # Season-to-date: all qualifying games within the target game's season.
    season_sog = [
        float(r.actual_value)  # type: ignore[arg-type]
        for r in player_games
        if r.provider_season_id == target_provider_season_id
    ]
    _record("season_to_date_sog_per_game", _rolling_feature(season_sog, len(season_sog) or 1))
    feature_sample_counts["season_to_date_games_played"] = len(season_sog)
    feature_values["season_to_date_games_played"] = float(len(season_sog))
    feature_sample_counts["career_qualifying_games_available"] = len(player_games)
    feature_values["career_qualifying_games_available"] = float(len(player_games))

    # Recent-vs-long-window deltas: raw components preserved, no subjective flag.
    def _delta(short_key: str, long_key: str) -> FeatureResult:
        short, long_ = feature_values.get(short_key), feature_values.get(long_key)
        if short is None or long_ is None:
            return FeatureResult(None, 0, "INSUFFICIENT_WINDOWS_FOR_DELTA")
        return FeatureResult(_round(short - long_), min(feature_sample_counts[short_key], feature_sample_counts[long_key]))

    _record("recent_vs_long_toi_delta_5_20", _delta("rolling_toi_minutes_5", "rolling_toi_minutes_20"))
    _record("recent_vs_long_sog_rate_delta_5_20", _delta("rolling_sog_per_game_5", "rolling_sog_per_game_20"))
    _record("recent_vs_long_shots_per_60_delta_5_20", _delta("rolling_shots_per_60_5", "rolling_shots_per_60_20"))

    # Opponent shot suppression, from strictly-prior, reconciliation-trusted
    # official records only.
    opponent_rows = _opponent_prior_shots_allowed(
        opponent_team_id, history, event_id=event_id, as_of_dt=as_of_dt, target_start_dt=target_start_dt
    )
    allowed_values = [float(shots) for _g, shots, _c in opponent_rows]
    per_skater_values = [
        float(shots) / count for _g, shots, count in opponent_rows if count
    ]
    for window in OPPONENT_WINDOWS:
        _record(f"opponent_sog_allowed_per_game_{window}", _rolling_feature(allowed_values, window))
        _record(f"opponent_sog_allowed_per_skater_game_{window}", _rolling_feature(per_skater_values, window))
    feature_sample_counts["opponent_qualifying_games_available"] = len(opponent_rows)
    feature_values["opponent_qualifying_games_available"] = float(len(opponent_rows))

    # Situational context.
    feature_values["is_home"] = 1.0 if is_home else 0.0
    feature_sample_counts["is_home"] = 1

    # Team-based rest / back-to-back (correct for call-ups, trades, and
    # players with no individual prior-game record).
    team_schedule = _team_schedule(team_id, history, event_id=event_id, target_start_dt=target_start_dt)
    if team_schedule:
        _, most_recent_team_game_start = team_schedule[0]
        rest_days = (target_start_dt - most_recent_team_game_start).total_seconds() / 86400.0
        _record("rest_days", FeatureResult(_round(rest_days), 1))
        # Back-to-back is calendar-date adjacency, not an elapsed-hours
        # threshold: two games on consecutive UTC dates are a back-to-back
        # even if ~25 hours apart, and two games on the same date with a
        # large gap are not meaningfully "rested" either -- but the
        # canonical NHL definition is date adjacency, so that governs.
        target_date = target_start_dt.date()
        prior_date = most_recent_team_game_start.date()
        back_to_back = 1.0 if (target_date - prior_date).days == 1 else 0.0
        _record("back_to_back", FeatureResult(back_to_back, 1))
    else:
        _record("rest_days", FeatureResult(None, 0, "NO_PRIOR_TEAM_GAME_AVAILABLE"))
        _record("back_to_back", FeatureResult(None, 0, "NO_PRIOR_TEAM_GAME_AVAILABLE"))

    missing_features = tuple(sorted(name for name, value in feature_values.items() if value is None))

    # Sample sufficiency: how much qualifying history exists (unrelated to
    # how recent it is).
    core_sample = feature_sample_counts.get("rolling_sog_per_game_5", 0)
    if core_sample >= 5:
        sample_sufficiency_status = SAMPLE_SUFFICIENT
    elif core_sample > 0:
        sample_sufficiency_status = SAMPLE_PARTIAL
    else:
        sample_sufficiency_status = SAMPLE_INSUFFICIENT

    # Freshness: how recent the newest contributing evidence is relative to
    # as_of. A full sample of old games is sufficient but not fresh.
    newest_evidence_dt = None
    if player_games:
        newest_evidence_dt = _aware(player_games[0].game_start_time)
    if team_schedule:
        _, team_start = team_schedule[0]
        newest_evidence_dt = team_start if newest_evidence_dt is None else max(newest_evidence_dt, team_start)
    if newest_evidence_dt is None:
        freshness_status = FRESHNESS_STALE
    else:
        recency_days = (as_of_dt - newest_evidence_dt).total_seconds() / 86400.0
        if recency_days <= FRESHNESS_FRESH_MAX_DAYS:
            freshness_status = FRESHNESS_FRESH
        elif recency_days <= FRESHNESS_DEGRADED_MAX_DAYS:
            freshness_status = FRESHNESS_DEGRADED
        else:
            freshness_status = FRESHNESS_STALE

    evidence_ids = tuple(sorted({r.canonical_game_id for r in player_games} | {g for g, _s, _c in opponent_rows}))
    source_ids = tuple(sorted({r.source for r in history if r.canonical_game_id in evidence_ids}))

    # Hash the complete canonical snapshot content, excluding only the hash
    # field itself, so identical numeric feature values sourced from
    # different evidence (different evidence_ids/source_ids) never collide.
    hash_payload = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "player_id": player_id,
        "team_id": team_id,
        "opponent_team_id": opponent_team_id,
        "as_of": as_of,
        "event_start": event_start,
        "transformation_version": TRANSFORMATION_VERSION,
        "source_ids": source_ids,
        "feature_values": feature_values,
        "feature_sample_counts": feature_sample_counts,
        "freshness_status": freshness_status,
        "sample_sufficiency_status": sample_sufficiency_status,
        "missing_features": missing_features,
        "missing_feature_reasons": missing_reasons,
        "evidence_ids": evidence_ids,
    }
    snapshot_hash = _compute_hash(hash_payload)

    return FeatureSnapshot(
        schema_version=SCHEMA_VERSION,
        event_id=event_id,
        player_id=player_id,
        team_id=team_id,
        opponent_team_id=opponent_team_id,
        as_of=as_of,
        event_start=event_start,
        transformation_version=TRANSFORMATION_VERSION,
        source_ids=source_ids,
        feature_values=feature_values,
        feature_sample_counts=feature_sample_counts,
        freshness_status=freshness_status,
        sample_sufficiency_status=sample_sufficiency_status,
        missing_features=missing_features,
        missing_feature_reasons=missing_reasons,
        evidence_ids=evidence_ids,
        snapshot_hash=snapshot_hash,
    )


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "RESEARCH_EVIDENCE_ONLY",
    "SCHEMA_VERSION",
    "TRANSFORMATION_VERSION",
    "ROLLING_WINDOWS",
    "OPPONENT_WINDOWS",
    "SAMPLE_SUFFICIENT",
    "SAMPLE_PARTIAL",
    "SAMPLE_INSUFFICIENT",
    "FRESHNESS_FRESH",
    "FRESHNESS_DEGRADED",
    "FRESHNESS_STALE",
    "HOME",
    "AWAY",
    "NHLSogFeatureError",
    "FeatureResult",
    "FeatureSnapshot",
    "hydrate_pregame_snapshot",
]
