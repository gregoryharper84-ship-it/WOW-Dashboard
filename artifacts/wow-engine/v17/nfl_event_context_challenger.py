"""Governed research-only NFL event-context challenger.

This candidate tests a proved V1 early-season defect: the production recent-8
window blends prior-season games directly into current-season form (75% prior
season at Week 3 in the frozen historical feature ledger).

No sportsbook/market probability is consumed and no home-underdog bonus is
applied. Current-season and previous-season state are separate fitted inputs.
Every feature is reconstructed strictly from events before the target event.

Candidate only: no certification, promotion, probability publication, or wager
execution authority.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from statistics import mean
from typing import Any, Mapping, Sequence

from v17.binary_candidate_lifecycle import (
    BinaryCandidateError,
    BinaryTrainingRow,
    train_binary_candidate,
)
from v17.team_state_challenger_training import (
    TeamStateChallengerUnavailable,
    _persist_artifact,
    _persist_rows,
    evaluate_binary_research_screen,
)

CAN_EXECUTE = False
MODEL_FAMILY = "NFL_EVENT_CONTEXT_LOGIT_V2"
FEATURE_SCHEMA_VERSION = "NFL_EVENT_CONTEXT_FEATURES_V2"
CURRENT_SEASON_REFERENCE_GAMES = 8.0
PREVIOUS_SEASON_GAMES = 8
MIN_TOTAL_PRIOR_GAMES = 4

FEATURE_ORDER = (
    "week",
    "early_week_1_4",
    "home_current_season_share",
    "away_current_season_share",
    "current_season_share_edge",
    "season_win_rate_edge",
    "season_point_diff_edge",
    "season_process_margin_edge",
    "season_schedule_adjusted_point_diff_edge",
    "season_turnover_rate_edge",
    "season_sack_allowed_rate_edge",
    "season_special_teams_edge",
    "recent3_point_diff_edge",
    "recent3_process_margin_edge",
    "previous_season_win_rate_edge",
    "previous_season_point_diff_edge",
    "previous_season_process_margin_edge",
    "previous_season_turnover_rate_edge",
    "previous_season_sack_allowed_rate_edge",
    "qb_continuity_edge",
    "home_qb_continuity",
    "away_qb_continuity",
    "rest_days_edge",
    "home_rest_days",
    "away_rest_days",
)


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _avg(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return mean(values) if values else 0.0


def _win_rate(rows: Sequence[Mapping[str, Any]]) -> float:
    return mean(1.0 if bool(row.get("won")) else 0.0 for row in rows) if rows else 0.0


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a = {str(value) for value in left if str(value)}
    b = {str(value) for value in right if str(value)}
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _side_metrics(
    history: Sequence[Mapping[str, Any]],
    *,
    season: int,
    target_time: datetime,
) -> dict[str, float]:
    prior = [row for row in history if _dt(row["event_time"]) < target_time]
    current = [row for row in prior if int(row.get("season") or -1) == season]
    previous = [
        row for row in prior if int(row.get("season") or -1) == season - 1
    ][-PREVIOUS_SEASON_GAMES:]
    recent3 = current[-3:]

    season_n = len(current)
    current_share = min(season_n, int(CURRENT_SEASON_REFERENCE_GAMES)) / CURRENT_SEASON_REFERENCE_GAMES
    last_time = _dt(prior[-1]["event_time"]) if prior else target_time
    rest_days = max(
        0.0,
        min(21.0, (target_time - last_time).total_seconds() / 86400.0),
    )

    if len(prior) >= 2:
        qb_continuity = _jaccard(
            list(prior[-1].get("qb_ids") or []),
            list(prior[-2].get("qb_ids") or []),
        )
    else:
        qb_continuity = 0.0

    return {
        "total_prior_games": float(len(prior)),
        "current_season_share": float(current_share),
        "season_win_rate": _win_rate(current),
        "season_point_diff": _avg(current, "point_diff"),
        "season_process_margin": _avg(current, "process_margin"),
        "season_schedule_adjusted_point_diff": _avg(
            current, "schedule_adjusted_point_diff"
        ),
        "season_turnover_rate": _avg(current, "turnovers"),
        "season_sack_allowed_rate": _avg(current, "sacks_allowed"),
        "season_special_teams": _avg(current, "special_teams_epa"),
        "recent3_point_diff": _avg(recent3, "point_diff"),
        "recent3_process_margin": _avg(recent3, "process_margin"),
        "previous_season_win_rate": _win_rate(previous),
        "previous_season_point_diff": _avg(previous, "point_diff"),
        "previous_season_process_margin": _avg(previous, "process_margin"),
        "previous_season_turnover_rate": _avg(previous, "turnovers"),
        "previous_season_sack_allowed_rate": _avg(previous, "sacks_allowed"),
        "qb_continuity": qb_continuity,
        "rest_days": rest_days,
    }


def _edge(home: Mapping[str, float], away: Mapping[str, float], field: str) -> float:
    return float(home[field] - away[field])


def _reverse_edge(
    home: Mapping[str, float], away: Mapping[str, float], field: str
) -> float:
    return float(away[field] - home[field])


def _feature_map(
    *,
    week: int,
    home: Mapping[str, float],
    away: Mapping[str, float],
) -> dict[str, float]:
    features = {
        "week": float(week),
        "early_week_1_4": float(int(week) <= 4),
        "home_current_season_share": float(home["current_season_share"]),
        "away_current_season_share": float(away["current_season_share"]),
        "current_season_share_edge": _edge(home, away, "current_season_share"),
        "season_win_rate_edge": _edge(home, away, "season_win_rate"),
        "season_point_diff_edge": _edge(home, away, "season_point_diff"),
        "season_process_margin_edge": _edge(home, away, "season_process_margin"),
        "season_schedule_adjusted_point_diff_edge": _edge(
            home, away, "season_schedule_adjusted_point_diff"
        ),
        # Lower turnover and sack-allowed rates are favorable.
        "season_turnover_rate_edge": _reverse_edge(
            home, away, "season_turnover_rate"
        ),
        "season_sack_allowed_rate_edge": _reverse_edge(
            home, away, "season_sack_allowed_rate"
        ),
        "season_special_teams_edge": _edge(home, away, "season_special_teams"),
        "recent3_point_diff_edge": _edge(home, away, "recent3_point_diff"),
        "recent3_process_margin_edge": _edge(
            home, away, "recent3_process_margin"
        ),
        "previous_season_win_rate_edge": _edge(
            home, away, "previous_season_win_rate"
        ),
        "previous_season_point_diff_edge": _edge(
            home, away, "previous_season_point_diff"
        ),
        "previous_season_process_margin_edge": _edge(
            home, away, "previous_season_process_margin"
        ),
        "previous_season_turnover_rate_edge": _reverse_edge(
            home, away, "previous_season_turnover_rate"
        ),
        "previous_season_sack_allowed_rate_edge": _reverse_edge(
            home, away, "previous_season_sack_allowed_rate"
        ),
        "qb_continuity_edge": _edge(home, away, "qb_continuity"),
        "home_qb_continuity": float(home["qb_continuity"]),
        "away_qb_continuity": float(away["qb_continuity"]),
        "rest_days_edge": _edge(home, away, "rest_days"),
        "home_rest_days": float(home["rest_days"]),
        "away_rest_days": float(away["rest_days"]),
    }
    return {name: float(features[name]) for name in FEATURE_ORDER}


def build_rows(
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    """Build strictly-prior NFL rows with separated current/previous seasons."""
    history: dict[str, list[dict[str, Any]]] = {}
    rows: list[BinaryTrainingRow] = []
    metadata: list[dict[str, Any]] = []

    ordered = sorted(
        events,
        key=lambda row: (_dt(row["event_start_time"]), str(row["event_id"])),
    )
    for event in ordered:
        start = _dt(event["event_start_time"])
        season = int(event.get("season") or 0)
        week = int(event.get("week") or 0)
        home_team = str(event.get("home_team") or "").strip()
        away_team = str(event.get("away_team") or "").strip()
        if not home_team or not away_team or home_team == away_team or week <= 0:
            continue

        home_history = history.get(home_team, [])
        away_history = history.get(away_team, [])
        hm = _side_metrics(home_history, season=season, target_time=start)
        am = _side_metrics(away_history, season=season, target_time=start)
        home_score = float(event.get("home_score") or 0.0)
        away_score = float(event.get("away_score") or 0.0)

        if (
            hm["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES
            and am["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES
            and home_score != away_score
        ):
            features = _feature_map(week=week, home=hm, away=am)
            manifest = {
                "program": "NFL_EVENT_CONTEXT_CHALLENGER_V2",
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "event_id": str(event["event_id"]),
                "source_manifest": dict(event.get("source_manifest") or {}),
                "current_and_previous_season_separated": True,
                "v1_recent8_blend_reused": False,
                "market_features_used": False,
                "manual_probability_adjustments": False,
                # Target-game QB participation is postgame evidence and is never
                # used. Continuity comes only from the two strictly prior games.
                "target_game_participant_ids_used": False,
            }
            rows.append(
                BinaryTrainingRow(
                    event_id=str(event["event_id"]),
                    event_start_time=start.isoformat(),
                    feature_as_of=(start - timedelta(seconds=1)).isoformat(),
                    positive_outcome=home_score > away_score,
                    features=features,
                    source_manifest_sha256=_hash(manifest),
                )
            )
            metadata.append({"source_manifest": manifest})

        # Opponent strength is frozen before the target result is appended.
        home_prior_strength = float(hm["season_point_diff"])
        away_prior_strength = float(am["season_point_diff"])
        history.setdefault(home_team, []).append(
            {
                "event_time": start.isoformat(),
                "season": season,
                "won": home_score > away_score,
                "point_diff": home_score - away_score,
                "process_margin": float(event.get("home_process_margin") or 0.0),
                "schedule_adjusted_point_diff": (
                    home_score - away_score + away_prior_strength
                ),
                "turnovers": float(event.get("home_turnovers") or 0.0),
                "sacks_allowed": float(event.get("home_sacks_allowed") or 0.0),
                "special_teams_epa": float(
                    event.get("home_special_teams_epa") or 0.0
                ),
                "qb_ids": list(event.get("home_history_lineup_ids") or []),
            }
        )
        history.setdefault(away_team, []).append(
            {
                "event_time": start.isoformat(),
                "season": season,
                "won": away_score > home_score,
                "point_diff": away_score - home_score,
                "process_margin": float(event.get("away_process_margin") or 0.0),
                "schedule_adjusted_point_diff": (
                    away_score - home_score + home_prior_strength
                ),
                "turnovers": float(event.get("away_turnovers") or 0.0),
                "sacks_allowed": float(event.get("away_sacks_allowed") or 0.0),
                "special_teams_epa": float(
                    event.get("away_special_teams_epa") or 0.0
                ),
                "qb_ids": list(event.get("away_history_lineup_ids") or []),
            }
        )

    if not rows:
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_ROWS_EMPTY", "no leakage-safe rows"
        )
    return rows, metadata


def train_and_persist(
    client: Any,
    *,
    events: Sequence[Mapping[str, Any]],
    training_code_sha: str,
) -> dict[str, Any]:
    rows, metadata = build_rows(events)
    try:
        candidate = train_binary_candidate(
            rows,
            model_family=MODEL_FAMILY,
            feature_names=FEATURE_ORDER,
            min_rows=300,
        )
    except BinaryCandidateError as exc:
        raise TeamStateChallengerUnavailable(exc.code, str(exc)) from exc

    screen = evaluate_binary_research_screen(candidate.metrics)
    _persist_rows(
        client,
        sport="NFL",
        league="NFL",
        schema=FEATURE_SCHEMA_VERSION,
        model_family=MODEL_FAMILY,
        rows=rows,
        metadata=metadata,
        multiclass=False,
    )
    metrics = asdict(candidate.metrics) | {
        "research_screen_pass": bool(screen["passed"]),
        "team_state_research_screen": screen,
        "market_features_used": False,
        "manual_probability_adjustments": False,
        "champion_challenger_required": True,
        "v1_recent8_blend_reused": False,
        "separated_current_previous_season": True,
        "week_3_v1_prior_season_share_reproduced_pct": 75.0,
    }
    version = _persist_artifact(
        client,
        sport="NFL",
        league="NFL",
        family=MODEL_FAMILY,
        schema=FEATURE_SCHEMA_VERSION,
        training_code_sha=training_code_sha,
        candidate=candidate,
        metrics=metrics,
        research_screen_pass=bool(screen["passed"]),
    )
    return {
        "sport": "NFL",
        "league": "NFL",
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_artifact_version": version,
        "eligible_rows": len(rows),
        "feature_count": len(FEATURE_ORDER),
        "metrics": metrics,
        "research_screen_pass": bool(screen["passed"]),
        "lifecycle_state": "CANDIDATE",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "FEATURE_ORDER",
    "FEATURE_SCHEMA_VERSION",
    "MODEL_FAMILY",
    "build_rows",
    "train_and_persist",
]
