"""Sport-specific V17 team/event probability models for non-MLB LLP routes.

These models are deliberately independent of sportsbook prices. They consume
sporting evidence and return immutable probability packages with dynamic
uncertainty bounds. Market evidence remains downstream and can never substitute
for missing model inputs.

Numerical families:
- WNBA: Bradley-Terry season-strength specialist.
- NHL: Elo baseline + goalie/special-teams/OT simulation.
- Soccer: draw-preserving independent-Poisson 1X2 model.
- Tennis: surface/form -> Elo -> hold-rate -> H2H hierarchy.
- MMA: internally fitted Elo ratings from a frozen chronological fight ledger.

No function in this module can execute a wager.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import random
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
CALIBRATION_METHOD = "V17_DYNAMIC_CANDIDATE_UNCERTAINTY"
CALIBRATION_VERSION = "v1.0"

SPORT_VOLATILITY = {
    "WNBA": 0.09,
    "NHL": 0.12,
    "SOCCER": 0.13,
    "TENNIS": 0.09,
    "MMA": 0.14,
}

MODEL_SPECS: dict[str, dict[str, str]] = {
    "WNBA": {
        "specialist": "WNBA_GAME_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "WNBA_BRADLEY_TERRY_V1",
        "model_version": "wnba-game-winner-bt-v1.0",
        "outcome_space": "HOME_AWAY_WINNER",
    },
    "NHL": {
        "specialist": "NHL_GAME_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "NHL_ELO_GOALIE_SPECIAL_TEAMS_OT_V1",
        "model_version": "nhl-game-winner-elo-sim-v1.0",
        "outcome_space": "HOME_AWAY_INCLUDING_OT_SO",
    },
    "SOCCER": {
        "specialist": "SOCCER_1X2_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "SOCCER_INDEPENDENT_POISSON_1X2_V1",
        "model_version": "soccer-1x2-poisson-v1.0",
        "outcome_space": "HOME_DRAW_AWAY_1X2",
    },
    "TENNIS": {
        "specialist": "TENNIS_MATCH_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "TENNIS_MATCH_WINNER_SPECIALIST_V1",
        "model_version": "tennis-match-winner-v1.0",
        "outcome_space": "PLAYER_A_PLAYER_B_MATCH_WINNER",
    },
    "MMA": {
        "specialist": "MMA_FIGHT_WIN_PROBABILITY_EXPERT_V1",
        "model_family": "MMA_INTERNAL_ELO_FIGHT_WINNER_V1",
        "model_version": "mma-fight-winner-elo-v1.0",
        "outcome_space": "FIGHTER_A_FIGHTER_B_WINNER",
    },
}


class ModelInputsInsufficient(ValueError):
    code = "MODEL_INPUTS_INSUFFICIENT"

    def __init__(
        self,
        sport: str,
        missing: Iterable[str],
        reason: str = "SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT",
    ):
        self.sport = sport
        self.missing_fields = tuple(sorted(set(str(v) for v in missing if v)))
        self.reason = reason
        super().__init__(f"{sport}:{reason}:{','.join(self.missing_fields)}")


class ModelOutputInvalid(ValueError):
    code = "MODEL_OUTPUT_INVALID"


class ModelScorerFailed(RuntimeError):
    code = "MODEL_SCORER_FAILED"


def _evidence(req: Any) -> dict[str, Any]:
    value = getattr(req, "sport_specific_evidence", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def _required_number(
    evidence: Mapping[str, Any],
    key: str,
    sport: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    parsed = _number(evidence.get(key), minimum=minimum, maximum=maximum)
    if parsed is None:
        raise ModelInputsInsufficient(sport, [key])
    return parsed


def _probability(value: Any) -> float | None:
    return _number(value, minimum=0.000001, maximum=0.999999)


def _logistic(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _elo_probability(home_elo: float, away_elo: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo) / 400.0))


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_timestamp(req: Any, fallback: str) -> str:
    value = str(
        getattr(req, "latest_material_update_timestamp", "") or ""
    ).strip()
    return value or fallback


def _sample_size(evidence: Mapping[str, Any], *, fallback: int = 0) -> int:
    for key in (
        "sample_size",
        "effective_sample_n",
        "games_sampled",
        "matches_sampled",
    ):
        value = _number(evidence.get(key), minimum=0)
        if value is not None:
            return int(value)
    return int(fallback)


def _status_certainty_adjustment(
    sport: str,
    evidence: Mapping[str, Any],
) -> float:
    key = {
        "WNBA": "expected_starters_rotation",
        "NHL": "goalie_status",
        "SOCCER": "starting_xi_status",
        "TENNIS": "participant_status",
        "MMA": "weigh_in_status",
    }.get(sport)
    token = str(evidence.get(key, "") if key else "").strip().upper()
    if token in {"CONFIRMED", "ACTIVE", "OFFICIAL", "COMPLETE", "PASS"}:
        return -0.01
    if token in {"EXPECTED", "PROJECTED", "PROBABLE", "LIKELY"}:
        return -0.005
    if token in {"OUT", "SCRATCHED", "INACTIVE", "CANCELLED", "FAILED"}:
        return 0.03
    return 0.0


def _freshness_penalty(evidence: Mapping[str, Any]) -> float:
    age = _number(evidence.get("status_freshness_hours"), minimum=0)
    if age is None:
        return 0.005
    if age <= 1.0:
        return 0.0
    if age <= 4.0:
        return 0.01
    return 0.025


def _sample_penalty(n: int) -> float:
    if n >= 10:
        return 0.0
    if n <= 0:
        return 0.06
    return 0.06 * (1.0 - n / 10.0)


def _disagreement_factor(component_probabilities: Iterable[float]) -> float:
    values = [
        float(v)
        for v in component_probabilities
        if _probability(v) is not None
    ]
    if len(values) < 2:
        return 1.0
    spread = max(values) - min(values)
    if spread > 0.10:
        return 1.35
    if spread > 0.05:
        return 1.15
    return 1.0


def _dynamic_bounds(
    sport: str,
    probability: float,
    evidence: Mapping[str, Any],
    components: Mapping[str, float],
    *,
    fallback_sample_n: int = 0,
) -> dict[str, Any]:
    n = _sample_size(evidence, fallback=fallback_sample_n)
    raw_uncertainty = (
        SPORT_VOLATILITY[sport]
        + _sample_penalty(n)
        + _status_certainty_adjustment(sport, evidence)
        + _freshness_penalty(evidence)
    )
    widening = _disagreement_factor(components.values())
    uncertainty = max(0.01, min(0.30, raw_uncertainty * widening))
    p = max(0.01, min(0.99, float(probability)))
    return {
        "calibrated_probability": round(p, 6),
        "calibrated_lower_bound": round(max(0.01, p - uncertainty), 6),
        "calibrated_upper_bound": round(min(0.99, p + uncertainty), 6),
        "dynamic_uncertainty": round(uncertainty, 6),
        "calibration_method": CALIBRATION_METHOD,
        "calibration_version": CALIBRATION_VERSION,
        "calibration_sample_scope": f"{sport}_CANDIDATE_DYNAMIC_UNCERTAINTY",
        "effective_sample_n": n,
        "uncertainty_components": {
            "sport_volatility": SPORT_VOLATILITY[sport],
            "sample_size_penalty": round(_sample_penalty(n), 6),
            "status_certainty_adjustment": round(
                _status_certainty_adjustment(sport, evidence), 6
            ),
            "freshness_penalty": round(_freshness_penalty(evidence), 6),
            "model_disagreement_widening_factor": widening,
        },
    }


def _base_package(
    req: Any,
    sport: str,
    raw_home_probability: float,
    components: Mapping[str, float],
    *,
    evidence: Mapping[str, Any],
    fallback_sample_n: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spec = MODEL_SPECS[sport]
    model_at = _timestamp_now()
    source_at = _source_timestamp(req, model_at)
    raw_home = max(0.01, min(0.99, float(raw_home_probability)))
    bounds = _dynamic_bounds(
        sport,
        raw_home,
        evidence,
        components,
        fallback_sample_n=fallback_sample_n,
    )
    calibrated_home = bounds["calibrated_probability"]
    uncertainty = bounds["dynamic_uncertainty"]
    calibrated_away = round(1.0 - calibrated_home, 6)
    raw_away = round(1.0 - raw_home, 6)
    away_lower = round(max(0.01, calibrated_away - uncertainty), 6)
    away_upper = round(min(0.99, calibrated_away + uncertainty), 6)
    candidate_id = str(
        getattr(req, "event_key", "")
        or getattr(req, "official_event_id", "")
        or ""
    )
    prediction_id = (
        f"{getattr(req, 'research_run_id', 'run')}:"
        f"{candidate_id}:{sport}:{spec['model_version']}"
    )
    result: dict[str, Any] = {
        "candidate_id": candidate_id,
        "prediction_id": prediction_id,
        "sport": sport,
        "league": getattr(req, "league", sport),
        "official_event_id": str(
            getattr(req, "official_event_id", "") or ""
        ),
        "event_key": candidate_id,
        "event_start_time_utc": str(
            getattr(req, "event_start_time_utc", "") or ""
        ),
        "home_team": str(getattr(req, "home_team", "") or ""),
        "away_team": str(getattr(req, "away_team", "") or ""),
        "controlling_specialist": spec["specialist"],
        "model_family": spec["model_family"],
        "model_version": spec["model_version"],
        "model_artifact_version": spec["model_version"],
        "model_run_status": "PASS",
        "scorer_status": "PASS",
        "raw_model_probability": round(raw_home, 6),
        "independent_model_probability": round(raw_home, 6),
        "raw_home_probability": round(raw_home, 6),
        "raw_away_probability": raw_away,
        "calibrated_probability": calibrated_home,
        "calibrated_lower_bound": bounds["calibrated_lower_bound"],
        "calibrated_upper_bound": bounds["calibrated_upper_bound"],
        "calibrated_home_probability": calibrated_home,
        "calibrated_home_lower_bound": bounds["calibrated_lower_bound"],
        "calibrated_home_upper_bound": bounds["calibrated_upper_bound"],
        "calibrated_away_probability": calibrated_away,
        "calibrated_away_lower_bound": away_lower,
        "calibrated_away_upper_bound": away_upper,
        "calibration_method": bounds["calibration_method"],
        "calibration_version": bounds["calibration_version"],
        "calibration_sample_scope": bounds["calibration_sample_scope"],
        "calibration_health_status": "PASS",
        "effective_sample_n": bounds["effective_sample_n"],
        "dynamic_uncertainty": uncertainty,
        "uncertainty_method": (
            "SPORT_VOLATILITY_PLUS_SAMPLE_STATUS_FRESHNESS_AND_DISAGREEMENT"
        ),
        "uncertainty_components": bounds["uncertainty_components"],
        "model_components": {
            k: round(float(v), 6) for k, v in components.items()
        },
        "model_disagreement": (
            round(max(components.values()) - min(components.values()), 6)
            if len(components) > 1
            else 0.0
        ),
        "immutable_model_timestamp": model_at,
        "model_timestamp": model_at,
        "latest_material_update_timestamp": getattr(
            req, "latest_material_update_timestamp", None
        ),
        "source_snapshot_id": str(
            getattr(req, "source_snapshot_id", "") or ""
        ),
        "source_snapshot_timestamp": source_at,
        "outcome_space": spec["outcome_space"],
        "ranking_basis": "CALIBRATED_LOWER_BOUND",
        "rank_eligible": True,
        "probability_publishable": True,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "market_probability_used_as_model": False,
        "generic_reasoning_used_as_model": False,
        "can_execute": False,
    }
    if extra:
        result.update(dict(extra))
    return result


def score_wnba_team_event(req: Any) -> dict[str, Any]:
    sport = "WNBA"
    evidence = _evidence(req)
    home_wp = _required_number(
        evidence, "home_win_pct", sport, minimum=0.0, maximum=1.0
    )
    away_wp = _required_number(
        evidence, "away_win_pct", sport, minimum=0.0, maximum=1.0
    )
    denom = home_wp + away_wp
    if denom <= 0.0:
        raise ModelOutputInvalid("WNBA_BRADLEY_TERRY_DENOMINATOR_INVALID")
    p_bt = home_wp / denom
    rest_days = _number(evidence.get("rest_days"), minimum=0.0)
    if rest_days is None:
        rest_days = _number(evidence.get("rest_days_home"), minimum=0.0)
    if rest_days is not None and rest_days < 2.0:
        p_bt = max(0.01, p_bt - 0.02)
    components = {"wnba_bradley_terry_win_pct": p_bt}
    home_elo = _number(evidence.get("home_elo"))
    away_elo = _number(evidence.get("away_elo"))
    if home_elo is not None and away_elo is not None:
        components["elo_differential"] = _elo_probability(home_elo, away_elo)
        p_bt = (
            0.35 * components["wnba_bradley_terry_win_pct"]
            + 0.30 * components["elo_differential"]
        ) / 0.65
    return _base_package(req, sport, p_bt, components, evidence=evidence)


def _nhl_seed(req: Any) -> int:
    token = str(
        getattr(req, "official_event_id", "")
        or getattr(req, "event_key", "")
        or "NHL"
    )
    return int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:16], 16)


def score_nhl_team_event(req: Any) -> dict[str, Any]:
    sport = "NHL"
    evidence = _evidence(req)
    home_elo = _required_number(evidence, "home_elo", sport)
    away_elo = _required_number(evidence, "away_elo", sport)
    home_sv = _required_number(
        evidence, "home_goalie_sv_pct", sport, minimum=0.75, maximum=1.0
    )
    away_sv = _required_number(
        evidence, "away_goalie_sv_pct", sport, minimum=0.75, maximum=1.0
    )
    home_pp = _required_number(
        evidence, "home_pp_pct", sport, minimum=0.0, maximum=1.0
    )
    away_pk = _required_number(
        evidence, "away_pk_pct", sport, minimum=0.0, maximum=1.0
    )
    base_prob = _elo_probability(home_elo, away_elo)
    rng = random.Random(_nhl_seed(req))
    n_sims = int(
        _number(
            evidence.get("simulation_count"), minimum=1000, maximum=50000
        )
        or 5000
    )
    ot_freq = 0.24
    sv_diff = home_sv - away_sv
    pp_adj = (home_pp - (1.0 - away_pk)) * 0.5
    sim_probs: list[float] = []
    ot_count = 0
    for _ in range(n_sims):
        logit = math.log(base_prob / (1.0 - base_prob + 1e-12))
        goalie_adj = sv_diff * 3.0 + rng.gauss(0.0, 0.05)
        ot_adj = 0.0
        if rng.random() < ot_freq:
            ot_count += 1
            ot_adj = (0.50 - base_prob) * 0.30 + rng.gauss(0.0, 0.03)
        sim_probs.append(_logistic(logit + goalie_adj + pp_adj + ot_adj))
    adjusted = sum(sim_probs) / len(sim_probs)
    components = {
        "nhl_elo_baseline": base_prob,
        "nhl_goalie_special_teams_ot_simulation": adjusted,
    }
    return _base_package(
        req,
        sport,
        adjusted,
        components,
        evidence=evidence,
        extra={
            "simulation_count": n_sims,
            "simulation_regimes": {
                "overtime_frequency": round(ot_count / n_sims, 6)
            },
        },
    )


def _poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def score_soccer_team_event(req: Any) -> dict[str, Any]:
    sport = "SOCCER"
    evidence = _evidence(req)
    home_xg = _required_number(
        evidence, "home_xg_per_game", sport, minimum=0.05, maximum=6.0
    )
    away_xg = _required_number(
        evidence, "away_xg_per_game", sport, minimum=0.05, maximum=6.0
    )
    max_goals = int(
        _number(evidence.get("poisson_goal_cap"), minimum=7, maximum=14)
        or 10
    )
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    mass = 0.0
    for home_goals in range(max_goals + 1):
        ph = _poisson_pmf(home_xg, home_goals)
        for away_goals in range(max_goals + 1):
            p = ph * _poisson_pmf(away_xg, away_goals)
            mass += p
            if home_goals > away_goals:
                p_home += p
            elif home_goals == away_goals:
                p_draw += p
            else:
                p_away += p
    if mass <= 0.0:
        raise ModelOutputInvalid("SOCCER_POISSON_MASS_INVALID")
    p_home /= mass
    p_draw /= mass
    p_away /= mass
    components = {"soccer_poisson_home_win": p_home}
    result = _base_package(req, sport, p_home, components, evidence=evidence)
    uncertainty = float(result["dynamic_uncertainty"])
    result.update(
        {
            "raw_draw_probability": round(p_draw, 6),
            "raw_away_probability": round(p_away, 6),
            "calibrated_draw_probability": round(p_draw, 6),
            "calibrated_draw_lower_bound": round(
                max(0.01, p_draw - uncertainty), 6
            ),
            "calibrated_draw_upper_bound": round(
                min(0.99, p_draw + uncertainty), 6
            ),
            "calibrated_away_probability": round(p_away, 6),
            "calibrated_away_lower_bound": round(
                max(0.01, p_away - uncertainty), 6
            ),
            "calibrated_away_upper_bound": round(
                min(0.99, p_away + uncertainty), 6
            ),
            "three_state_1x2": {
                "home": round(p_home, 6),
                "draw": round(p_draw, 6),
                "away": round(p_away, 6),
            },
            "poisson_goal_cap": max_goals,
            "poisson_mass_covered": round(mass, 8),
        }
    )
    return result


def score_tennis_team_event(req: Any) -> dict[str, Any]:
    sport = "TENNIS"
    evidence = _evidence(req)
    components: dict[str, float] = {}
    raw: float | None = None
    surface_form = _probability(evidence.get("surface_adjusted_form"))
    if surface_form is not None:
        raw = surface_form
        components["surface_adjusted_form"] = surface_form
    else:
        home_elo = _number(evidence.get("home_elo"))
        away_elo = _number(evidence.get("away_elo"))
        if home_elo is not None and away_elo is not None:
            raw = _elo_probability(home_elo, away_elo)
            components["tennis_elo"] = raw
        else:
            hold = _probability(evidence.get("hold_rate"))
            opp_hold = _probability(evidence.get("opp_hold_rate"))
            if (
                hold is not None
                and opp_hold is not None
                and hold + opp_hold > 0.0
            ):
                raw = hold / (hold + opp_hold)
                components["hold_rate_dominance"] = raw
            else:
                h2h = _probability(evidence.get("h2h_win_rate")) or _probability(
                    evidence.get("h2h_win_pct")
                )
                if h2h is not None:
                    raw = h2h
                    components["h2h_win_rate"] = raw
    if raw is None:
        raise ModelInputsInsufficient(
            sport,
            [
                "surface_adjusted_form OR home_elo+away_elo OR "
                "hold_rate+opp_hold_rate OR h2h_win_rate"
            ],
            reason="TENNIS_SPECIALIST_NUMERIC_INPUT_PATH_UNAVAILABLE",
        )
    return _base_package(req, sport, raw, components, evidence=evidence)


def _fit_mma_elo(
    history: list[dict[str, Any]],
    home: str,
    away: str,
) -> tuple[float, dict[str, Any]]:
    ratings: dict[str, float] = {}
    appearances: dict[str, int] = {}
    used = 0
    k_factor = 24.0
    for fight in history:
        if not isinstance(fight, dict):
            continue
        fighter_a = str(
            fight.get("fighter_a") or fight.get("red_corner") or ""
        ).strip()
        fighter_b = str(
            fight.get("fighter_b") or fight.get("blue_corner") or ""
        ).strip()
        winner = str(fight.get("winner") or "").strip()
        if (
            not fighter_a
            or not fighter_b
            or not winner
            or fighter_a == fighter_b
        ):
            continue
        if winner not in {fighter_a, fighter_b}:
            continue
        ra = ratings.setdefault(fighter_a, 1500.0)
        rb = ratings.setdefault(fighter_b, 1500.0)
        expected_a = _elo_probability(ra, rb)
        score_a = 1.0 if winner == fighter_a else 0.0
        delta = k_factor * (score_a - expected_a)
        ratings[fighter_a] = ra + delta
        ratings[fighter_b] = rb - delta
        appearances[fighter_a] = appearances.get(fighter_a, 0) + 1
        appearances[fighter_b] = appearances.get(fighter_b, 0) + 1
        used += 1
    if home not in ratings or away not in ratings:
        raise ModelInputsInsufficient(
            "MMA",
            ["fight_history containing both current fighters"],
            reason="MMA_ELO_CURRENT_FIGHTER_HISTORY_UNAVAILABLE",
        )
    if appearances.get(home, 0) < 3 or appearances.get(away, 0) < 3:
        raise ModelInputsInsufficient(
            "MMA",
            ["minimum_3_completed_fights_per_current_fighter"],
            reason="MMA_ELO_SAMPLE_TOO_SMALL",
        )
    probability = _elo_probability(ratings[home], ratings[away])
    return probability, {
        "home_elo": round(ratings[home], 4),
        "away_elo": round(ratings[away], 4),
        "home_fights_in_ledger": appearances.get(home, 0),
        "away_fights_in_ledger": appearances.get(away, 0),
        "history_fights_used": used,
        "elo_k_factor": k_factor,
    }


def score_mma_team_event(req: Any) -> dict[str, Any]:
    sport = "MMA"
    evidence = _evidence(req)
    history = evidence.get("fight_history")
    if not isinstance(history, list) or not history:
        raise ModelInputsInsufficient(
            sport,
            ["fight_history"],
            reason="MMA_INTERNAL_FITTED_ELO_LEDGER_UNAVAILABLE",
        )
    home = str(getattr(req, "home_team", "") or "").strip()
    away = str(getattr(req, "away_team", "") or "").strip()
    if not home or not away:
        raise ModelInputsInsufficient(sport, ["home_team", "away_team"])
    raw, fit = _fit_mma_elo(history, home, away)
    components = {"mma_internal_fitted_elo": raw}
    return _base_package(
        req,
        sport,
        raw,
        components,
        evidence=evidence,
        fallback_sample_n=min(
            fit["home_fights_in_ledger"], fit["away_fights_in_ledger"]
        ),
        extra={
            "mma_elo_fit": fit,
            "model_fit_scope": (
                "FROZEN_CHRONOLOGICAL_FIGHT_HISTORY_SUPPLIED_BY_BACKEND"
            ),
        },
    )


MODEL_SCORERS = {
    "WNBA": score_wnba_team_event,
    "NHL": score_nhl_team_event,
    "SOCCER": score_soccer_team_event,
    "TENNIS": score_tennis_team_event,
    "MMA": score_mma_team_event,
}


def score_multisport_team_event(req: Any, sport: str) -> dict[str, Any]:
    normalized = str(sport or "").strip().upper()
    scorer = MODEL_SCORERS.get(normalized)
    if scorer is None:
        raise ModelInputsInsufficient(
            normalized or "UNKNOWN",
            ["registered_multisport_scorer"],
            reason="MODEL_ROUTE_UNSUPPORTED",
        )
    return scorer(req)


__all__ = [
    "CALIBRATION_METHOD",
    "CALIBRATION_VERSION",
    "CAN_EXECUTE",
    "MODEL_SCORERS",
    "MODEL_SPECS",
    "ModelInputsInsufficient",
    "ModelOutputInvalid",
    "ModelScorerFailed",
    "score_mma_team_event",
    "score_multisport_team_event",
    "score_nhl_team_event",
    "score_soccer_team_event",
    "score_tennis_team_event",
    "score_wnba_team_event",
]
