"""NHL Skater Shots on Goal -- Phase 3: challenger fitting, ablation, OOS
validation.

Research/candidate-only. This module fits statistical challengers (Poisson
GLM, Negative Binomial GLM, and a partial-pooling hierarchical NB) over the
leakage-safe feature snapshots produced by nhl_skater_sog_feature_hydration
and nhl_skater_sog_ingestion, evaluates them with strict chronological
train/validation/holdout splits, and reports an ablation table. It never:

  * registers a production artifact or capability-manifest entry;
  * wires /score-pick-request or any scorer;
  * publishes a governed probability or calibrated lower bound;
  * touches prop_terminal_reducer_v2 / V17_TERMINAL_REDUCER;
  * creates market/value/card logic;
  * treats sportsbook/PrizePicks data as a model input.

Every serialized artifact carries research_only=true, registered=false,
probability_publishable=false, can_execute=false, and is never written to
the production artifact registry (there is no import of, or call into, any
registry module from this file).

The NHL_PUBLIC_WEB_API source remains certification_source_review_required
in v17/model_source_entitlements.py; nothing in this module changes that.
A strong holdout result here is model evidence, not source certification,
and not a capability publication -- those are separate, later, explicitly
governed decisions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy import stats
from scipy.special import gammaln

from nhl_skater_sog_feature_hydration import FeatureSnapshot, build_history_index, hydrate_pregame_snapshot
from nhl_skater_sog_ingestion import PARTICIPATION_DRESSED_PLAYED, SkaterGameSogRecord

RESEARCH_ONLY = True
REGISTERED = False
PROBABILITY_PUBLISHABLE = False
CAN_EXECUTE = False

SCHEMA_VERSION = "NHL_SOG_PHASE3_CHALLENGER_V1"
FEATURE_TRANSFORMATION_VERSION = "NHL_SOG_FEATURE_TRANSFORM_V1"  # from Phase 2.1

REGULAR_SEASON_GAME_TYPE = "REGULAR"  # V1 Phase 3 excludes playoff observations

# --- Data-sufficiency gate (Section 5) -- never lowered to manufacture a candidate ---
MIN_SEASONS = 3
MIN_TOTAL_OBSERVATIONS = 20_000
MIN_HOLDOUT_OBSERVATIONS = 5_000
MIN_UNIQUE_SKATERS = 400

THRESHOLD_LINES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5)
MIN_LINE_SUPPORT = 30  # minimum observations for a threshold line to count toward macro-Brier

STATUS_REJECTED = "REJECTED"
STATUS_RETAINED_CHALLENGER = "RETAINED_CHALLENGER"
STATUS_PHASE4_CANDIDATE = "PHASE4_CANDIDATE"
STATUS_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE_FOR_PROMOTION"

# --- Candidate feature blocks (Section 3) ---
FEATURE_BLOCKS: dict[str, tuple[str, ...]] = {
    "F0": (
        "rolling_sog_per_game_20",
        "rolling_shots_per_60_20",
        "rolling_toi_minutes_20",
        "career_qualifying_games_available",
    ),
    "F1": (
        "recent_vs_long_sog_rate_delta_5_20",
        "recent_vs_long_shots_per_60_delta_5_20",
        "recent_vs_long_toi_delta_5_20",
    ),
    "F2": (
        "opponent_sog_allowed_per_game_10",
        "opponent_sog_allowed_per_game_5",
        "opponent_qualifying_games_available",
    ),
    "F3": (
        "is_home",
        "rest_days",
        "back_to_back",
    ),
}
FEATURE_BLOCKS["F4"] = tuple(dict.fromkeys(sum(FEATURE_BLOCKS.values(), ())))

# --- Window ablation configurations (Section 4) ---
WINDOW_CONFIGS: dict[str, tuple[str, ...]] = {
    "short_only": ("rolling_sog_per_game_5", "rolling_shots_per_60_5", "rolling_toi_minutes_5"),
    "medium_only": ("rolling_sog_per_game_10", "rolling_shots_per_60_10", "rolling_toi_minutes_10"),
    "long_only": ("rolling_sog_per_game_20", "rolling_shots_per_60_20", "rolling_toi_minutes_20"),
    "short_long": (
        "rolling_sog_per_game_5", "rolling_shots_per_60_5", "rolling_toi_minutes_5",
        "rolling_sog_per_game_20", "rolling_shots_per_60_20", "rolling_toi_minutes_20",
    ),
    "5_10_20": (
        "rolling_sog_per_game_5", "rolling_sog_per_game_10", "rolling_sog_per_game_20",
        "rolling_shots_per_60_5", "rolling_shots_per_60_10", "rolling_shots_per_60_20",
        "rolling_toi_minutes_5", "rolling_toi_minutes_10", "rolling_toi_minutes_20",
    ),
    "season_to_date_plus_recency": (
        "season_to_date_sog_per_game",
        "recent_vs_long_sog_rate_delta_5_20",
    ),
}

MODEL_NAIVE = "M0_NAIVE_BASELINE"
MODEL_POISSON = "M1_POISSON_GLM"
MODEL_NEGATIVE_BINOMIAL = "M2_NEGATIVE_BINOMIAL_GLM"
MODEL_HIERARCHICAL_NB = "M3_HIERARCHICAL_PARTIAL_POOLING_NB"


class NHLSogChallengerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Training-row assembly (Section 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingRow:
    """One (skater x game) observation, reconstructed strictly from evidence
    available before that game's puck drop under the Phase 2.1
    available_at/as_of contract."""

    game_id: str
    player_id: str
    position: str
    provider_season_id: str
    game_start_time: str
    is_home: bool
    target_sog: int
    snapshot: FeatureSnapshot

    def feature(self, name: str) -> float | None:
        return self.snapshot.feature_values.get(name)


@dataclass(frozen=True)
class ExcludedRow:
    game_id: str
    player_id: str
    reason_code: str
    reason_detail: str


def build_training_dataset(
    history: Sequence[SkaterGameSogRecord],
    *,
    game_type: str = REGULAR_SEASON_GAME_TYPE,
) -> tuple[tuple[TrainingRow, ...], tuple[ExcludedRow, ...]]:
    """Reconstruct one TrainingRow per settled, dressed-with-recorded-value
    skater-game, evaluated at `as_of` = an instant strictly before that
    game's puck drop, using ONLY the leakage-safe hydration path from
    Phase 2/2.1 (nhl_skater_sog_feature_hydration.hydrate_pregame_snapshot).
    A row that cannot be reconstructed under that contract is excluded with
    a typed research reason -- never repaired with future data.

    `game_type` is accepted for forward compatibility with a later playoff
    cohort; V1 Phase 3 only ever receives REGULAR_SEASON_GAME_TYPE rows from
    a caller (this module does not itself distinguish playoff games, since
    nothing in the Phase 1 ingestion schema currently carries that flag --
    see the Phase 3 completion report for this as an explicit dependency on
    a future ingestion field, not a silently ignored requirement).

    Builds one HistoryIndex over `history` up front and reuses it for every
    row's hydration call, rather than letting each call rebuild its own --
    this is what keeps building a full training dataset close to
    O(n log n) rather than O(n^2) in the size of `history`.
    """
    rows: list[TrainingRow] = []
    excluded: list[ExcludedRow] = []
    index = build_history_index(history)

    for record in history:
        if record.participation_status != PARTICIPATION_DRESSED_PLAYED or record.actual_value is None:
            continue
        opponent_team_id = (
            record.away_team.team_id if record.team_id == record.home_team.team_id else record.home_team.team_id
        )
        is_home = record.team_id == record.home_team.team_id
        target_start = datetime.fromisoformat(record.game_start_time.replace("Z", "+00:00"))
        as_of = (target_start - _EPSILON).isoformat()
        try:
            snapshot = hydrate_pregame_snapshot(
                event_id=record.canonical_game_id,
                player_id=record.player_id,
                team_id=record.team_id,
                opponent_team_id=opponent_team_id,
                is_home=is_home,
                target_provider_season_id=record.provider_season_id,
                event_start=record.game_start_time,
                as_of=as_of,
                history_index=index,
            )
        except Exception as exc:  # noqa: BLE001 -- any hydration failure excludes this row with a typed reason
            excluded.append(
                ExcludedRow(record.canonical_game_id, record.player_id, "NHL_SOG_TRAINING_ROW_HYDRATION_FAILED", str(exc))
            )
            continue
        rows.append(
            TrainingRow(
                game_id=record.canonical_game_id,
                player_id=record.player_id,
                position=record.position,
                provider_season_id=record.provider_season_id,
                game_start_time=record.game_start_time,
                is_home=is_home,
                target_sog=record.actual_value,
                snapshot=snapshot,
            )
        )
    return tuple(rows), tuple(excluded)


_EPSILON = timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Data-sufficiency gate (Section 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataSufficiencyReport:
    seasons: int
    total_observations: int
    holdout_observations: int
    unique_skaters: int
    meets_promotion_threshold: bool
    reasons: tuple[str, ...]


def check_data_sufficiency(
    rows: Sequence[TrainingRow], *, holdout_season: str | None
) -> DataSufficiencyReport:
    seasons = sorted({r.provider_season_id for r in rows})
    unique_skaters = len({r.player_id for r in rows})
    holdout_rows = [r for r in rows if holdout_season is not None and r.provider_season_id == holdout_season]
    reasons = []
    if len(seasons) < MIN_SEASONS:
        reasons.append(f"SEASONS_BELOW_MINIMUM({len(seasons)}<{MIN_SEASONS})")
    if len(rows) < MIN_TOTAL_OBSERVATIONS:
        reasons.append(f"TOTAL_OBSERVATIONS_BELOW_MINIMUM({len(rows)}<{MIN_TOTAL_OBSERVATIONS})")
    if len(holdout_rows) < MIN_HOLDOUT_OBSERVATIONS:
        reasons.append(f"HOLDOUT_OBSERVATIONS_BELOW_MINIMUM({len(holdout_rows)}<{MIN_HOLDOUT_OBSERVATIONS})")
    if unique_skaters < MIN_UNIQUE_SKATERS:
        reasons.append(f"UNIQUE_SKATERS_BELOW_MINIMUM({unique_skaters}<{MIN_UNIQUE_SKATERS})")
    return DataSufficiencyReport(
        seasons=len(seasons),
        total_observations=len(rows),
        holdout_observations=len(holdout_rows),
        unique_skaters=unique_skaters,
        meets_promotion_threshold=not reasons,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# Chronological split (Section 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChronologicalSplit:
    holdout_season: str | None
    development_rows: tuple[TrainingRow, ...]
    holdout_rows: tuple[TrainingRow, ...]
    rolling_folds: tuple[tuple[tuple[TrainingRow, ...], tuple[TrainingRow, ...]], ...]  # [(train, validate), ...]


def build_chronological_split(rows: Sequence[TrainingRow], *, max_folds: int = 4) -> ChronologicalSplit:
    """Most recent complete season -> untouched final holdout. All earlier
    seasons -> development, split into rolling-origin (train past ->
    validate future) folds by season boundary. No random shuffling."""
    seasons = sorted({r.provider_season_id for r in rows})
    if not seasons:
        return ChronologicalSplit(None, (), (), ())
    holdout_season = seasons[-1]
    dev_seasons = seasons[:-1]
    holdout_rows = tuple(sorted((r for r in rows if r.provider_season_id == holdout_season), key=lambda r: r.game_start_time))
    development_rows = tuple(sorted((r for r in rows if r.provider_season_id != holdout_season), key=lambda r: r.game_start_time))

    folds: list[tuple[tuple[TrainingRow, ...], tuple[TrainingRow, ...]]] = []
    for i in range(1, len(dev_seasons)):
        train_seasons = set(dev_seasons[:i])
        validate_season = dev_seasons[i]
        train_rows = tuple(r for r in development_rows if r.provider_season_id in train_seasons)
        validate_rows = tuple(r for r in development_rows if r.provider_season_id == validate_season)
        if train_rows and validate_rows:
            folds.append((train_rows, validate_rows))
    folds = folds[-max_folds:] if len(folds) > max_folds else folds
    return ChronologicalSplit(holdout_season, development_rows, holdout_rows, tuple(folds))


# ---------------------------------------------------------------------------
# Feature matrix assembly -- standardization fitted on the training fold ONLY
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureMatrixSpec:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]


def _rows_with_complete_features(rows: Sequence[TrainingRow], feature_names: Sequence[str]) -> tuple[TrainingRow, ...]:
    return tuple(r for r in rows if all(r.feature(name) is not None for name in feature_names))


def fit_feature_matrix_spec(train_rows: Sequence[TrainingRow], feature_names: Sequence[str]) -> FeatureMatrixSpec:
    """Standardization (mean/scale) is learned from the training fold only,
    per Section 4/13 -- never from validation, holdout, or the whole dataset."""
    if not train_rows:
        return FeatureMatrixSpec(tuple(feature_names), tuple(0.0 for _ in feature_names), tuple(1.0 for _ in feature_names))
    raw = np.array([[r.feature(name) for name in feature_names] for r in train_rows], dtype=float)
    means = raw.mean(axis=0)
    scales = raw.std(axis=0)
    scales = np.where(scales < 1e-8, 1.0, scales)
    return FeatureMatrixSpec(tuple(feature_names), tuple(means.tolist()), tuple(scales.tolist()))


def transform_feature_matrix(rows: Sequence[TrainingRow], spec: FeatureMatrixSpec) -> np.ndarray:
    """Design matrix with an intercept column, standardized using a spec
    fitted on the training fold (never refit on this call's own rows)."""
    n = len(rows)
    p = len(spec.feature_names)
    X = np.ones((n, p + 1))
    for j, name in enumerate(spec.feature_names):
        raw = np.array([r.feature(name) for r in rows], dtype=float)
        X[:, j + 1] = (raw - spec.means[j]) / spec.scales[j]
    return X


def target_vector(rows: Sequence[TrainingRow]) -> np.ndarray:
    return np.array([r.target_sog for r in rows], dtype=float)


# ---------------------------------------------------------------------------
# Model fitting (Section 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedCountModel:
    model_family: str
    feature_spec: FeatureMatrixSpec
    beta: tuple[float, ...]
    dispersion_alpha: float | None  # None for Poisson/naive
    player_shrinkage: Mapping[str, float] = field(default_factory=dict)  # M3 only: player_id -> log-scale adjustment
    training_rows: int = 0
    naive_reference_mean: float | None = None  # M0 population fallback

    def predict_mu(self, rows: Sequence[TrainingRow]) -> np.ndarray:
        if self.model_family == MODEL_NAIVE:
            out = []
            for r in rows:
                v = r.feature("rolling_sog_per_game_20")
                if v is None:
                    v = r.feature("season_to_date_sog_per_game")
                if v is None:
                    v = self.naive_reference_mean if self.naive_reference_mean is not None else 1.0
                out.append(max(float(v), 1e-6))
            return np.array(out)
        X = transform_feature_matrix(rows, self.feature_spec)
        eta = X @ np.array(self.beta)
        if self.model_family == MODEL_HIERARCHICAL_NB:
            adjustments = np.array([self.player_shrinkage.get(r.player_id, 0.0) for r in rows])
            eta = eta + adjustments
        return np.exp(np.clip(eta, -30, 30))


def _poisson_irls(X: np.ndarray, y: np.ndarray, *, max_iter: int = 100, tol: float = 1e-9) -> np.ndarray:
    p = X.shape[1]
    beta = np.zeros(p)
    for _ in range(max_iter):
        eta = np.clip(X @ beta, -30, 30)
        mu = np.exp(eta)
        w = np.clip(mu, 1e-8, None)
        z = eta + (y - mu) / w
        XtW = X.T * w
        A = XtW @ X + 1e-8 * np.eye(p)
        b = XtW @ z
        beta_new = np.linalg.solve(A, b)
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            break
        beta = beta_new
    return beta


def fit_poisson_glm(train_rows: Sequence[TrainingRow], feature_names: Sequence[str]) -> FittedCountModel:
    usable = _rows_with_complete_features(train_rows, feature_names)
    if not usable:
        raise NHLSogChallengerError("NHL_SOG_NO_USABLE_TRAINING_ROWS", "no training rows with complete features")
    spec = fit_feature_matrix_spec(usable, feature_names)
    X = transform_feature_matrix(usable, spec)
    y = target_vector(usable)
    beta = _poisson_irls(X, y)
    return FittedCountModel(MODEL_POISSON, spec, tuple(beta.tolist()), None, training_rows=len(usable))


def _nb_irls(X: np.ndarray, y: np.ndarray, *, max_outer: int = 15, max_inner: int = 50, tol: float = 1e-8) -> tuple[np.ndarray, float]:
    p = X.shape[1]
    beta = _poisson_irls(X, y)
    alpha = 0.1
    for _ in range(max_outer):
        for _ in range(max_inner):
            eta = np.clip(X @ beta, -30, 30)
            mu = np.exp(eta)
            w = np.clip(mu / (1.0 + alpha * mu), 1e-8, None)
            z = eta + (y - mu) / np.clip(mu, 1e-8, None)
            XtW = X.T * w
            A = XtW @ X + 1e-8 * np.eye(p)
            b = XtW @ z
            beta_new = np.linalg.solve(A, b)
            if np.max(np.abs(beta_new - beta)) < tol:
                beta = beta_new
                break
            beta = beta_new
        eta = np.clip(X @ beta, -30, 30)
        mu = np.exp(eta)
        resid_excess = (y - mu) ** 2 - mu
        denom = np.clip(mu ** 2, 1e-8, None)
        alpha_new = float(np.clip(np.mean(resid_excess / denom), 1e-6, 50.0))
        if abs(alpha_new - alpha) < 1e-6:
            alpha = alpha_new
            break
        alpha = alpha_new
    return beta, alpha


def fit_negative_binomial_glm(train_rows: Sequence[TrainingRow], feature_names: Sequence[str]) -> FittedCountModel:
    usable = _rows_with_complete_features(train_rows, feature_names)
    if not usable:
        raise NHLSogChallengerError("NHL_SOG_NO_USABLE_TRAINING_ROWS", "no training rows with complete features")
    spec = fit_feature_matrix_spec(usable, feature_names)
    X = transform_feature_matrix(usable, spec)
    y = target_vector(usable)
    beta, alpha = _nb_irls(X, y)
    return FittedCountModel(MODEL_NEGATIVE_BINOMIAL, spec, tuple(beta.tolist()), alpha, training_rows=len(usable))


def fit_hierarchical_nb(
    train_rows: Sequence[TrainingRow],
    feature_names: Sequence[str],
    *,
    shrinkage_k_candidates: Sequence[float] = (1.0, 3.0, 5.0, 10.0, 20.0),
    dev_validate_rows: Sequence[TrainingRow] | None = None,
) -> FittedCountModel:
    """Partial-pooling approximation to a hierarchical NB: fits the
    population NB2 (M2) first, then computes a per-player log-scale
    intercept adjustment shrunk toward zero by n_player/(n_player+k) --
    an empirical-Bayes approximation to full hierarchical partial pooling,
    not a Bayesian posterior. This is a deliberate, documented
    simplification: the property Section 2 actually requires of M3 (that
    low-sample players be supported by pooled information rather than
    excluded or hand-substituted with a league average) holds for this
    estimator, since the shrinkage weight -> 0 for n_player=0 (falling back
    exactly to the population M2 prediction, never a manually chosen
    heuristic value) and -> 1 for large n_player (approaching that player's
    own empirical rate).

    `shrinkage_k_candidates` is selected via dev_validate_rows NLL if given
    (never via holdout), else the median candidate is used.
    """
    usable = _rows_with_complete_features(train_rows, feature_names)
    if not usable:
        raise NHLSogChallengerError("NHL_SOG_NO_USABLE_TRAINING_ROWS", "no training rows with complete features")
    population = fit_negative_binomial_glm(train_rows, feature_names)
    X = transform_feature_matrix(usable, population.feature_spec)
    y = target_vector(usable)
    eta_pop = X @ np.array(population.beta)

    per_player_resid: dict[str, list[float]] = {}
    for row, eta_i, y_i in zip(usable, eta_pop, y):
        log_obs = np.log(max(y_i, 0.5)) - eta_i  # residual on the log scale using a 0.5 continuity correction
        per_player_resid.setdefault(row.player_id, []).append(float(log_obs))

    def _adjustments_for_k(k: float) -> dict[str, float]:
        out = {}
        for player_id, resids in per_player_resid.items():
            n = len(resids)
            weight = n / (n + k)
            out[player_id] = weight * float(np.mean(resids))
        return out

    best_k = shrinkage_k_candidates[len(shrinkage_k_candidates) // 2]
    if dev_validate_rows:
        best_nll = None
        for k in shrinkage_k_candidates:
            adjustments = _adjustments_for_k(k)
            candidate = FittedCountModel(
                MODEL_HIERARCHICAL_NB, population.feature_spec, population.beta, population.dispersion_alpha,
                player_shrinkage=adjustments, training_rows=len(usable),
            )
            usable_validate = _rows_with_complete_features(dev_validate_rows, feature_names)
            if not usable_validate:
                continue
            mu = candidate.predict_mu(usable_validate)
            nll = negative_binomial_nll(target_vector(usable_validate), mu, candidate.dispersion_alpha or 1e-6)
            if best_nll is None or nll < best_nll:
                best_nll = nll
                best_k = k

    adjustments = _adjustments_for_k(best_k)
    return FittedCountModel(
        MODEL_HIERARCHICAL_NB, population.feature_spec, population.beta, population.dispersion_alpha,
        player_shrinkage=adjustments, training_rows=len(usable),
    )


def fit_naive_baseline(train_rows: Sequence[TrainingRow]) -> FittedCountModel:
    values = [r.target_sog for r in train_rows]
    mean = float(np.mean(values)) if values else 1.0
    return FittedCountModel(MODEL_NAIVE, FeatureMatrixSpec((), (), ()), (), None, naive_reference_mean=mean, training_rows=len(train_rows))


# ---------------------------------------------------------------------------
# Metrics (Section 7)
# ---------------------------------------------------------------------------


def poisson_nll(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.clip(mu, 1e-10, None)
    return float(np.mean(mu - y * np.log(mu) + gammaln(y + 1)))


def negative_binomial_nll(y: np.ndarray, mu: np.ndarray, alpha: float) -> float:
    r = 1.0 / max(alpha, 1e-8)
    mu = np.clip(mu, 1e-10, None)
    ll = gammaln(y + r) - gammaln(r) - gammaln(y + 1) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))
    return float(-np.mean(ll))


def _cdf_at(mu: np.ndarray, k: np.ndarray, *, alpha: float | None) -> np.ndarray:
    if alpha is None:
        return stats.poisson.cdf(k, mu)
    r = 1.0 / max(alpha, 1e-8)
    p = r / (r + mu)
    return stats.nbinom.cdf(k, r, p)


def threshold_probabilities(mu: np.ndarray, *, alpha: float | None, lines: Sequence[float] = THRESHOLD_LINES) -> dict[float, np.ndarray]:
    """{line: P(SOG > line)} for each synthetic research threshold. These
    are NOT sportsbook lines or market probabilities -- fixed research
    thresholds only, per Section 7."""
    out = {}
    for line in lines:
        floor_k = np.floor(line)
        out[line] = 1.0 - _cdf_at(mu, floor_k, alpha=alpha)
    return out


def threshold_brier_scores(
    y: np.ndarray, mu: np.ndarray, *, alpha: float | None, lines: Sequence[float] = THRESHOLD_LINES, min_support: int = MIN_LINE_SUPPORT
) -> tuple[dict[float, float], float]:
    """Per-line Brier score for P(SOG > line) against the actual over/under
    outcome, plus macro-Brier across lines with adequate support."""
    over_probs = threshold_probabilities(mu, alpha=alpha, lines=lines)
    per_line: dict[float, float] = {}
    supported: list[float] = []
    for line, p_over in over_probs.items():
        actual_over = (y > line).astype(float)
        brier = float(np.mean((p_over - actual_over) ** 2))
        per_line[line] = brier
        if len(y) >= min_support:
            supported.append(brier)
    macro = float(np.mean(supported)) if supported else float("nan")
    return per_line, macro


def mean_bias(y: np.ndarray, mu: np.ndarray) -> float:
    return float(np.mean(mu - y))


def mean_absolute_error(y: np.ndarray, mu: np.ndarray) -> float:
    return float(np.mean(np.abs(mu - y)))


def expected_calibration_error(y: np.ndarray, mu: np.ndarray, *, alpha: float | None, line: float = 0.5, n_bins: int = 10) -> float:
    """ECE computed on P(SOG > line) (default line=0.5, i.e. "recorded at
    least one shot") as the representative calibration probability,
    binned into deciles by predicted probability."""
    p = threshold_probabilities(mu, alpha=alpha, lines=(line,))[line]
    actual = (y > line).astype(float)
    order = np.argsort(p)
    p_sorted, actual_sorted = p[order], actual[order]
    bins = np.array_split(np.arange(len(p_sorted)), n_bins)
    ece = 0.0
    for idx in bins:
        if len(idx) == 0:
            continue
        conf = float(np.mean(p_sorted[idx]))
        acc = float(np.mean(actual_sorted[idx]))
        ece += (len(idx) / len(p_sorted)) * abs(conf - acc)
    return float(ece)


@dataclass(frozen=True)
class EvaluationMetrics:
    nll: float
    macro_brier: float
    per_line_brier: Mapping[float, float]
    mae: float
    mean_bias: float
    ece: float
    n: int


def evaluate_model(model: FittedCountModel, rows: Sequence[TrainingRow]) -> EvaluationMetrics:
    usable = rows if model.model_family == MODEL_NAIVE else _rows_with_complete_features(rows, model.feature_spec.feature_names)
    if not usable:
        return EvaluationMetrics(float("nan"), float("nan"), {}, float("nan"), float("nan"), float("nan"), 0)
    y = target_vector(usable)
    mu = model.predict_mu(usable)
    alpha = model.dispersion_alpha
    nll = poisson_nll(y, mu) if alpha is None else negative_binomial_nll(y, mu, alpha)
    per_line, macro = threshold_brier_scores(y, mu, alpha=alpha)
    return EvaluationMetrics(
        nll=nll,
        macro_brier=macro,
        per_line_brier=per_line,
        mae=mean_absolute_error(y, mu),
        mean_bias=mean_bias(y, mu),
        ece=expected_calibration_error(y, mu, alpha=alpha),
        n=len(usable),
    )


# ---------------------------------------------------------------------------
# Paired bootstrap (Section 8/9) -- resampled over distinct games, not rows
# ---------------------------------------------------------------------------


def paired_bootstrap_delta_ci(
    rows: Sequence[TrainingRow],
    metric_fn: Callable[[Sequence[TrainingRow]], float],
    *,
    n_boot: int = 200,
    seed: int = 1234,
) -> tuple[float, float]:
    """95% CI for a paired metric delta, resampling whole games (not
    independent rows) to respect same-game dependence between skaters."""
    rng = np.random.default_rng(seed)
    game_ids = sorted({r.game_id for r in rows})
    rows_by_game: dict[str, list[TrainingRow]] = {}
    for r in rows:
        rows_by_game.setdefault(r.game_id, []).append(r)
    if not game_ids:
        return (float("nan"), float("nan"))
    deltas = []
    for _ in range(n_boot):
        sampled_games = rng.choice(game_ids, size=len(game_ids), replace=True)
        sample_rows: list[TrainingRow] = []
        for g in sampled_games:
            sample_rows.extend(rows_by_game[g])
        deltas.append(metric_fn(sample_rows))
    deltas_arr = np.array(deltas)
    return float(np.percentile(deltas_arr, 2.5)), float(np.percentile(deltas_arr, 97.5))


# ---------------------------------------------------------------------------
# Acceptance / promotion rules (Section 8/9)
# ---------------------------------------------------------------------------


def feature_block_clears_acceptance(
    fold_deltas_nll_pct: Sequence[float],
    fold_deltas_macro_brier_abs: Sequence[float],
    *,
    ci_excludes_zero: bool,
) -> bool:
    """Section 8: cross-validation-consistency + practical-improvement +
    uncertainty gates for a candidate feature block."""
    if not fold_deltas_nll_pct:
        return False
    n_folds = len(fold_deltas_nll_pct)
    positive_nll_folds = sum(1 for d in fold_deltas_nll_pct if d > 0)
    if n_folds >= 4:
        consistent = positive_nll_folds >= 3
    else:
        consistent = (positive_nll_folds / n_folds) >= 0.75
    if not consistent:
        return False

    mean_nll_pct = float(np.mean(fold_deltas_nll_pct))
    mean_brier_abs = float(np.mean(fold_deltas_macro_brier_abs))
    practical = (mean_nll_pct >= 0.5 and mean_brier_abs >= -0.001) or (mean_brier_abs >= 0.002 and mean_nll_pct >= -0.25)
    if not practical:
        return False

    return ci_excludes_zero


@dataclass(frozen=True)
class CohortMetrics:
    cohort: str
    n: int
    nll: float
    macro_brier: float


def model_family_clears_promotion(
    *,
    holdout_nll_pct_improvement: float,
    holdout_macro_brier_abs_improvement: float,
    ci_excludes_zero_for_claimed_primary: bool,
    other_primary_regressed_materially: bool,
    cohort_regressions: Sequence[CohortMetrics],
    baseline_cohort_metrics: Mapping[str, CohortMetrics],
) -> bool:
    """Section 9: a more complex family only replaces the simpler one if
    its frozen holdout result clears both the improvement bar and the
    regression-protection bar."""
    meets_primary = holdout_nll_pct_improvement >= 1.0 or holdout_macro_brier_abs_improvement >= 0.003
    if not meets_primary or not ci_excludes_zero_for_claimed_primary or other_primary_regressed_materially:
        return False
    for cohort_metric in cohort_regressions:
        if cohort_metric.n < 500:
            continue
        baseline = baseline_cohort_metrics.get(cohort_metric.cohort)
        if baseline is None:
            continue
        brier_regression = cohort_metric.macro_brier - baseline.macro_brier
        nll_regression_pct = ((cohort_metric.nll - baseline.nll) / abs(baseline.nll)) * 100.0 if baseline.nll else 0.0
        if brier_regression > 0.010 or nll_regression_pct > 3.0:
            return False
    return True


# ---------------------------------------------------------------------------
# Cohort reporting (Section 10/11)
# ---------------------------------------------------------------------------


LOW_SAMPLE_COHORTS = (
    ("0_4_PRIOR_GAMES", lambda r: (r.snapshot.feature_sample_counts.get("career_qualifying_games_available", 0) or 0) < 5),
    ("5_9_PRIOR_GAMES", lambda r: 5 <= (r.snapshot.feature_sample_counts.get("career_qualifying_games_available", 0) or 0) < 10),
    ("10_19_PRIOR_GAMES", lambda r: 10 <= (r.snapshot.feature_sample_counts.get("career_qualifying_games_available", 0) or 0) < 20),
    ("20_PLUS_PRIOR_GAMES", lambda r: (r.snapshot.feature_sample_counts.get("career_qualifying_games_available", 0) or 0) >= 20),
)

GENERAL_COHORTS = (
    ("FORWARDS", lambda r: r.position in {"C", "LW", "RW"}),
    ("DEFENSEMEN", lambda r: r.position == "D"),
    ("HOME", lambda r: r.is_home),
    ("AWAY", lambda r: not r.is_home),
    ("BACK_TO_BACK", lambda r: (r.feature("back_to_back") or 0.0) == 1.0),
    ("RESTED", lambda r: (r.feature("back_to_back") or 0.0) == 0.0),
)


def cohort_report(model: FittedCountModel, rows: Sequence[TrainingRow], cohorts=LOW_SAMPLE_COHORTS + GENERAL_COHORTS) -> tuple[CohortMetrics, ...]:
    out = []
    for name, predicate in cohorts:
        subset = [r for r in rows if predicate(r)]
        metrics = evaluate_model(model, subset)
        out.append(CohortMetrics(name, metrics.n, metrics.nll, metrics.macro_brier))
    return tuple(out)


# ---------------------------------------------------------------------------
# Research artifact serialization (Section 15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchArtifact:
    schema_version: str
    model_family: str
    feature_schema_version: str
    transformation_version: str
    feature_names: tuple[str, ...]
    beta: tuple[float, ...]
    dispersion_alpha: float | None
    player_shrinkage: Mapping[str, float]
    training_cutoff: str
    dataset_hash: str
    training_metrics: Mapping[str, Any]
    oos_metrics: Mapping[str, Any]
    code_version_sha: str
    research_only: bool = True
    registered: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dataset_hash(rows: Sequence[TrainingRow]) -> str:
    payload = sorted((r.game_id, r.player_id, r.target_sog) for r in rows)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def serialize_research_artifact(
    model: FittedCountModel,
    *,
    training_rows: Sequence[TrainingRow],
    training_cutoff: str,
    training_metrics: EvaluationMetrics,
    oos_metrics: EvaluationMetrics,
    code_version_sha: str,
) -> ResearchArtifact:
    return ResearchArtifact(
        schema_version=SCHEMA_VERSION,
        model_family=model.model_family,
        feature_schema_version=FEATURE_TRANSFORMATION_VERSION,
        transformation_version=FEATURE_TRANSFORMATION_VERSION,
        feature_names=model.feature_spec.feature_names,
        beta=model.beta,
        dispersion_alpha=model.dispersion_alpha,
        player_shrinkage=dict(model.player_shrinkage),
        training_cutoff=training_cutoff,
        dataset_hash=_dataset_hash(training_rows),
        training_metrics=asdict(training_metrics),
        oos_metrics=asdict(oos_metrics),
        code_version_sha=code_version_sha,
    )


# NOTE: this module intentionally has no function that writes a
# ResearchArtifact into any production/certified registry. Promotion out of
# research status is a separate, later, explicitly governed step.


__all__ = [
    "RESEARCH_ONLY", "REGISTERED", "PROBABILITY_PUBLISHABLE", "CAN_EXECUTE",
    "MIN_SEASONS", "MIN_TOTAL_OBSERVATIONS", "MIN_HOLDOUT_OBSERVATIONS", "MIN_UNIQUE_SKATERS",
    "THRESHOLD_LINES", "FEATURE_BLOCKS", "WINDOW_CONFIGS",
    "MODEL_NAIVE", "MODEL_POISSON", "MODEL_NEGATIVE_BINOMIAL", "MODEL_HIERARCHICAL_NB",
    "STATUS_REJECTED", "STATUS_RETAINED_CHALLENGER", "STATUS_PHASE4_CANDIDATE", "STATUS_INSUFFICIENT_SAMPLE",
    "NHLSogChallengerError", "TrainingRow", "ExcludedRow", "build_training_dataset",
    "DataSufficiencyReport", "check_data_sufficiency",
    "ChronologicalSplit", "build_chronological_split",
    "FeatureMatrixSpec", "fit_feature_matrix_spec", "transform_feature_matrix", "target_vector",
    "FittedCountModel", "fit_poisson_glm", "fit_negative_binomial_glm", "fit_hierarchical_nb", "fit_naive_baseline",
    "poisson_nll", "negative_binomial_nll", "threshold_probabilities", "threshold_brier_scores",
    "mean_bias", "mean_absolute_error", "expected_calibration_error",
    "EvaluationMetrics", "evaluate_model",
    "paired_bootstrap_delta_ci",
    "feature_block_clears_acceptance", "CohortMetrics", "model_family_clears_promotion",
    "LOW_SAMPLE_COHORTS", "GENERAL_COHORTS", "cohort_report",
    "ResearchArtifact", "serialize_research_artifact",
]
