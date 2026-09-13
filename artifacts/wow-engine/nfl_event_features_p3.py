"""WOW V17 NFL moneyline P3 early-season feature enrichment.

This module is additive to P2. It does not publish probabilities, certify a
model, or change can_execute. It creates leakage-safe features for a later
fitted/calibrated NFL event model.

Design:
- Prior-season performance is the early-season anchor.
- Current-season observations gain weight as games are played.
- Extreme priors are shrunk toward league average.
- QB/coaching/roster continuity, market and weather signals are bounded inputs.
- Situational/trend signals are tie-breaker scale only; they cannot dominate.
- Only information timestamped before kickoff is eligible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp
from typing import Any, Iterable

FEATURE_SCHEMA_VERSION = "NFL_EVENT_EARLY_SEASON_V1"
MAX_SITUATIONAL_POINTS = 1.0
MAX_MARKET_POINTS = 2.5
MAX_CONTINUITY_POINTS = 1.5


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _mean(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values]
    return sum(vals) / len(vals) if vals else None


def prior_weight_for_week(week: int) -> float:
    """Smooth prior decay: W1=1.00, W2=.85, W3=.70, W4=.55, W5=.40, W6=.25, W7+=.15.

    We intentionally retain a small prior floor instead of hard-dropping prior
    information; the fitted model may learn a different effective weight.
    """
    schedule = {1: 1.00, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40, 6: 0.25}
    return schedule.get(max(1, int(week)), 0.15)


def shrink(value: float | None, league_mean: float, sample_games: int, strength: float = 6.0) -> float:
    """Empirical-Bayes style shrinkage toward a league mean."""
    if value is None:
        return float(league_mean)
    n = max(0, int(sample_games))
    w = n / (n + float(strength))
    return float(w * float(value) + (1.0 - w) * float(league_mean))


def blend_prior_current(prior: float, current: float | None, week: int) -> float:
    p = prior_weight_for_week(week)
    if current is None:
        return float(prior)
    return float(p * prior + (1.0 - p) * float(current))


@dataclass(frozen=True)
class PregameContext:
    kickoff_at: datetime
    as_of: datetime
    week: int
    home_qb_continuity: float = 0.0
    away_qb_continuity: float = 0.0
    home_coaching_continuity: float = 0.0
    away_coaching_continuity: float = 0.0
    home_roster_continuity: float = 0.0
    away_roster_continuity: float = 0.0
    market_home_prob_open: float | None = None
    market_home_prob_current: float | None = None
    wind_mph: float | None = None
    temperature_f: float | None = None
    precipitation_prob: float | None = None
    situational_home_signal: float = 0.0

    def validate(self) -> None:
        if self.as_of >= self.kickoff_at:
            raise ValueError("PREGAME_CUTOFF_VIOLATION")
        for value in (
            self.home_qb_continuity, self.away_qb_continuity,
            self.home_coaching_continuity, self.away_coaching_continuity,
            self.home_roster_continuity, self.away_roster_continuity,
        ):
            if not -1.0 <= float(value) <= 1.0:
                raise ValueError("CONTINUITY_INPUT_OUT_OF_RANGE")
        if not -1.0 <= float(self.situational_home_signal) <= 1.0:
            raise ValueError("SITUATIONAL_INPUT_OUT_OF_RANGE")
        for value in (self.market_home_prob_open, self.market_home_prob_current):
            if value is not None and not 0.0 < float(value) < 1.0:
                raise ValueError("MARKET_PROBABILITY_OUT_OF_RANGE")


def continuity_points(ctx: PregameContext) -> float:
    """Bounded continuity modifier, expressed in point-equivalent units."""
    qb = ctx.home_qb_continuity - ctx.away_qb_continuity
    coach = ctx.home_coaching_continuity - ctx.away_coaching_continuity
    roster = ctx.home_roster_continuity - ctx.away_roster_continuity
    raw = 0.75 * qb + 0.45 * coach + 0.30 * roster
    return _clip(raw, -MAX_CONTINUITY_POINTS, MAX_CONTINUITY_POINTS)


def market_points(ctx: PregameContext) -> float:
    """Translate pregame market movement into a bounded informational feature.

    This is evidence, not a governed probability. A later fitted model decides
    whether and how much predictive weight to assign it.
    """
    if ctx.market_home_prob_open is None or ctx.market_home_prob_current is None:
        return 0.0
    move = float(ctx.market_home_prob_current) - float(ctx.market_home_prob_open)
    return _clip(move * 10.0, -MAX_MARKET_POINTS, MAX_MARKET_POINTS)


def weather_points(ctx: PregameContext) -> float:
    """Small symmetric weather severity feature; sign is neutral for side models."""
    wind = max(0.0, float(ctx.wind_mph or 0.0) - 15.0) / 20.0
    precip = max(0.0, float(ctx.precipitation_prob or 0.0) - 0.50)
    temp = 0.0
    if ctx.temperature_f is not None:
        t = float(ctx.temperature_f)
        temp = max(0.0, 32.0 - t) / 40.0 + max(0.0, t - 90.0) / 40.0
    return _clip(wind + precip + temp, 0.0, 1.0)


def situational_points(ctx: PregameContext) -> float:
    """Explicitly capped tie-breaker feature for trends/scripts."""
    return _clip(ctx.situational_home_signal * MAX_SITUATIONAL_POINTS,
                 -MAX_SITUATIONAL_POINTS, MAX_SITUATIONAL_POINTS)


def build_early_season_features(
    *,
    week: int,
    prior_season: dict[str, Any],
    current_season: dict[str, Any] | None,
    league_means: dict[str, float],
    context: PregameContext,
) -> dict[str, Any]:
    """Create leakage-safe P3 features for one team-event row.

    Expected metric keys in prior/current dictionaries:
      off_epa_pp, def_epa_pp, success_rate, point_diff_pg, games
    Inputs are pregame snapshots only; callers own source certification and
    timestamp provenance.
    """
    context.validate()
    if int(week) != int(context.week):
        raise ValueError("WEEK_CONTEXT_MISMATCH")

    current_season = current_season or {}
    prior_games = int(prior_season.get("games") or 0)
    current_games = int(current_season.get("games") or 0)

    metrics: dict[str, float] = {}
    for key in ("off_epa_pp", "def_epa_pp", "success_rate", "point_diff_pg"):
        league_mean = float(league_means.get(key, 0.0))
        prior = shrink(prior_season.get(key), league_mean, prior_games)
        current_raw = current_season.get(key)
        current = None if current_raw is None else shrink(current_raw, league_mean, current_games, strength=3.0)
        metrics[f"prior_{key}_shrunk"] = prior
        metrics[f"current_{key}_shrunk"] = league_mean if current is None else current
        metrics[f"blended_{key}"] = blend_prior_current(prior, current, week)

    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "week": int(week),
        "prior_weight": prior_weight_for_week(week),
        "current_weight": 1.0 - prior_weight_for_week(week),
        "prior_games": prior_games,
        "current_games": current_games,
        **metrics,
        "continuity_points": continuity_points(context),
        "market_move_points": market_points(context),
        "weather_severity": weather_points(context),
        "situational_points": situational_points(context),
        "probability_publishable": False,
        "can_execute": False,
    }


def logistic_probability(score: float) -> float:
    """Utility for tests/research only; not a certified NFL probability."""
    return 1.0 / (1.0 + exp(-float(score)))
