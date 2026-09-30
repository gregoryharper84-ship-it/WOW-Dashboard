"""Governed NFL total-points specialist for Pick'em tiebreakers only.

The production candidate deliberately uses the simplest challenger that remained
most stable in forward evidence: a leakage-safe empirical scoring/allowing model
with current-season shrinkage toward the immediately prior regular season.

This is *not* an over/under betting model.  It never consumes a sportsbook total,
spread, moneyline, implied probability, consensus projection, or generic LLM
estimate.  It does not produce p_over/p_under, does not become rank eligible, and
cannot execute anything.

V1 evidence receipt (regular season only):
* 2025 validation: MAE 10.7303, RMSE 13.4892, bias -0.1632 (272 games)
* 2026 forward weeks 1-3: MAE 12.0737, RMSE 15.4942, bias -0.3689 (48 games)
* 2026 forward prior-season-mean reference: MAE 12.6720, RMSE 15.6463
* 2025 empirical residual q10/q90: -16.8519 / +17.4090
* 2026 forward interval coverage using those frozen quantiles: 75.0%

More complex ridge challengers were retained as research evidence but were not
promoted because their 2026 forward error was worse.  Complexity does not earn
promotion when the counterexample review says otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite, sqrt
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
CONTROLLING_SPECIALIST = "wow.nfl-total-points-tiebreaker-specialist"
MODEL_FAMILY = "NFL_TOTAL_POINTS_SHRUNK_SCORING_V1"
MODEL_ARTIFACT_VERSION = "NFL_TOTAL_POINTS_SHRUNK_SCORING_V1_2026"
FEATURE_SCHEMA_VERSION = "NFL_TOTAL_POINTS_SCORING_PRIOR_V1"
CALIBRATION_METHOD = "EMPIRICAL_RESIDUAL_QUANTILES"
CALIBRATION_VERSION = "NFL_TOTAL_POINTS_2025_RESIDUAL_INTERVAL_V1"
USE_CASE = "PICKEM_TIEBREAKER_ONLY"
VALID_SEASON = 2026
CURRENT_SEASON_REFERENCE_GAMES = 8.0
MIN_PREVIOUS_SEASON_GAMES = 8
INTERVAL_COVERAGE_TARGET = 0.80
CALIBRATION_Q10 = -16.8519050802139
CALIBRATION_Q50 = -0.274877450980391
CALIBRATION_Q90 = 17.4090497737557
VALIDATION_2025 = {
    "n": 272,
    "mae": 10.7302526688637,
    "rmse": 13.48918044647,
    "bias_actual_minus_predicted": -0.16315055691137,
    "interval_coverage": 0.794117647058823,
}
FORWARD_2026 = {
    "n": 48,
    "mae": 12.0736825980392,
    "rmse": 15.4941923855256,
    "bias_actual_minus_predicted": -0.368872549019609,
    "interval_coverage": 0.75,
}
FORWARD_REFERENCE_2026 = {
    "model": "PRIOR_SEASON_LEAGUE_MEAN",
    "mae": 12.6720281862745,
    "rmse": 15.6463203733955,
}
CALIBRATION_CORPUS_SHA256 = "ef439cb952770a0b626a67024950a3e3807399adefdcccf6bb3e338b26f181fd"
FORWARD_CORPUS_SHA256 = "0a3969b4f2721269394aec318814bb51332a4c967b7fcdbf34f61f307d631f1b"


class NFLTotalPointsTiebreakerError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TeamState:
    team: str
    prior_games: int
    current_games: int
    prior_points_for: float
    prior_points_against: float
    current_points_for: float | None
    current_points_against: float | None
    current_weight: float
    blended_points_for: float
    blended_points_against: float


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NFLTotalPointsTiebreakerError("MODEL_INPUTS_INSUFFICIENT", field)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NFLTotalPointsTiebreakerError("MODEL_INPUTS_INSUFFICIENT", field) from exc
    if not isfinite(parsed) or parsed < 0:
        raise NFLTotalPointsTiebreakerError("MODEL_INPUTS_INSUFFICIENT", field)
    return parsed


def _is_regular(event: Mapping[str, Any]) -> bool:
    return str(event.get("game_type") or "").strip().upper() in {"REG", "REGULAR", "REGULAR_SEASON"}


def _completed_prior_events(
    events: Sequence[Mapping[str, Any]], *, target_time: datetime
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in events:
        if not _is_regular(raw):
            continue
        try:
            event_time = _dt(raw.get("event_start_time") or raw.get("event_start_time_utc") or raw.get("gameday"))
        except Exception:
            continue
        if event_time >= target_time:
            continue
        home = str(raw.get("home_team") or "").strip().upper()
        away = str(raw.get("away_team") or "").strip().upper()
        if not home or not away or home == away:
            continue
        if raw.get("home_score") is None or raw.get("away_score") is None:
            continue
        try:
            season = int(raw.get("season"))
            home_score = _number(raw.get("home_score"), field="home_score")
            away_score = _number(raw.get("away_score"), field="away_score")
        except (TypeError, ValueError, NFLTotalPointsTiebreakerError):
            continue
        out.append({
            "event_id": str(raw.get("event_id") or raw.get("game_id") or ""),
            "event_time": event_time,
            "season": season,
            "home_team": home,
            "away_team": away,
            "home_score": home_score,
            "away_score": away_score,
        })
    out.sort(key=lambda row: (row["event_time"], row["event_id"]))
    return out


def _team_state(
    events: Sequence[Mapping[str, Any]], *, team: str, season: int, target_time: datetime
) -> TeamState:
    prior_season = season - 1
    prior_pf: list[float] = []
    prior_pa: list[float] = []
    current_pf: list[float] = []
    current_pa: list[float] = []
    for event in _completed_prior_events(events, target_time=target_time):
        if team not in {event["home_team"], event["away_team"]}:
            continue
        if team == event["home_team"]:
            pf, pa = float(event["home_score"]), float(event["away_score"])
        else:
            pf, pa = float(event["away_score"]), float(event["home_score"])
        if int(event["season"]) == prior_season:
            prior_pf.append(pf)
            prior_pa.append(pa)
        elif int(event["season"]) == season:
            current_pf.append(pf)
            current_pa.append(pa)

    if len(prior_pf) < MIN_PREVIOUS_SEASON_GAMES:
        raise NFLTotalPointsTiebreakerError(
            "MODEL_INPUTS_INSUFFICIENT",
            f"NFL_TOTAL_POINTS_PRIOR_SEASON_HISTORY_INSUFFICIENT:{team}:{len(prior_pf)}",
        )
    prior_for = sum(prior_pf) / len(prior_pf)
    prior_against = sum(prior_pa) / len(prior_pa)
    current_for = sum(current_pf) / len(current_pf) if current_pf else None
    current_against = sum(current_pa) / len(current_pa) if current_pa else None
    weight = min(len(current_pf) / CURRENT_SEASON_REFERENCE_GAMES, 1.0)
    blended_for = weight * (current_for if current_for is not None else prior_for) + (1.0 - weight) * prior_for
    blended_against = weight * (current_against if current_against is not None else prior_against) + (1.0 - weight) * prior_against
    return TeamState(
        team=team,
        prior_games=len(prior_pf),
        current_games=len(current_pf),
        prior_points_for=prior_for,
        prior_points_against=prior_against,
        current_points_for=current_for,
        current_points_against=current_against,
        current_weight=weight,
        blended_points_for=blended_for,
        blended_points_against=blended_against,
    )


def _source_hash(events: Sequence[Mapping[str, Any]], *, target_time: datetime) -> str:
    normalized = [
        {
            "event_id": row["event_id"],
            "event_time": row["event_time"].isoformat(),
            "season": row["season"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_score": row["home_score"],
            "away_score": row["away_score"],
        }
        for row in _completed_prior_events(events, target_time=target_time)
    ]
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return sha256(payload).hexdigest()


def score_tiebreaker(
    events: Sequence[Mapping[str, Any]],
    *,
    event_id: str,
    event_start_time: Any,
    season: int,
    week: int,
    home_team: str,
    away_team: str,
) -> dict[str, Any]:
    """Return one governed tiebreaker point projection from sporting history only."""
    if int(season) != VALID_SEASON:
        raise NFLTotalPointsTiebreakerError(
            "NFL_TOTAL_POINTS_ARTIFACT_SEASON_UNSUPPORTED",
            f"artifact={MODEL_ARTIFACT_VERSION}:requested_season={season}",
        )
    target_time = _dt(event_start_time)
    home = str(home_team or "").strip().upper()
    away = str(away_team or "").strip().upper()
    if not event_id or not home or not away or home == away or int(week) <= 0:
        raise NFLTotalPointsTiebreakerError("MODEL_INPUTS_INSUFFICIENT", "NFL_TOTAL_POINTS_EVENT_IDENTITY_INVALID")

    home_state = _team_state(events, team=home, season=int(season), target_time=target_time)
    away_state = _team_state(events, team=away, season=int(season), target_time=target_time)
    home_points = (home_state.blended_points_for + away_state.blended_points_against) / 2.0
    away_points = (away_state.blended_points_for + home_state.blended_points_against) / 2.0
    total = home_points + away_points
    if not all(isfinite(value) and value >= 0 for value in (home_points, away_points, total)):
        raise NFLTotalPointsTiebreakerError("MODEL_OUTPUT_INVALID", "NFL_TOTAL_POINTS_PROJECTION_INVALID")

    return {
        "status": "TIEBREAKER_MODEL_QUALIFIED",
        "code": "NFL_TOTAL_POINTS_TIEBREAKER_QUALIFIED",
        "use_case": USE_CASE,
        "event_id": str(event_id),
        "sport": "NFL",
        "season": int(season),
        "week": int(week),
        "home_team": home,
        "away_team": away,
        "projected_home_points": float(home_points),
        "projected_away_points": float(away_points),
        "projected_total_points": float(total),
        "suggested_integer_tiebreaker": int(round(total)),
        "prediction_interval": {
            "coverage_target": INTERVAL_COVERAGE_TARGET,
            "lower": max(0.0, float(total + CALIBRATION_Q10)),
            "upper": float(total + CALIBRATION_Q90),
            "calibration_q10": CALIBRATION_Q10,
            "calibration_q50": CALIBRATION_Q50,
            "calibration_q90": CALIBRATION_Q90,
        },
        "team_state": {
            "home": home_state.__dict__,
            "away": away_state.__dict__,
        },
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": MODEL_ARTIFACT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "calibration_method": CALIBRATION_METHOD,
        "calibration_version": CALIBRATION_VERSION,
        "calibration_corpus_sha256": CALIBRATION_CORPUS_SHA256,
        "forward_corpus_sha256": FORWARD_CORPUS_SHA256,
        "source_history_sha256": _source_hash(events, target_time=target_time),
        "validation_receipt": {
            "validation_2025": dict(VALIDATION_2025),
            "forward_2026": dict(FORWARD_2026),
            "forward_reference_2026": dict(FORWARD_REFERENCE_2026),
        },
        "sportsbook_total_used": False,
        "sportsbook_price_used": False,
        "market_probability_used": False,
        "moneyline_probability_used": False,
        "generic_llm_projection_used": False,
        "tiebreaker_publishable": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_authority": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


def capability() -> dict[str, Any]:
    return {
        "status": "AVAILABLE",
        "use_case": USE_CASE,
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": MODEL_ARTIFACT_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "valid_season": VALID_SEASON,
        "sportsbook_total_substitution_allowed": False,
        "moneyline_probability_reuse_allowed": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def replay_metrics(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, float]:
    if len(actual) != len(predicted) or not actual:
        raise NFLTotalPointsTiebreakerError("MODEL_OUTPUT_INVALID", "NFL_TOTAL_POINTS_REPLAY_LENGTH_INVALID")
    errors = [float(a) - float(p) for a, p in zip(actual, predicted)]
    abs_errors = [abs(value) for value in errors]
    return {
        "n": float(len(errors)),
        "mae": sum(abs_errors) / len(abs_errors),
        "rmse": sqrt(sum(value * value for value in errors) / len(errors)),
        "bias_actual_minus_predicted": sum(errors) / len(errors),
    }


__all__ = [
    "CALIBRATION_VERSION",
    "CAN_EXECUTE",
    "CONTROLLING_SPECIALIST",
    "MODEL_ARTIFACT_VERSION",
    "MODEL_FAMILY",
    "NFLTotalPointsTiebreakerError",
    "capability",
    "replay_metrics",
    "score_tiebreaker",
]
