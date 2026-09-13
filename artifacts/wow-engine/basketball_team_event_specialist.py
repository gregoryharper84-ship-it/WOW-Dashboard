"""WOW V17 NBA/WNBA team-event specialist foundation.

Independent league-specific moneyline win-probability model support.
No sportsbook price is used as a feature. NBA and WNBA never share fitted
coefficients or calibration artifacts. Promotion is fail-closed.

can_execute=False is unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from itertools import groupby
import hashlib
import json
import math
from typing import Iterable, Mapping, Sequence

import numpy as np

can_execute: bool = False

SUPPORTED_SPORTS = ("NBA", "WNBA")
FEATURE_SCHEMA_VERSION = "BASKETBALL_TEAM_EVENT_FEATURES_V2"
MODEL_FAMILY = "BASKETBALL_TEAM_EVENT_LOGISTIC_V1"
MIN_TRAIN_ROWS = 200
MIN_HOLDOUT_ROWS = 50
MIN_PRIOR_GAMES = 5

FEATURE_NAMES = (
    "home_win_rate_prior",
    "away_win_rate_prior",
    "home_point_diff_prior",
    "away_point_diff_prior",
    "home_rest_days_capped",
    "away_rest_days_capped",
    "home_back_to_back",
    "away_back_to_back",
)


class BasketballSpecialistError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingGame:
    game_id: str
    sport: str
    season: int
    game_date: date
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int

    @property
    def home_win(self) -> int:
        if self.home_score == self.away_score:
            raise BasketballSpecialistError("settled basketball game cannot be tied")
        return int(self.home_score > self.away_score)


@dataclass(frozen=True)
class FeatureRow:
    game_id: str
    sport: str
    game_date: date
    home_games_prior: int
    away_games_prior: int
    values: tuple[float, ...]
    outcome: int


@dataclass(frozen=True)
class LogisticArtifact:
    sport: str
    model_family: str
    model_artifact_version: str
    feature_schema_version: str
    feature_names: tuple[str, ...]
    intercept: float
    coefficients: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    training_row_count: int
    holdout_row_count: int
    train_start_date: str
    train_end_date: str
    holdout_brier: float
    holdout_log_loss: float
    holdout_accuracy: float
    artifact_checksum: str

    def raw_probability(self, values: Sequence[float]) -> float:
        x = np.asarray(values, dtype=float)
        means = np.asarray(self.means, dtype=float)
        scales = np.asarray(self.scales, dtype=float)
        beta = np.asarray(self.coefficients, dtype=float)
        if x.shape != beta.shape:
            raise BasketballSpecialistError("feature vector shape mismatch")
        z = (x - means) / scales
        return float(_sigmoid(self.intercept + float(z @ beta)))


@dataclass(frozen=True)
class CertificationDecision:
    sport: str
    capability_status: str
    reason_code: str
    training_rows: int
    holdout_rows: int
    calibration_rows: int
    notes: tuple[str, ...]


def _sport(value: str) -> str:
    out = str(value or "").upper().strip()
    if out not in SUPPORTED_SPORTS:
        raise BasketballSpecialistError(f"unsupported basketball sport: {out}")
    return out


def parse_bdl_game(raw: Mapping[str, object], sport: str) -> TrainingGame:
    """Normalize one completed BallDontLie team game into a settled label row."""
    s = _sport(sport)
    status = str(raw.get("status") or "").lower()
    home_score = raw.get("home_team_score")
    away_score = raw.get("visitor_team_score")
    if home_score is None or away_score is None:
        raise BasketballSpecialistError("completed game scores are required")
    if status and not any(token in status for token in ("final", "post", "complete")):
        raise BasketballSpecialistError(f"game is not final: {status}")
    home_team = raw.get("home_team") or {}
    away_team = raw.get("visitor_team") or raw.get("away_team") or {}
    if not isinstance(home_team, Mapping) or not isinstance(away_team, Mapping):
        raise BasketballSpecialistError("team identity payload missing")
    date_text = str(raw.get("date") or raw.get("datetime") or "")[:10]
    gid = str(raw.get("id") or "").strip()
    home_id = str(home_team.get("id") or "").strip()
    away_id = str(away_team.get("id") or "").strip()
    if not date_text or not gid or not home_id or not away_id:
        raise BasketballSpecialistError("game identity/date missing")
    return TrainingGame(
        game_id=gid,
        sport=s,
        season=int(raw.get("season") or int(date_text[:4])),
        game_date=date.fromisoformat(date_text),
        home_team_id=home_id,
        away_team_id=away_id,
        home_score=int(home_score),
        away_score=int(away_score),
    )


def build_pregame_features(games: Iterable[TrainingGame], sport: str) -> list[FeatureRow]:
    """Build features from history strictly earlier than the target calendar date.

    Games on the same date are evaluated as a batch. No final from an earlier game
    that day can enter another same-day game's pregame features.
    """
    s = _sport(sport)
    ordered = sorted((g for g in games if _sport(g.sport) == s), key=lambda g: (g.game_date, g.game_id))
    history: dict[str, list[tuple[date, int, int, bool]]] = {}
    rows: list[FeatureRow] = []
    for _, grouped in groupby(ordered, key=lambda g: g.game_date):
        day_games = list(grouped)
        pending_updates: list[tuple[str, tuple[date, int, int, bool]]] = []
        for g in day_games:
            hh = history.get(g.home_team_id, [])
            ah = history.get(g.away_team_id, [])
            if len(hh) >= MIN_PRIOR_GAMES and len(ah) >= MIN_PRIOR_GAMES:
                hwr = sum(x[3] for x in hh) / len(hh)
                awr = sum(x[3] for x in ah) / len(ah)
                hpd = sum(x[1] - x[2] for x in hh) / len(hh)
                apd = sum(x[1] - x[2] for x in ah) / len(ah)
                hrest = max(0, (g.game_date - hh[-1][0]).days - 1)
                arest = max(0, (g.game_date - ah[-1][0]).days - 1)
                values = (
                    float(hwr), float(awr), float(hpd), float(apd),
                    float(min(hrest, 7)), float(min(arest, 7)),
                    float(hrest == 0), float(arest == 0),
                )
                rows.append(FeatureRow(g.game_id, s, g.game_date, len(hh), len(ah), values, g.home_win))
            pending_updates.append((g.home_team_id, (g.game_date, g.home_score, g.away_score, bool(g.home_win))))
            pending_updates.append((g.away_team_id, (g.game_date, g.away_score, g.home_score, not bool(g.home_win))))
        for team_id, update in pending_updates:
            history.setdefault(team_id, []).append(update)
    return rows


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35.0, 35.0)))


def _fit_logistic(x: np.ndarray, y: np.ndarray, *, l2: float = 0.01, lr: float = 0.05, steps: int = 4000):
    n, p = x.shape
    beta = np.zeros(p, dtype=float)
    intercept = 0.0
    for _ in range(steps):
        pred = _sigmoid(intercept + x @ beta)
        err = pred - y
        intercept -= lr * float(np.mean(err))
        beta -= lr * ((x.T @ err) / n + l2 * beta)
    return intercept, beta


def _metrics(p: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    pp = np.clip(p, 1e-9, 1 - 1e-9)
    brier = float(np.mean((pp - y) ** 2))
    log_loss = float(-np.mean(y * np.log(pp) + (1 - y) * np.log(1 - pp)))
    accuracy = float(np.mean((pp >= 0.5) == y))
    return brier, log_loss, accuracy


def _date_safe_split(rows: Sequence[FeatureRow], desired_index: int) -> int:
    """Move a chronological split forward so a calendar date is never divided."""
    if desired_index <= 0 or desired_index >= len(rows):
        return desired_index
    boundary_date = rows[desired_index - 1].game_date
    index = desired_index
    while index < len(rows) and rows[index].game_date == boundary_date:
        index += 1
    return index


def train_specialist(rows: Sequence[FeatureRow], sport: str, *, model_artifact_version: str) -> LogisticArtifact:
    s = _sport(sport)
    league_rows = sorted((r for r in rows if _sport(r.sport) == s), key=lambda r: (r.game_date, r.game_id))
    if len(league_rows) < MIN_TRAIN_ROWS + MIN_HOLDOUT_ROWS:
        raise BasketballSpecialistError(
            f"{s} requires >= {MIN_TRAIN_ROWS + MIN_HOLDOUT_ROWS} usable chronological feature rows; got {len(league_rows)}"
        )
    desired = max(MIN_TRAIN_ROWS, int(len(league_rows) * 0.8))
    desired = min(desired, len(league_rows) - MIN_HOLDOUT_ROWS)
    split = _date_safe_split(league_rows, desired)
    if len(league_rows) - split < MIN_HOLDOUT_ROWS:
        split = _date_safe_split(league_rows, len(league_rows) - MIN_HOLDOUT_ROWS)
        if len(league_rows) - split < MIN_HOLDOUT_ROWS:
            raise BasketballSpecialistError("cannot create date-safe holdout with minimum rows")
    train = league_rows[:split]
    holdout = league_rows[split:]
    x_train = np.asarray([r.values for r in train], dtype=float)
    y_train = np.asarray([r.outcome for r in train], dtype=float)
    x_hold = np.asarray([r.values for r in holdout], dtype=float)
    y_hold = np.asarray([r.outcome for r in holdout], dtype=float)
    means = x_train.mean(axis=0)
    scales = x_train.std(axis=0)
    scales[scales < 1e-9] = 1.0
    z_train = (x_train - means) / scales
    z_hold = (x_hold - means) / scales
    intercept, beta = _fit_logistic(z_train, y_train)
    p_hold = _sigmoid(intercept + z_hold @ beta)
    brier, log_loss, accuracy = _metrics(p_hold, y_hold)
    payload = {
        "sport": s, "model_family": MODEL_FAMILY,
        "model_artifact_version": model_artifact_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": FEATURE_NAMES, "intercept": float(intercept),
        "coefficients": [float(v) for v in beta],
        "means": [float(v) for v in means], "scales": [float(v) for v in scales],
    }
    checksum = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return LogisticArtifact(
        sport=s, model_family=MODEL_FAMILY, model_artifact_version=model_artifact_version,
        feature_schema_version=FEATURE_SCHEMA_VERSION, feature_names=FEATURE_NAMES,
        intercept=float(intercept), coefficients=tuple(float(v) for v in beta),
        means=tuple(float(v) for v in means), scales=tuple(float(v) for v in scales),
        training_row_count=len(train), holdout_row_count=len(holdout),
        train_start_date=train[0].game_date.isoformat(), train_end_date=train[-1].game_date.isoformat(),
        holdout_brier=brier, holdout_log_loss=log_loss, holdout_accuracy=accuracy,
        artifact_checksum=checksum,
    )


def certification_decision(artifact: LogisticArtifact | None, *, sport: str, calibration_rows: int,
                           calibration_status: str | None, provenance_complete: bool) -> CertificationDecision:
    s = _sport(sport)
    if artifact is None:
        return CertificationDecision(s, "UNAVAILABLE", "MODEL_ARTIFACT_NOT_REGISTERED", 0, 0, calibration_rows, ())
    if artifact.sport != s:
        return CertificationDecision(s, "UNAVAILABLE", "MODEL_ARTIFACT_SPORT_MISMATCH", artifact.training_row_count, artifact.holdout_row_count, calibration_rows, ())
    if not provenance_complete:
        return CertificationDecision(s, "SHADOW", "MODEL_INPUTS_INSUFFICIENT", artifact.training_row_count, artifact.holdout_row_count, calibration_rows, ("training provenance incomplete",))
    if artifact.training_row_count < MIN_TRAIN_ROWS or artifact.holdout_row_count < MIN_HOLDOUT_ROWS:
        return CertificationDecision(s, "SHADOW", "MODEL_INPUTS_INSUFFICIENT", artifact.training_row_count, artifact.holdout_row_count, calibration_rows, ())
    if calibration_rows < 200 or calibration_status not in {"PLATT_TIME_SPLIT_V1", "ISOTONIC_V1"}:
        return CertificationDecision(s, "SHADOW", "MODEL_CALIBRATION_UNAVAILABLE", artifact.training_row_count, artifact.holdout_row_count, calibration_rows, ())
    if not (artifact.holdout_brier < 0.25 and artifact.holdout_log_loss < math.log(2)):
        return CertificationDecision(s, "SHADOW", "MODEL_VALIDATION_FAILED", artifact.training_row_count, artifact.holdout_row_count, calibration_rows, ())
    return CertificationDecision(s, "CERTIFIED", "OK", artifact.training_row_count, artifact.holdout_row_count, calibration_rows, ())


def artifact_payload(artifact: LogisticArtifact) -> dict:
    return asdict(artifact)
