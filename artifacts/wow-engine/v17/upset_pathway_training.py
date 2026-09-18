"""Walk-forward training for sport-specific V17 upset-pathway challengers.

Training rows must be chronological, pregame, and market-free.  The fitted
model estimates P(regime | x), P(upset | regime, x), and favorite fragility
independently.  Promotion remains the existing champion/challenger concern.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from v17.upset_pathway_model import SCHEMA_VERSION, validate_and_aggregate_upset_pathways

CAN_EXECUTE = False
FORBIDDEN_FEATURE_TOKENS = (
    "ODDS", "PRICE", "MARKET", "MONEYLINE", "SPREAD", "PUBLIC", "SHARP",
    "REVENGE", "STREAK", "PAYOUT", "IMPLIED",
)

# These are distinct model-input contracts, not universal weights. Values are
# learned within each sport; interaction_only=True fits leverage interactions.
SPORT_FEATURE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "MLB": (
        "adjusted_strength_gap", "starter_quality_gap", "starter_volatility_gap",
        "bullpen_availability_gap", "bullpen_quality_gap", "platoon_matchup_gap",
        "lineup_quality_gap", "defense_gap", "power_event_rate_gap",
        "run_environment", "effective_innings", "close_game_finish_gap",
    ),
    "NFL": (
        "adjusted_strength_gap", "qb_efficiency_gap", "qb_volatility_gap",
        "pass_rush_vs_protection", "early_down_efficiency_gap", "explosive_play_gap",
        "turnover_creation_gap", "special_teams_gap", "weather_variance",
        "expected_drives", "fourth_down_aggression_gap", "close_game_finish_gap",
    ),
    "WNBA": (
        "adjusted_strength_gap", "rotation_availability_gap", "three_rate_gap",
        "three_variance_gap", "turnover_pressure_gap", "offensive_rebound_gap",
        "rim_pressure_gap", "foul_pressure_gap", "transition_gap",
        "expected_possessions", "star_dependency_gap", "close_game_finish_gap",
    ),
    "NBA": (
        "adjusted_strength_gap", "rotation_availability_gap", "three_rate_gap",
        "three_variance_gap", "turnover_pressure_gap", "offensive_rebound_gap",
        "rim_pressure_gap", "foul_pressure_gap", "transition_gap",
        "expected_possessions", "star_dependency_gap", "close_game_finish_gap",
    ),
}

SPORT_REGIME_MECHANISMS: dict[str, dict[str, tuple[str, ...]]] = {
    "MLB": {
        "NORMAL": ("DEFENSE",), "STARTER_INTERACTION": ("STARTER", "PLATOON"),
        "BULLPEN_EXPOSURE": ("BULLPEN",), "POWER_VARIANCE": ("POWER", "RUN_ENVIRONMENT"),
    },
    "NFL": {
        "NORMAL": ("QB",), "PRESSURE_COLLAPSE": ("PASS_RUSH", "OFFENSIVE_LINE"),
        "EXPLOSIVE_TURNOVER": ("EXPLOSIVE_PLAY", "TURNOVER"),
        "LOW_REPETITION": ("WEATHER", "SPECIAL_TEAMS", "FOURTH_DOWN"),
    },
    "WNBA": {
        "NORMAL": ("ROTATION",), "SHOOTING_TAIL": ("SHOOTING",),
        "POSSESSION_THEFT": ("TURNOVER", "OFFENSIVE_REBOUND"),
        "RIM_FOUL_TRANSITION": ("RIM", "FOUL", "TRANSITION"),
    },
    "NBA": {
        "NORMAL": ("ROTATION",), "SHOOTING_TAIL": ("SHOOTING",),
        "POSSESSION_THEFT": ("TURNOVER", "OFFENSIVE_REBOUND"),
        "RIM_FOUL_TRANSITION": ("RIM", "FOUL", "TRANSITION"),
    },
}


class UpsetTrainingInvalid(ValueError):
    pass


def _model() -> Pipeline:
    return Pipeline((
        ("impute", SimpleImputer(strategy="median")),
        ("interactions", PolynomialFeatures(degree=2, interaction_only=True, include_bias=False)),
        ("scale", StandardScaler()),
        ("logit", LogisticRegression(C=1.0, max_iter=4000)),
    ))


def _audit_features(sport: str, names: Sequence[str]) -> tuple[str, ...]:
    expected = SPORT_FEATURE_CONTRACTS.get(sport)
    actual = tuple(str(v).strip() for v in names)
    if expected is None or actual != expected:
        raise UpsetTrainingInvalid("SPORT_FEATURE_CONTRACT_MISMATCH")
    leaked = [name for name in actual if any(token in name.upper() for token in FORBIDDEN_FEATURE_TOKENS)]
    if leaked:
        raise UpsetTrainingInvalid("MARKET_OR_NARRATIVE_FEATURE_LEAKAGE:" + ",".join(leaked))
    return actual


def _matrix(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> np.ndarray:
    if not rows:
        raise UpsetTrainingInvalid("TRAINING_ROWS_REQUIRED")
    try:
        return np.asarray([[float(row[name]) for name in names] for row in rows], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise UpsetTrainingInvalid("NUMERIC_FEATURE_ROW_INVALID") from exc


@dataclass(frozen=True)
class UpsetPathwayArtifact:
    sport: str
    artifact_id: str
    feature_names: tuple[str, ...]
    feature_schema_hash: str
    regime_model: Pipeline
    conditional_models: Mapping[str, Pipeline]
    fragility_model: Pipeline
    regime_mechanisms: Mapping[str, tuple[str, ...]]


def fit_upset_pathway_challenger(
    *, sport: str, rows: Sequence[Mapping[str, Any]], regime_labels: Sequence[str],
    upset_labels: Sequence[int], favorite_fragility_labels: Sequence[int],
    artifact_id: str,
) -> UpsetPathwayArtifact:
    sport = str(sport).upper()
    names = _audit_features(sport, SPORT_FEATURE_CONTRACTS.get(sport, ()))
    n = len(rows)
    if not (n == len(regime_labels) == len(upset_labels) == len(favorite_fragility_labels)):
        raise UpsetTrainingInvalid("ROW_LABEL_LENGTH_MISMATCH")
    allowed_regimes = SPORT_REGIME_MECHANISMS[sport]
    regimes = np.asarray([str(v).upper() for v in regime_labels])
    if set(regimes) != set(allowed_regimes):
        raise UpsetTrainingInvalid("REGIME_LABEL_COVERAGE_INCOMPLETE")
    y = np.asarray(upset_labels, dtype=int)
    fragility = np.asarray(favorite_fragility_labels, dtype=int)
    if set(y) != {0, 1} or set(fragility) != {0, 1}:
        raise UpsetTrainingInvalid("BINARY_TARGET_CLASS_COVERAGE_INCOMPLETE")
    x = _matrix(rows, names)
    regime_model = _model().fit(x, regimes)
    conditional: dict[str, Pipeline] = {}
    for regime in sorted(allowed_regimes):
        mask = regimes == regime
        if set(y[mask]) != {0, 1}:
            raise UpsetTrainingInvalid(f"CONDITIONAL_TARGET_CLASS_COVERAGE_INCOMPLETE:{regime}")
        conditional[regime] = _model().fit(x[mask], y[mask])
    fragility_model = _model().fit(x, fragility)
    schema_hash = sha256(json.dumps({"sport": sport, "features": names}, sort_keys=True).encode()).hexdigest()
    return UpsetPathwayArtifact(sport, artifact_id, names, schema_hash, regime_model,
                                conditional, fragility_model, allowed_regimes)


def predict_upset_pathway(artifact: UpsetPathwayArtifact, row: Mapping[str, Any]) -> dict[str, Any]:
    x = _matrix([row], artifact.feature_names)
    regime_probs = artifact.regime_model.predict_proba(x)[0]
    regimes = []
    for index, regime in enumerate(artifact.regime_model.classes_):
        conditional = float(artifact.conditional_models[str(regime)].predict_proba(x)[0, 1])
        mechanisms = artifact.regime_mechanisms[str(regime)]
        regimes.append({
            "regime_id": str(regime), "probability": float(regime_probs[index]),
            "upset_probability_given_regime": conditional,
            "mechanism_families": list(mechanisms),
            "favorite_failure": str(regime) != "NORMAL",
            "underdog_success": conditional >= .5,
        })
    package = {
        "schema_version": SCHEMA_VERSION, "sport": artifact.sport,
        "fitted_artifact_id": artifact.artifact_id,
        "feature_schema_hash": artifact.feature_schema_hash,
        "probability_source_types": ["MODEL_INPUT", "REGIME_INPUT"],
        "regimes": regimes,
        "independent_favorite_fragility_probability": float(
            artifact.fragility_model.predict_proba(x)[0, 1]
        ),
    }
    return {**package, "aggregation": validate_and_aggregate_upset_pathways(package)}


def walk_forward_metrics(
    *, artifact: UpsetPathwayArtifact, validation_rows: Sequence[Mapping[str, Any]],
    upset_labels: Sequence[int], minimum_n: int = 40,
) -> dict[str, Any]:
    if len(validation_rows) < minimum_n or len(validation_rows) != len(upset_labels):
        raise UpsetTrainingInvalid("WALK_FORWARD_VALIDATION_SAMPLE_INSUFFICIENT")
    probabilities = [
        predict_upset_pathway(artifact, row)["aggregation"]["unconditional_raw_upset_probability"]
        for row in validation_rows
    ]
    return {
        "status": "EVALUATED", "sport": artifact.sport, "n": len(probabilities),
        "brier_score": float(brier_score_loss(upset_labels, probabilities)),
        "log_loss": float(log_loss(upset_labels, probabilities, labels=[0, 1])),
        "promotion_eligible": False,
        "promotion_blocker": "CHAMPION_CHALLENGER_COMPARISON_REQUIRED",
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "SPORT_FEATURE_CONTRACTS", "SPORT_REGIME_MECHANISMS",
           "UpsetPathwayArtifact", "UpsetTrainingInvalid", "fit_upset_pathway_challenger",
           "predict_upset_pathway", "walk_forward_metrics"]
