"""
gate_engine/universal_agent/lanes/wnba_props/game_script/game_environment.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Game-Environment Distribution builder.

Extracts game-context features from the combined evidence dict and derives
a normalized script-prior distribution over 5 mutually exclusive game scripts.

WNBA total-line baseline: ~160 pts (80 + 80).
NBA  total-line baseline: ~225 pts (112.5 + 112.5).

Script priors are derived from:
  - spread magnitude  → blowout probability
  - spread direction  → which team is favoured
  - total O/U line    → pace/scoring environment
  - projected pace    → from enrichment if provided

Fail-closed: any KeyError or type error → SCRIPT_UNAVAILABLE sentinel.
can_execute = False
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Expected total-line baselines per sport
_WNBA_BASELINE = 160.0
_NBA_BASELINE  = 225.0

# Script identifiers (canonical)
SCRIPT_BLOWOUT_HOME  = "BLOWOUT_HOME"
SCRIPT_BLOWOUT_AWAY  = "BLOWOUT_AWAY"
SCRIPT_CLOSE_HIGH    = "CLOSE_HIGH"
SCRIPT_CLOSE_LOW     = "CLOSE_LOW"
SCRIPT_NEUTRAL       = "NEUTRAL_PACE"

ALL_SCRIPTS: tuple[str, ...] = (
    SCRIPT_BLOWOUT_HOME,
    SCRIPT_BLOWOUT_AWAY,
    SCRIPT_CLOSE_HIGH,
    SCRIPT_CLOSE_LOW,
    SCRIPT_NEUTRAL,
)


@dataclass(frozen=True)
class GameEnvironment:
    """
    Parsed game-context features.

    spread        Positive = home team favoured (in points).
    total_line    Over/Under line for total game score.
    baseline      Sport-specific expected total.
    sport         "WNBA" or "NBA".
    spread_magnitude  abs(spread).
    total_delta   total_line - baseline (positive = high-pace game).
    """
    spread:           float
    total_line:       float
    baseline:         float
    sport:            str
    spread_magnitude: float
    total_delta:      float
    # Evidence basis is explicit: neither path is a fitted or certified probability model.
    basis:            str = "MARKET_ONLY_UNFITTED_SHADOW"
    source_record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScriptPriors:
    """
    Normalized prior probability over all 5 game scripts.
    All priors sum to 1.0 (to 6 decimal places).
    """
    blowout_home: float
    blowout_away: float
    close_high:   float
    close_low:    float
    neutral:      float

    def as_dict(self) -> dict[str, float]:
        return {
            SCRIPT_BLOWOUT_HOME: self.blowout_home,
            SCRIPT_BLOWOUT_AWAY: self.blowout_away,
            SCRIPT_CLOSE_HIGH:   self.close_high,
            SCRIPT_CLOSE_LOW:    self.close_low,
            SCRIPT_NEUTRAL:      self.neutral,
        }

    def sum(self) -> float:
        return self.blowout_home + self.blowout_away + self.close_high + self.close_low + self.neutral


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)



def _utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp missing")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return timestamp.astimezone(timezone.utc)


def _finite_rating(value: Any, *, low: float, high: float) -> float:
    # Do not accept booleans, strings, NaN, inf, or extreme values as model evidence.
    if type(value) not in (int, float):
        raise ValueError("efficiency input must be numeric")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("efficiency input outside research bounds")
    return number


def _opponent_adjusted_environment(payload: Any, sport: str, baseline: float) -> GameEnvironment:
    """Construct an opponent-aware basketball research scenario from pregame ratings.

    Required offense/defense ratings are opponent/schedule-adjusted points per
    100 possessions, with defensive rating representing points ALLOWED. This
    symmetric interaction is an unfitted research baseline, NOT a calibrated
    scoring model. It deliberately does not read betting spreads or totals.
    """
    if sport not in ("NBA", "WNBA") or not isinstance(payload, dict):
        raise ValueError("opponent-aware research is basketball-only")

    home, away = payload["home"], payload["away"]
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise ValueError("team ratings missing")
    home_id, away_id = home["team_id"], away["team_id"]
    if not (isinstance(home_id, str) and home_id.strip()
            and isinstance(away_id, str) and away_id.strip()
            and home_id != away_id):
        raise ValueError("team identities missing or duplicated")

    # Source IDs are unverified provenance pointers, not data-rights attestation.
    sources = payload["source_record_ids"]
    if not isinstance(sources, dict) or any(
        not isinstance(sources.get(k), str) or not sources[k].strip()
        for k in ("home", "away", "league")
    ):
        raise ValueError("source record IDs missing")
    method = payload.get("opponent_adjustment_method")
    if not isinstance(method, str) or method.strip().lower() in ("", "raw", "none"):
        raise ValueError("opponent-adjustment method not declared")
    if _utc_timestamp(payload["as_of_utc"]) >= _utc_timestamp(payload["event_start_utc"]):
        raise ValueError("features not pregame")

    avg_offense = _finite_rating(payload["league_avg_off_rating"], low=30, high=200)
    ho = _finite_rating(home["adjusted_off_rating"], low=30, high=200)
    hd = _finite_rating(home["adjusted_def_rating"], low=30, high=200)
    ao = _finite_rating(away["adjusted_off_rating"], low=30, high=200)
    ad = _finite_rating(away["adjusted_def_rating"], low=30, high=200)
    pace_home = _finite_rating(home["pace"], low=40, high=160)
    pace_away = _finite_rating(away["pace"], low=40, high=160)

    # Equal baseline pace and schedule adjustment are placeholders for a
    # future independently fitted game-state / fatigue / possession specialist.
    possessions = (pace_home + pace_away) / 2.0
    home_points = possessions * (ho * ad / avg_offense) / 100.0
    away_points = possessions * (ao * hd / avg_offense) / 100.0
    research_spread = home_points - away_points   # positive: home favored
    research_total = home_points + away_points
    return GameEnvironment(
        spread=research_spread,
        total_line=research_total,
        baseline=baseline,
        sport=sport,
        spread_magnitude=abs(research_spread),
        total_delta=research_total - baseline,
        basis="OPPONENT_ADJUSTED_UNFITTED_SHADOW",
        source_record_ids=tuple(sources[k] for k in ("home", "away", "league")),
    )


def parse_game_environment(combined: dict) -> GameEnvironment | None:
    """
    Extract GameEnvironment from the combined evidence dict.
    Returns None if required fields are absent or invalid.

    Reads from matchup enrichment or top-level keys.
    """
    try:
        matchup = combined.get("matchup") or {}
        sport = (combined.get("sport") or "WNBA").strip().upper()
        baseline = _NBA_BASELINE if sport == "NBA" else _WNBA_BASELINE

        # Explicit opponent-aware payload wins over all market inputs. Invalid
        # payloads fail closed; never silently fall back to a betting line.
        if "opponent_adjusted" in matchup:
            return _opponent_adjusted_environment(
                matchup["opponent_adjusted"], sport, baseline,
            )

        # Legacy market-only shadow is preserved for existing consumers.
        # Spread: positive = home favoured
        spread_raw = (
            matchup.get("spread")
            or matchup.get("home_spread")
            or combined.get("spread")
        )
        if spread_raw is None:
            return None
        spread = float(spread_raw)

        # Total / O-U line
        total_raw = (
            matchup.get("total_line")
            or matchup.get("ou_line")
            or combined.get("total_line")
            or combined.get("ou_line")
        )
        if total_raw is None:
            return None
        total_line = float(total_raw)

        return GameEnvironment(
            spread=spread,
            total_line=total_line,
            baseline=baseline,
            sport=sport,
            spread_magnitude=abs(spread),
            total_delta=total_line - baseline,
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def derive_script_priors(env: GameEnvironment) -> ScriptPriors:
    """
    Derive normalized script prior distribution from a GameEnvironment.

    Algorithm
    ---------
    1. Blowout probability ∝ sigmoid(spread_magnitude / 5).
       Split between HOME and AWAY based on spread direction.
    2. Remaining probability allocated to CLOSE_HIGH, CLOSE_LOW, NEUTRAL
       based on total_delta relative to baseline.
    3. Normalise to sum exactly to 1.0.
    """
    # Step 1: total blowout mass
    blowout_total = _sigmoid(env.spread_magnitude / 5.0) * 0.45

    # Split: favoured side gets 70% of blowout mass
    if env.spread >= 0:
        p_bh = blowout_total * 0.70
        p_ba = blowout_total * 0.30
    else:
        p_bh = blowout_total * 0.30
        p_ba = blowout_total * 0.70

    remaining = 1.0 - p_bh - p_ba

    # Step 2: distribute remaining across CLOSE_HIGH / CLOSE_LOW / NEUTRAL
    if env.total_delta > 7.0:
        # high-pace game
        p_ch = remaining * 0.55
        p_cl = remaining * 0.15
        p_n  = remaining * 0.30
    elif env.total_delta < -7.0:
        # low-pace game
        p_ch = remaining * 0.15
        p_cl = remaining * 0.55
        p_n  = remaining * 0.30
    else:
        p_ch = remaining * 0.33
        p_cl = remaining * 0.33
        p_n  = remaining * 0.34

    # Step 3: normalise (guard against floating-point drift)
    total = p_bh + p_ba + p_ch + p_cl + p_n
    factor = 1.0 / total if total > 0 else 1.0

    return ScriptPriors(
        blowout_home=round(p_bh * factor, 8),
        blowout_away=round(p_ba * factor, 8),
        close_high=  round(p_ch * factor, 8),
        close_low=   round(p_cl * factor, 8),
        neutral=     round(p_n  * factor, 8),
    )
