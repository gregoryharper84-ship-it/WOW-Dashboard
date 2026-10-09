"""Research-only NHL goalie/shot/special-teams challenger; never a serving scorer.

All additional features are pre-registered and reconstructed from *prior* settled
games. V1 is refit on the same boxscore-complete intersection, with identical
chronological 60/20/20 partitions. No model release, DB write or wagering path.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import time
from typing import Any, Mapping, Sequence

import numpy as np
import requests
from sklearn.metrics import brier_score_loss, log_loss

from nhl_candidate_pipeline import (
    FEATURE_NAMES as V1_FEATURES, NHLGame, NHLCandidateError,
    _aware, fetch_historical_games, reconstruct_training_rows,
)
from v17.binary_candidate_lifecycle import (
    BinaryCandidate, BinaryTrainingRow, _ece, _map_calibrator,
    train_binary_candidate,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION_ALLOWED = False
FAMILY = "NHL_GOALIE_SHOT_CHALLENGER_V1"
SPEC_VERSION = "PRE_REGISTERED_2026_10_09"
BOX_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"
WINDOW = 10
GOALIE_PRIOR_SHOTS = 300
GOALIE_PRIOR_SV = 0.900
BOOTSTRAPS = 2000
ADDED_FEATURES = (
    "shot_share_l10_delta", "projected_starter_sv_delta",
    "pp_goals_for_l10_delta", "pp_goals_against_l10_delta",
)


@dataclass(frozen=True)
class Box:
    event_id: str
    season: int
    home: str
    away: str
    home_sog: int
    away_sog: int
    home_pp: int
    away_pp: int
    home_goalie: str
    away_goalie: str
    home_saves: int
    away_saves: int
    home_shots_against: int
    away_shots_against: int
    source_hash: str


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _natural(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NHLCandidateError("NHL_BOX_SCHEMA_DRIFT", field)
    return value


def parse_box(game: NHLGame, data: Mapping[str, Any]) -> Box:
    """Validate official boxscore identity and all numeric data; no silent defaults."""
    try:
        if str(data.get("id")) != game.game_id or int(data["season"]) != game.season_id:
            raise NHLCandidateError("NHL_BOX_IDENTITY_MISMATCH", game.game_id)
        if data.get("gameState") not in ("FINAL", "OFF") or int(data.get("gameType", -1)) != 2:
            raise NHLCandidateError("NHL_BOX_NOT_FINAL", game.game_id)
        teams = data["playerByGameStats"]
        info = []
        for side, code in (("home", game.home_team), ("away", game.away_team)):
            public = data[side + "Team"]
            if str(public["abbrev"]).upper() != code:
                raise NHLCandidateError("NHL_BOX_IDENTITY_MISMATCH", game.game_id + ":" + side)
            sog = _natural(public["sog"], side + "_sog")
            players = teams[side + "Team"]
            goalies = players["goalies"]
            if not isinstance(goalies, list) or not goalies:
                raise NHLCandidateError("NHL_BOX_SCHEMA_DRIFT", side + "_goalies")
            starters = [g for g in goalies if g.get("starter") is True]
            if len(starters) != 1:
                raise NHLCandidateError("NHL_BOX_STARTER_AMBIGUOUS", game.game_id + ":" + side)
            starter = starters[0]
            goalie_id = str(_natural(starter["playerId"], side + "_player_id"))
            saves = _natural(starter["saves"], side + "_saves")
            against = _natural(starter["shotsAgainst"], side + "_shots_against")
            if saves > against:
                raise NHLCandidateError("NHL_BOX_SCHEMA_DRIFT", "saves_gt_shots")
            skaters = players["forwards"] + players["defense"]
            if not isinstance(players["forwards"], list) or not isinstance(players["defense"], list):
                raise NHLCandidateError("NHL_BOX_SCHEMA_DRIFT", side + "_skaters")
            pp = sum(_natural(p["powerPlayGoals"], side + "_pp") for p in skaters)
            info.append((sog, pp, goalie_id, saves, against))
        h, a = info
        return Box(
            event_id=game.game_id, season=game.season_id,
            home=game.home_team, away=game.away_team,
            home_sog=h[0], away_sog=a[0], home_pp=h[1], away_pp=a[1],
            home_goalie=h[2], away_goalie=a[2],
            home_saves=h[3], away_saves=a[3],
            home_shots_against=h[4], away_shots_against=a[4],
            source_hash=_hash(data),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise NHLCandidateError("NHL_BOX_SCHEMA_DRIFT", game.game_id + ":" + type(exc).__name__) from exc


def fetch_box(game: NHLGame, *, session: Any = requests, retries: int = 4,
              sleep: Any = time.sleep) -> Box:
    url = BOX_URL.format(game_id=game.game_id)
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=20, headers={"User-Agent": "WOW-V17-NHL-Research/1.0"})
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise NHLCandidateError("NHL_BOX_TRANSPORT_FAILED", game.game_id) from exc
            sleep(min(2 ** attempt, 8))
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            if attempt == retries - 1:
                raise NHLCandidateError("NHL_BOX_HTTP_" + str(response.status_code), game.game_id)
            retry_after = response.headers.get("Retry-After", "")
            try:
                pause = min(30.0, max(1.0, float(retry_after))) if retry_after else min(2 ** attempt, 8)
            except ValueError:
                pause = min(2 ** attempt, 8)
            sleep(pause)
            continue
        if response.status_code != 200:
            raise NHLCandidateError("NHL_BOX_HTTP_" + str(response.status_code), game.game_id)
        try:
            data = response.json()
        except ValueError as exc:
            raise NHLCandidateError("NHL_BOX_JSON_INVALID", game.game_id) from exc
        if not isinstance(data, Mapping):
            raise NHLCandidateError("NHL_BOX_SCHEMA_DRIFT", game.game_id)
        return parse_box(game, data)
    raise NHLCandidateError("NHL_BOX_RETRIES_EXHAUSTED", game.game_id)


def _shot_share(history: Sequence[tuple[int, int, int, int]]) -> float:
    own = sum(r[0] for r in history)
    opp = sum(r[1] for r in history)
    return own / (own + opp) if own + opp else 0.5


def _pp_rate(history: Sequence[tuple[int, int, int, int]], index: int) -> float:
    return sum(r[index] for r in history) / len(history) if history else 0.0


def _projected_goalie(starts: Sequence[str], is_b2b: bool) -> str:
    if not starts:
        return ""
    last = starts[-1]
    if is_b2b:
        other = next((g for g in reversed(starts) if g != last), None)
        if other:
            return other
    return last


def _goalie_save_pct(goalie: str, counts: Mapping[str, tuple[int, int]]) -> float:
    saves, against = counts.get(goalie, (0, 0))
    return (saves + GOALIE_PRIOR_SHOTS * GOALIE_PRIOR_SV) / (against + GOALIE_PRIOR_SHOTS)


def augment_rows(
    games: Sequence[NHLGame], boxes: Mapping[str, Box],
    v1_rows: Sequence[BinaryTrainingRow],
) -> tuple[list[BinaryTrainingRow], list[BinaryTrainingRow]]:
    """Build prior-only features. Hold updates until every same-start game is scored."""
    universe = {g.game_id for g in games}
    if len(universe) != len(games) or not set(boxes).issubset(universe):
        raise NHLCandidateError("NHL_BOX_GAME_RECONCILIATION_FAILED", "duplicated or unknown game")
    v1 = {r.event_id: r for r in v1_rows}
    if len(v1) != len(v1_rows):
        raise NHLCandidateError("NHL_V1_IDENTITY_DUPLICATE", "duplicate reconstructed event")
    histories: dict[tuple[int, str], deque[tuple[int, int, int, int]]] = defaultdict(lambda: deque(maxlen=WINDOW))
    goalie_starts: dict[tuple[int, str], list[str]] = defaultdict(list)
    goalie_stats: dict[tuple[int, str], dict[str, tuple[int, int]]] = defaultdict(dict)
    last_start: dict[tuple[int, str], datetime] = {}
    created: dict[str, BinaryTrainingRow] = {}
    ordered = sorted(games, key=lambda g: (g.event_start_time, g.game_id))
    for start_key in sorted({g.event_start_time for g in ordered}):
        batch = [g for g in ordered if g.event_start_time == start_key]
        for game in batch:
            b = boxes.get(game.game_id)
            if b is None:
                continue  # explicit coverage gap; neither candidate trains on this event
            if (b.season, b.home, b.away) != (game.season_id, game.home_team, game.away_team):
                raise NHLCandidateError("NHL_BOX_IDENTITY_MISMATCH", game.game_id)
            prior = v1.get(game.game_id)
            if prior is None:
                continue
            home = (game.season_id, game.home_team)
            away = (game.season_id, game.away_team)
            now = _aware(game.event_start_time)
            def projected(key: tuple[int, str]) -> float:
                previous = last_start.get(key)
                b2b = previous is not None and 0 < (now - previous).total_seconds() <= 36 * 3600
                selected = _projected_goalie(goalie_starts[key], b2b)
                return _goalie_save_pct(selected, goalie_stats[key])
            extra = {
                "shot_share_l10_delta": _shot_share(histories[home]) - _shot_share(histories[away]),
                "projected_starter_sv_delta": projected(home) - projected(away),
                "pp_goals_for_l10_delta": _pp_rate(histories[home], 2) - _pp_rate(histories[away], 2),
                "pp_goals_against_l10_delta": _pp_rate(histories[home], 3) - _pp_rate(histories[away], 3),
            }
            # V1's research manifest includes the current completed schedule
            # payload SHA (postgame outcome provenance), so it MUST NOT enter
            # this pregame feature manifest. Hash only the chronological V1
            # feature vector and its pre-start as-of identity instead.
            manifest = _hash({"v1_features": _hash(prior.features),
                              "v1_feature_as_of": prior.feature_as_of,
                              "v1_event_id": prior.event_id,
                              "version": SPEC_VERSION,
                              "home_prior": list(histories[home]), "away_prior": list(histories[away]),
                              "home_starts": goalie_starts[home], "away_starts": goalie_starts[away],
                              "goalie_status": "PROJECTED", "goalie_projection_policy": "LAST_START_OR_B2B_ALTERNATE_V1",
                              "home_goalie_counts": goalie_stats[home], "away_goalie_counts": goalie_stats[away],
                              # Never include the CURRENT game's settled boxscore
                              # in any pregame feature-row manifest. Target/source
                              # grade evidence remains separate from model inputs.
                              "reconstruction": "prior_settled_only"})
            created[game.game_id] = BinaryTrainingRow(
                event_id=prior.event_id, event_start_time=prior.event_start_time,
                feature_as_of=prior.feature_as_of, positive_outcome=prior.positive_outcome,
                features={**prior.features, **extra}, source_manifest_sha256=manifest)
        # Same-time batch never observes any other game's result.
        for game in batch:
            b = boxes.get(game.game_id)
            if b is None:
                continue  # do not invent past boxscore-derived history
            home, away = (game.season_id, game.home_team), (game.season_id, game.away_team)
            if home == away or home in last_start and last_start[home] >= _aware(game.event_start_time):
                raise NHLCandidateError("NHL_GAME_TIME_CONFLICT", game.game_id)
            for side, key in (("home", home), ("away", away)):
                sog, opp_sog = (b.home_sog, b.away_sog) if side == "home" else (b.away_sog, b.home_sog)
                pp, opp_pp = (b.home_pp, b.away_pp) if side == "home" else (b.away_pp, b.home_pp)
                histories[key].append((sog, opp_sog, pp, opp_pp))
                goalie = b.home_goalie if side == "home" else b.away_goalie
                saves = b.home_saves if side == "home" else b.away_saves
                shots = b.home_shots_against if side == "home" else b.away_shots_against
                prev_saves, prev_shots = goalie_stats[key].get(goalie, (0, 0))
                goalie_stats[key][goalie] = (prev_saves + saves, prev_shots + shots)
                goalie_starts[key].append(goalie)
                last_start[key] = _aware(game.event_start_time)
    common = [r for r in v1_rows if r.event_id in created]
    enriched = [created[r.event_id] for r in common]
    if len(common) < 300 or len(common) != len(enriched):
        raise NHLCandidateError("NHL_COMPARISON_INTERSECTION_INCOMPLETE", str(len(common)))
    return common, enriched


def artifact_predict(candidate: BinaryCandidate, rows: Sequence[BinaryTrainingRow]) -> np.ndarray:
    a = candidate.artifact_payload
    names = tuple(a["feature_names"])
    matrix = np.asarray([[row.features[name] for name in names] for row in rows], dtype=float)
    mean = np.asarray(a["scaler_mean"], dtype=float)
    scale = np.asarray(a["scaler_scale"], dtype=float)
    coefficients = np.asarray(a["coefficients"], dtype=float)
    logit = ((matrix - mean) / scale) @ coefficients + float(a["intercept"])
    raw = np.where(logit >= 0, 1.0 / (1.0 + np.exp(-np.clip(logit, -700, 700))),
                   np.exp(np.clip(logit, -700, 700)) / (1.0 + np.exp(np.clip(logit, -700, 700))))
    return _map_calibrator(raw, candidate.calibrator_payload)


def replay(games: Sequence[NHLGame], boxes: Mapping[str, Box], *, bootstrap: int = BOOTSTRAPS) -> dict[str, Any]:
    v1_rows, _ = reconstruct_training_rows(games)
    common, enriched = augment_rows(games, boxes, v1_rows)
    v1 = train_binary_candidate(common, model_family="NHL_V1_SAME_COHORT_REPLAY",
                                feature_names=V1_FEATURES, min_rows=300)
    v2 = train_binary_candidate(enriched, model_family=FAMILY,
                                feature_names=(*V1_FEATURES, *ADDED_FEATURES), min_rows=300)
    start = int(len(common) * 0.8)
    assert start == int(v1.artifact_payload["calibration_end_index"]) == int(v2.artifact_payload["calibration_end_index"])
    y = np.asarray([int(r.positive_outcome) for r in common[start:]], dtype=int)
    p1, p2 = artifact_predict(v1, common[start:]), artifact_predict(v2, enriched[start:])
    err1, err2 = (p1 - y) ** 2, (p2 - y) ** 2
    diff = err1 - err2
    # Pair both metrics on identical untouched historical outcomes.
    # No fitting, test labels, or market prices influence bootstrap sampling.
    clipped1, clipped2 = np.clip(p1, 1e-6, 1.0 - 1e-6), np.clip(p2, 1e-6, 1.0 - 1e-6)
    log1 = -(y * np.log(clipped1) + (1 - y) * np.log1p(-clipped1))
    log2 = -(y * np.log(clipped2) + (1 - y) * np.log1p(-clipped2))
    ll_diff = log1 - log2
    if bootstrap < 100:
        raise ValueError("bootstrap must be at least 100")
    rng = np.random.default_rng(20261009)
    indices = rng.integers(0, len(y), size=(bootstrap, len(y)))
    means = np.mean(diff[indices], axis=1)
    ci = np.quantile(means, [0.025, 0.975])
    ll_ci = np.quantile(np.mean(ll_diff[indices], axis=1), [0.025, 0.975])
    score = lambda p: {"brier": float(brier_score_loss(y, p)),
                       "log_loss": float(log_loss(y, p, labels=[0, 1])),
                       "ece": float(_ece(p, y))}
    s1, s2 = score(p1), score(p2)
    delta_ll = s1["log_loss"] - s2["log_loss"]
    hold_reasons = []
    if ci[0] <= 0:
        hold_reasons.append("BRIER_IMPROVEMENT_NOT_PROVEN")
    if ll_ci[0] <= 0:
        hold_reasons.append("LOG_LOSS_IMPROVEMENT_NOT_PROVEN")
    if s2["ece"] > s1["ece"]:
        hold_reasons.append("CALIBRATION_ERROR_WORSENED")
    gate_pass = not hold_reasons
    return {
        "status": "ADVISORY_RESEARCH_ONLY", "spec_version": SPEC_VERSION,
        "source": "NHL_PUBLIC_WEB_API", "goalie_status": "PROJECTED", "boxscores": len(boxes),
        "eligible_v1_rows": len(v1_rows), "covered_intersection_rows": len(common),
        "excluded_v1_rows_missing_box": len(v1_rows) - len(common),
        "common_rows": len(common), "test_n": len(y),
        "v1_dataset_hash": v1.dataset_hash, "v2_dataset_hash": v2.dataset_hash,
        "v1": s1, "v2": s2,
        "delta_brier_v1_minus_v2": float(np.mean(diff)),
        "delta_log_loss_v1_minus_v2": float(delta_ll),
        "delta_brier_bootstrap_95_ci": [float(ci[0]), float(ci[1])],
        "delta_log_loss_bootstrap_95_ci": [float(ll_ci[0]), float(ll_ci[1])],
        "research_gate_pass": gate_pass,
        "hold_reasons": hold_reasons,
        "decision": (
            "FORWARD_SHADOW_AND_GOVERNED_REVIEW_REQUIRED"
            if gate_pass else "HOLD_CHALLENGER_INSUFFICIENT_VALIDATION"
        ),
        "automatic_promotion_allowed": False,
        "probability_publishable": False, "can_execute": False,
    }


def run(years: Sequence[int], *, session: Any = requests) -> dict[str, Any]:
    games = fetch_historical_games(years, session=session)
    if len({g.game_id for g in games}) != len(games):
        raise NHLCandidateError("NHL_GAME_ID_DUPLICATE", "historical schedule")
    boxes: dict[str, Box] = {}
    for game in games:
        boxes[game.game_id] = fetch_box(game, session=session)
    return replay(games, boxes)
