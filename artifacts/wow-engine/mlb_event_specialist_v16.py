from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import numpy as np

MIN_SIMULATIONS = 50_000
MODEL_VERSION = "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1"
FEATURE_SCHEMA_VERSION = "MLB_V2D_CONTEXT_V1"
LINEUP_MODEL_VERSION = "MLB_LINEUP_PLATOON_SHRINK_V1"
WEATHER_MODEL_VERSION = "MLB_OFFICIAL_FEED_WEATHER_V1"
FAILURE_MODEL_VERSION = "MLB_FAILURE_REGIME_MIXTURE_V1"
BOUNDS_VERSION = "V2D_DYNAMIC_BOUND_PLUS_CONTEXT_HAIRCUT_V1"
TERMINAL_CEILING = "MODEL_QUALIFIED_HOLD"


class ProspectiveModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LineupAdjustment:
    ratio: float
    valid_hitters: int
    season_ops: float
    platoon_ops: float
    missing_ids: tuple[int, ...]


@dataclass(frozen=True)
class WeatherContext:
    factor: float
    disruption_probability: float
    temperature_f: float | None
    wind_mph: float | None
    wind_direction: str | None
    condition: str | None
    roof_type: str | None


def _clip(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def _logit(p: float) -> float:
    p = _clip(float(p), 1e-9, 1 - 1e-9)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _single(client: Any, table: str, filters: dict[str, Any], select: str = "*") -> dict[str, Any]:
    q = client.table(table).select(select)
    for key, value in filters.items():
        q = q.eq(key, value)
    rows = q.limit(1).execute().data or []
    if not rows:
        raise ProspectiveModelUnavailable(f"{table}:required_row_missing")
    return rows[0]


def _load_evidence(client: Any, bridge_payload: dict[str, Any]) -> dict[str, Any]:
    score_id = bridge_payload.get("score_snapshot_id")
    shadow_event_id = bridge_payload.get("shadow_event_id")
    if not score_id or not shadow_event_id:
        raise ProspectiveModelUnavailable("bridge_missing_score_identity")

    score = _single(client, "wow_mlb_forward_score_snapshots", {"score_snapshot_id": score_id})
    event = _single(client, "wow_mlb_forward_shadow_events", {"shadow_event_id": shadow_event_id})
    lineup_rows = (
        client.table("wow_mlb_forward_lineup_snapshots")
        .select("*")
        .eq("shadow_event_id", shadow_event_id)
        .eq("lineup_status", "CONFIRMED")
        .eq("strict_pregame_provenance", True)
        .order("captured_at", desc=True)
        .limit(1)
        .execute().data or []
    )
    if not lineup_rows:
        raise ProspectiveModelUnavailable("confirmed_strict_pregame_lineup_missing")
    lineup = lineup_rows[0]

    feature_rows = (
        client.table("wow_mlb_forward_feature_snapshots")
        .select("side,feature_names,feature_vector,hydration_status,created_at")
        .eq("shadow_event_id", shadow_event_id)
        .eq("hydration_status", "PASS")
        .execute().data or []
    )
    features = {str(r["side"]).upper(): r for r in feature_rows}
    if "HOME" not in features or "AWAY" not in features:
        raise ProspectiveModelUnavailable("feature_snapshots_missing")

    health = _single(client, "wow_mlb_v2d_calibration_health", {"spec_id": score["spec_id"]})
    if health.get("calibration_health_status") != "PASS":
        raise ProspectiveModelUnavailable("calibration_health_not_pass")
    cal = _single(client, "wow_mlb_v2d_intercept_calibration", {"calibration_id": score["calibration_id"]})
    dist = _single(client, "wow_mlb_v2b_distribution_state", {"distribution_id": score["distribution_id"]})
    artifact = client.rpc("wow_mlb_event_certified_model_artifact", {"p_feature_schema_version": FEATURE_SCHEMA_VERSION}).execute().data
    if not isinstance(artifact, dict) or not artifact.get("ok"):
        raise ProspectiveModelUnavailable("prospective_certified_artifact_missing")
    if artifact.get("lifecycle_state") not in {"PROSPECTIVE_CERTIFIED", "CHAMPION"}:
        raise ProspectiveModelUnavailable("artifact_lifecycle_not_eligible")
    return {"score": score, "event": event, "lineup": lineup, "features": features, "health": health, "calibration": cal, "distribution": dist, "artifact": artifact}


def _parse_feed(raw_body: str) -> dict[str, Any]:
    try:
        body = json.loads(raw_body)
    except Exception as exc:
        raise ProspectiveModelUnavailable("official_lineup_feed_invalid_json") from exc
    if not isinstance(body, dict):
        raise ProspectiveModelUnavailable("official_lineup_feed_invalid")
    return body


def _starter_hand(feed: dict[str, Any], pitcher_id: int | None) -> str:
    if not pitcher_id:
        raise ProspectiveModelUnavailable("starter_id_missing")
    player = (feed.get("gameData", {}).get("players", {}) or {}).get(f"ID{pitcher_id}", {})
    hand = ((player.get("pitchHand") or {}).get("code") or "").upper()
    if hand not in {"L", "R"}:
        raise ProspectiveModelUnavailable("starter_handedness_missing")
    return hand


def _fetch_player_stats(player_ids: list[int], season: int) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    ids = ",".join(str(x) for x in sorted(set(player_ids)))
    url = "https://statsapi.mlb.com/api/v1/people" f"?personIds={ids}" "&hydrate=stats(group=[hitting],type=[season,statSplits],sitCodes=vl,vr)" f"&season={season}"
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.get(url)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        raise ProspectiveModelUnavailable("official_player_platoon_stats_unavailable") from exc
    out: dict[int, dict[str, Any]] = {}
    for p in body.get("people", []) or []:
        if isinstance(p, dict) and p.get("id") is not None:
            out[int(p["id"])] = p
    return out


def _stat_float(stat: dict[str, Any], key: str) -> float | None:
    value = stat.get(key)
    if value in (None, "", "-.--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _player_ops(player: dict[str, Any], starter_hand: str) -> tuple[float | None, float | None, int]:
    season_ops = None
    split_ops = None
    split_pa = 0
    target = "vs Left" if starter_hand == "L" else "vs Right"
    for group in player.get("stats", []) or []:
        display = str((group.get("type") or {}).get("displayName") or "")
        for split in group.get("splits", []) or []:
            stat = split.get("stat") or {}
            if display == "season" and season_ops is None:
                season_ops = _stat_float(stat, "ops")
            elif display == "statSplits" and str((split.get("split") or {}).get("description") or "") == target:
                split_ops = _stat_float(stat, "ops")
                try:
                    split_pa = int(stat.get("plateAppearances") or 0)
                except (TypeError, ValueError):
                    split_pa = 0
    return season_ops, split_ops, split_pa


def _lineup_adjustment(batting_order: list[int], starter_hand: str, players: dict[int, dict[str, Any]]) -> LineupAdjustment:
    if len(batting_order) < 9:
        raise ProspectiveModelUnavailable("batting_order_incomplete")
    order_weights = np.asarray([1.08, 1.06, 1.04, 1.02, 1.00, 0.98, 0.96, 0.94, 0.92], dtype=float)
    order_weights /= order_weights.sum()
    season_vals, platoon_vals, weights, missing = [], [], [], []
    for idx, pid in enumerate(batting_order[:9]):
        player = players.get(int(pid))
        if not player:
            missing.append(int(pid)); continue
        season_ops, split_ops, split_pa = _player_ops(player, starter_hand)
        if season_ops is None or season_ops <= 0:
            missing.append(int(pid)); continue
        if split_ops is None or split_ops <= 0:
            effective = season_ops
        else:
            w = split_pa / (split_pa + 120.0)
            effective = w * split_ops + (1.0 - w) * season_ops
        season_vals.append(season_ops); platoon_vals.append(effective); weights.append(float(order_weights[idx]))
    if len(season_vals) < 7:
        raise ProspectiveModelUnavailable("lineup_platoon_evidence_too_thin")
    wv = np.asarray(weights, dtype=float); wv /= wv.sum()
    season_weighted = float(np.dot(wv, np.asarray(season_vals)))
    platoon_weighted = float(np.dot(wv, np.asarray(platoon_vals)))
    ratio = _clip(platoon_weighted / max(season_weighted, 1e-6), 0.93, 1.07)
    return LineupAdjustment(ratio, len(season_vals), season_weighted, platoon_weighted, tuple(missing))


def _weather_context(feed: dict[str, Any]) -> WeatherContext:
    gd = feed.get("gameData", {}) or {}; weather = gd.get("weather") or {}; venue = gd.get("venue") or {}; field = venue.get("fieldInfo") or {}
    roof = str(field.get("roofType") or "UNKNOWN"); condition = str(weather.get("condition") or "UNKNOWN")
    try: temp = float(weather.get("temp")) if weather.get("temp") is not None else None
    except (TypeError, ValueError): temp = None
    wind_text = str(weather.get("wind") or ""); match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mph(?:,\s*(.*))?", wind_text, re.I)
    wind_mph = float(match.group(1)) if match else None; wind_dir = match.group(2).strip() if match and match.group(2) else None
    if "closed" in roof.lower():
        factor, disruption = 1.0, 0.01
    else:
        temp_factor = 1.0 if temp is None else _clip(1.0 + 0.0015 * (temp - 70.0), 0.96, 1.05)
        wind_factor = 1.0
        if wind_mph is not None and wind_dir:
            d = wind_dir.lower()
            if "out" in d: wind_factor = _clip(1.0 + 0.0020 * wind_mph, 1.0, 1.04)
            elif "in" in d: wind_factor = _clip(1.0 - 0.0020 * wind_mph, 0.96, 1.0)
        factor = _clip(temp_factor * wind_factor, 0.94, 1.08)
        c = condition.lower(); disruption = 0.12 if any(x in c for x in ("rain", "drizzle", "shower", "storm")) else 0.02
    return WeatherContext(factor, disruption, temp, wind_mph, wind_dir, condition, roof)


def _feature_map(row: dict[str, Any]) -> dict[str, float]:
    names = row.get("feature_names") or []; vals = row.get("feature_vector") or []
    if len(names) != len(vals): raise ProspectiveModelUnavailable("feature_vector_mismatch")
    return {str(n): float(v) for n, v in zip(names, vals)}


def _starter_failure_probability(f: dict[str, float]) -> float:
    p = 0.075 + 0.022 * max(0.0, f.get("opp_starter_era", 4.2) - 3.8) + 0.35 * max(0.0, f.get("opp_starter_bb_rate", 0.09) - 0.09)
    p += 0.025 if f.get("opp_starter_prior_starts", 10.0) < 5 else 0.0
    p += 0.00025 * max(0.0, f.get("opp_starter_pitches_last3", 250.0) - 280.0)
    return _clip(p, 0.055, 0.22)


def _bullpen_failure_probability(f: dict[str, float]) -> float:
    p = 0.065 + 0.018 * max(0.0, f.get("opp_bp_era", 4.2) - 3.8) + 0.25 * max(0.0, f.get("opp_bp_bb_rate", 0.09) - 0.09)
    p += 0.00030 * max(0.0, f.get("opp_bp_pitches_3d", 180.0) - 190.0)
    return _clip(p, 0.05, 0.20)


def _defense_failure_probability(f: dict[str, float]) -> float:
    return _clip(0.025 + 0.055 * max(0.0, f.get("opp_errors_pg", 0.55)), 0.03, 0.10)


def _nb_draw(rng: np.random.Generator, mu: np.ndarray, alpha: float) -> np.ndarray:
    mu = np.maximum(mu, 1e-6)
    if alpha <= 1e-9: return rng.poisson(mu)
    return rng.poisson(rng.gamma(shape=1.0 / alpha, scale=alpha * mu))


def _failure_summary(favorite: str, home_won: np.ndarray, ties_after_9: np.ndarray, home_starter_fail: np.ndarray, away_starter_fail: np.ndarray, home_bp_fail: np.ndarray, away_bp_fail: np.ndarray, home_def_fail: np.ndarray, away_def_fail: np.ndarray, weather_disrupt: np.ndarray, home_runs9: np.ndarray, away_runs9: np.ndarray, normal_mask: np.ndarray, lineup_ratio_home: float, lineup_ratio_away: float) -> tuple[dict[str, Any], dict[str, Any]]:
    fav_is_home = favorite == "HOME"; fav_win = home_won if fav_is_home else ~home_won; fav_loss = ~fav_win
    starter_fail = home_starter_fail if fav_is_home else away_starter_fail; bullpen_fail = home_bp_fail if fav_is_home else away_bp_fail; defense_fail = home_def_fail if fav_is_home else away_def_fail
    lineup_ratio = lineup_ratio_home if fav_is_home else lineup_ratio_away; extra_loss = ties_after_9 & fav_loss; one_run_low = fav_loss & ((home_runs9 + away_runs9) <= 7) & (np.abs(home_runs9 - away_runs9) <= 1)
    jp = lambda mask: float(np.mean(mask))
    losses = {"STARTER_UNDERPERFORMANCE": jp(starter_fail & fav_loss), "BULLPEN_FAILURE": jp(bullpen_fail & fav_loss), "LOW_SCORING_ONE_RUN_VARIANCE": jp(one_run_low), "DEFENSE_OR_BASERUNNING_FAILURE": jp(defense_fail & fav_loss), "WEATHER_OR_DELAY_DISRUPTION": jp(weather_disrupt & fav_loss), "EXTRA_INNING_LOSS": jp(extra_loss)}
    largest = max(losses, key=losses.get); normal_prob = float(np.mean(fav_win[normal_mask])) if np.any(normal_mask) else float(np.mean(fav_win)); unconditional = float(np.mean(fav_win))
    favorite_json = {"schema_version": "WOW_V16_MLB_FAILURE_PATH_V1", "favorite_side": favorite, "regimes": [{"name": k, "loss_joint_probability": v} for k, v in losses.items()], "lineup_downgrade_regime_probability": 0.0 if lineup_ratio >= 0.99 else float(_clip((0.99 - lineup_ratio) * 3.0, 0.0, 0.12)), "normal_regime_probability": normal_prob, "unconditional_probability": unconditional, "largest_favorite_loss_path": largest, "favorite_failure_path_probability": float(np.mean(fav_loss & ~normal_mask))}
    dog_json = {"schema_version": "WOW_V16_MLB_UPSET_PATH_V1", "underdog_side": "AWAY" if favorite == "HOME" else "HOME", "baseline_win_probability": 1.0 - unconditional, "favorite_starter_failure_path": losses["STARTER_UNDERPERFORMANCE"], "favorite_bullpen_failure_path": losses["BULLPEN_FAILURE"], "variance_path": losses["LOW_SCORING_ONE_RUN_VARIANCE"], "late_game_or_extra_inning_path": losses["EXTRA_INNING_LOSS"], "favorite_failure_path": favorite_json["favorite_failure_path_probability"]}
    return favorite_json, dog_json


def _simulate(*, home_mu: float, away_mu: float, home_alpha: float, away_alpha: float, extra_home_win: float, lineup_home: LineupAdjustment, lineup_away: LineupAdjustment, weather: WeatherContext, home_features: dict[str, float], away_features: dict[str, float], seed: int, simulation_count: int, favorite: str) -> dict[str, Any]:
    if simulation_count < MIN_SIMULATIONS: raise ProspectiveModelUnavailable("simulation_count_below_50000")
    rng = np.random.default_rng(seed); n = int(simulation_count)
    home_sp_p = _starter_failure_probability(away_features); away_sp_p = _starter_failure_probability(home_features); home_bp_p = _bullpen_failure_probability(away_features); away_bp_p = _bullpen_failure_probability(home_features); home_def_p = _defense_failure_probability(away_features); away_def_p = _defense_failure_probability(home_features)
    home_sp_fail = rng.random(n) < home_sp_p; away_sp_fail = rng.random(n) < away_sp_p; home_bp_fail = rng.random(n) < home_bp_p; away_bp_fail = rng.random(n) < away_bp_p; home_def_fail = rng.random(n) < home_def_p; away_def_fail = rng.random(n) < away_def_p; weather_disrupt = rng.random(n) < weather.disruption_probability
    shared_sigma = 0.045 + 0.10 * weather.disruption_probability; shared_env = rng.lognormal(mean=-0.5 * shared_sigma**2, sigma=shared_sigma, size=n); shared_env *= np.where(weather_disrupt, rng.choice(np.asarray([0.84, 1.18]), size=n), 1.0)
    home_mu_vec = np.full(n, home_mu * lineup_home.ratio * weather.factor) * shared_env; away_mu_vec = np.full(n, away_mu * lineup_away.ratio * weather.factor) * shared_env
    home_mu_vec *= np.where(away_sp_fail, 1.34, 1.0) * np.where(away_bp_fail, 1.17, 1.0) * np.where(away_def_fail, 1.08, 1.0); away_mu_vec *= np.where(home_sp_fail, 1.34, 1.0) * np.where(home_bp_fail, 1.17, 1.0) * np.where(home_def_fail, 1.08, 1.0)
    home_runs9 = _nb_draw(rng, home_mu_vec, home_alpha); away_runs9 = _nb_draw(rng, away_mu_vec, away_alpha); ties = home_runs9 == away_runs9; home_won = home_runs9 > away_runs9
    if np.any(ties): home_won[ties] = rng.random(int(np.sum(ties))) < extra_home_win
    raw_home = float(np.mean(home_won)); normal = ~(home_sp_fail | away_sp_fail | home_bp_fail | away_bp_fail | home_def_fail | away_def_fail | weather_disrupt)
    fav_json, dog_json = _failure_summary(favorite, home_won, ties, home_sp_fail, away_sp_fail, home_bp_fail, away_bp_fail, home_def_fail, away_def_fail, weather_disrupt, home_runs9, away_runs9, normal, lineup_home.ratio, lineup_away.ratio)
    return {"raw_home_probability": raw_home, "raw_away_probability": 1.0 - raw_home, "projected_runs_home": float(np.mean(home_runs9)), "projected_runs_away": float(np.mean(away_runs9)), "tie_after_9_probability": float(np.mean(ties)), "home_wins_extras_given_tie": float(extra_home_win), "away_wins_extras_given_tie": float(1.0 - extra_home_win), "favorite_failure_paths": fav_json, "underdog_upset_path": dog_json, "regime_probabilities": {"home_starter_failure": home_sp_p, "away_starter_failure": away_sp_p, "home_bullpen_failure": home_bp_p, "away_bullpen_failure": away_bp_p, "home_defense_baserunning_failure": home_def_p, "away_defense_baserunning_failure": away_def_p, "weather_delay_disruption": weather.disruption_probability}}


def _seed_for_event(event_key: str, snapshot_id: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{event_key}|{snapshot_id}|{MODEL_VERSION}".encode()).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _bounds(score: dict[str, Any], calibrated_home: float, context_haircut: float) -> tuple[float, float, float, float]:
    base_cal = float(score["calibrated_home_probability"]); lower_w = max(0.04, base_cal - float(score["home_lower_bound"])); upper_w = max(0.04, float(score["home_upper_bound"]) - base_cal)
    h_lo = _clip(calibrated_home - lower_w - context_haircut, 0.001, 0.999); h_hi = _clip(calibrated_home + upper_w + context_haircut, 0.001, 0.999)
    if not h_lo < calibrated_home < h_hi: raise ProspectiveModelUnavailable("prospective_bounds_invalid")
    return h_lo, h_hi, 1.0 - h_hi, 1.0 - h_lo


def score_prospective_event(req: Any, bridge_payload: dict[str, Any], client: Any, *, stats_fetcher: Callable[[list[int], int], dict[int, dict[str, Any]]] = _fetch_player_stats, simulation_count: int = MIN_SIMULATIONS) -> dict[str, Any]:
    if bridge_payload.get("code") != "REAL_FITTED_MODEL_PATH_PROVEN": raise ProspectiveModelUnavailable("prospective_path_requires_held_fitted_baseline")
    evidence = _load_evidence(client, bridge_payload); score = evidence["score"]; event = evidence["event"]; lineup = evidence["lineup"]; health = evidence["health"]; cal = evidence["calibration"]; dist = evidence["distribution"]; artifact = evidence["artifact"]; feed = _parse_feed(lineup["raw_body"])
    home_order = [int(x) for x in lineup.get("home_batting_order") or []]; away_order = [int(x) for x in lineup.get("away_batting_order") or []]; season = int(str(event["official_date"])[:4]); player_stats = stats_fetcher(home_order + away_order, season)
    home_starter_hand = _starter_hand(feed, event.get("home_probable_pitcher_id")); away_starter_hand = _starter_hand(feed, event.get("away_probable_pitcher_id")); home_lineup = _lineup_adjustment(home_order, away_starter_hand, player_stats); away_lineup = _lineup_adjustment(away_order, home_starter_hand, player_stats); weather = _weather_context(feed)
    home_features = _feature_map(evidence["features"]["HOME"]); away_features = _feature_map(evidence["features"]["AWAY"])
    if getattr(req, "market_prior", None) is not None:
        favorite = "HOME" if float(req.market_prior.home_probability) >= float(req.market_prior.away_probability) else "AWAY"; market_prior_available = True
    else:
        favorite = "HOME" if float(score["calibrated_home_probability"]) >= 0.5 else "AWAY"; market_prior_available = False
    seed = _seed_for_event(str(req.event_key), str(req.source_snapshot_id)); sim = _simulate(home_mu=float(score["home_mu"]), away_mu=float(score["away_mu"]), home_alpha=float(dist["home_alpha_total"]), away_alpha=float(dist["away_alpha_total"]), extra_home_win=float(dist["extra_inning_home_win_probability"]), lineup_home=home_lineup, lineup_away=away_lineup, weather=weather, home_features=home_features, away_features=away_features, seed=seed, simulation_count=simulation_count, favorite=favorite)
    raw_home = sim["raw_home_probability"]; raw_away = sim["raw_away_probability"]; cal_home = _sigmoid(_logit(raw_home) + float(cal["intercept_shift"])); cal_away = 1.0 - cal_home
    missing_hitters = len(home_lineup.missing_ids) + len(away_lineup.missing_ids); context_haircut = _clip(0.010 + 0.20 * abs(home_lineup.ratio - 1.0) + 0.20 * abs(away_lineup.ratio - 1.0) + 0.08 * weather.disruption_probability + 0.005 * missing_hitters, 0.010, 0.045); h_lo, h_hi, a_lo, a_hi = _bounds(score, cal_home, context_haircut)
    model_ts = datetime.now(timezone.utc).isoformat(); latest_material = max(str(event.get("snapshot_timestamp") or ""), str(event.get("lineup_confirmed_at") or ""), str(lineup.get("captured_at") or "")); inputs_hash = hashlib.sha256(json.dumps({"score_snapshot_id": score["score_snapshot_id"], "lineup_identity_sha256": lineup.get("lineup_identity_sha256"), "lineup_home_ratio": home_lineup.ratio, "lineup_away_ratio": away_lineup.ratio, "weather": weather.__dict__, "artifact_id": artifact.get("artifact_id"), "simulation_seed": seed, "simulation_count": simulation_count}, sort_keys=True, default=str).encode()).hexdigest()
    result = {"status": "MODEL_SCORED_PROSPECTIVE", "code": "GOVERNED_MODEL_PROBABILITY_PROSPECTIVE", "controlling_specialist": "wow.mlb-game-win-probability-expert", "model_version": MODEL_VERSION, "feature_schema_version": FEATURE_SCHEMA_VERSION, "artifact_id": artifact.get("artifact_id"), "artifact_lifecycle_state": artifact.get("lifecycle_state"), "research_run_id": req.research_run_id, "event_key": req.event_key, "official_event_id": req.official_event_id, "source_snapshot_id": req.source_snapshot_id, "base_score_snapshot_id": score["score_snapshot_id"], "model_inputs_hash": inputs_hash, "model_timestamp": model_ts, "latest_material_update_timestamp": latest_material, "model_valid_after_latest_update": True, "simulation_seed": seed, "simulation_count": int(simulation_count), "projected_runs_home": sim["projected_runs_home"], "projected_runs_away": sim["projected_runs_away"], "tie_after_9_probability": sim["tie_after_9_probability"], "home_wins_extras_given_tie": sim["home_wins_extras_given_tie"], "away_wins_extras_given_tie": sim["away_wins_extras_given_tie"], "raw_home_probability": raw_home, "raw_away_probability": raw_away, "independent_home_probability": raw_home, "independent_away_probability": raw_away, "market_prior_available": market_prior_available, "market_prior_weight": 0.0, "calibrated_home_probability": cal_home, "calibrated_away_probability": cal_away, "calibrated_home_lower_bound": h_lo, "calibrated_home_upper_bound": h_hi, "calibrated_away_lower_bound": a_lo, "calibrated_away_upper_bound": a_hi, "calibration_method": str(cal["method"]), "calibration_version": str(cal["calibration_id"]), "calibration_training_n": int(cal["prior_games"]), "calibration_health_status": health["calibration_health_status"], "graded_forward_shadow_n": int(health.get("graded_shadow_n") or 0), "bounds_method_version": BOUNDS_VERSION, "context_uncertainty_haircut": context_haircut, "lineup_context": {"status": "CONFIRMED", "model_version": LINEUP_MODEL_VERSION, "home_starter_hand": home_starter_hand, "away_starter_hand": away_starter_hand, "home_lineup_ratio": home_lineup.ratio, "away_lineup_ratio": away_lineup.ratio, "home_valid_hitters": home_lineup.valid_hitters, "away_valid_hitters": away_lineup.valid_hitters, "home_missing_hitters": list(home_lineup.missing_ids), "away_missing_hitters": list(away_lineup.missing_ids), "lineup_identity_sha256": lineup.get("lineup_identity_sha256")}, "weather_context": {"model_version": WEATHER_MODEL_VERSION, "factor": weather.factor, "disruption_probability": weather.disruption_probability, "temperature_f": weather.temperature_f, "wind_mph": weather.wind_mph, "wind_direction": weather.wind_direction, "condition": weather.condition, "roof_type": weather.roof_type, "source": lineup.get("source_url"), "timestamp": lineup.get("captured_at")}, "regime_model_version": FAILURE_MODEL_VERSION, "regime_probabilities": sim["regime_probabilities"], "favorite_failure_paths_json": sim["favorite_failure_paths"], "largest_favorite_loss_path": sim["favorite_failure_paths"]["largest_favorite_loss_path"], "favorite_failure_path_probability": sim["favorite_failure_paths"]["favorite_failure_path_probability"], "underdog_upset_path_json": sim["underdog_upset_path"], "probability_audit_required": True, "final_event_governor_required": True, "model_probability_publishable": True, "probability_publishable": False, "rank_eligible": False, "terminal_ceiling": TERMINAL_CEILING, "terminal_label": TERMINAL_CEILING, "blockers": ["PROSPECTIVE_CERTIFICATION_CEILING"], "can_execute": False}
    persisted = client.table("wow_mlb_event_specialist_snapshots").insert({"research_run_id": req.research_run_id, "event_key": req.event_key, "official_event_id": req.official_event_id, "source_snapshot_id": req.source_snapshot_id, "base_score_snapshot_id": score["score_snapshot_id"], "artifact_id": artifact.get("artifact_id"), "model_version": MODEL_VERSION, "model_inputs_hash": inputs_hash, "model_timestamp": model_ts, "latest_material_update_timestamp": latest_material, "simulation_seed": seed, "simulation_count": int(simulation_count), "raw_home_probability": raw_home, "raw_away_probability": raw_away, "calibrated_home_probability": cal_home, "calibrated_away_probability": cal_away, "calibrated_home_lower_bound": h_lo, "calibrated_home_upper_bound": h_hi, "calibrated_away_lower_bound": a_lo, "calibrated_away_upper_bound": a_hi, "favorite_failure_paths_json": sim["favorite_failure_paths"], "underdog_upset_path_json": sim["underdog_upset_path"], "context_evidence_json": {"lineup_context": result["lineup_context"], "weather_context": result["weather_context"], "regime_probabilities": result["regime_probabilities"]}, "output_json": result, "terminal_ceiling": TERMINAL_CEILING, "model_probability_publishable": True, "probability_publishable": False, "can_execute": False}).execute().data or []
    if not persisted: raise ProspectiveModelUnavailable("immutable_specialist_snapshot_write_failed")
    result["specialist_snapshot_id"] = persisted[0]["specialist_snapshot_id"]; result["immutable_pregame_write"] = "PASS"; return result
