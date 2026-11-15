"""Research-only market dependency and MMA/boxing style challengers.

Pure, synthetic-testable diagnostics. No serving imports, storage writes, wagers,
model certification, mutation of supplied forecasts, or terminal authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
EXPERIMENT_ONLY = True


def _finite_prob(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    number = float(value)
    return number if isfinite(number) and 0.0 <= number <= 1.0 else None


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None


def _quantile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank empirical quantile, for diagnostic stress only."""
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(fraction * len(ordered) + 0.999999) - 1))]


def market_dependency_shadow(
    forecast: Mapping[str, Any],
    frozen_history: Sequence[Mapping[str, Any]],
    *,
    min_cohort_n: int = 30,
) -> dict[str, Any]:
    """Compare adverse historical residual tails within sport/market-role.

    History MUST be from a predesignated development fold with immutable pregame
    issuance, official settlement before target issuance, and independent event
    identity. The caller is responsible for selecting that fold; this function
    additionally eliminates duplicate prediction IDs. The shadow stress result
    is NOT a calibrated sporting lower bound and never affects publication.
    """
    output: dict[str, Any] = {
        "status": "MODEL_INPUTS_INSUFFICIENT",
        "sport": forecast.get("sport"), "market_role": forecast.get("market_role"),
        "original_calibrated_probability": forecast.get("calibrated_probability"),
        "original_calibrated_lower_bound": forecast.get("calibrated_lower_bound"),
        "shadow_lower_bound": None, "shadow_empirical_excess_tail": None,
        "market_dependent": None, "cohort_n": {"dependent": 0, "independent": 0},
        "probability_mutated": False, "publication_allowed": False,
        "automatic_parameter_mutation": False, "can_execute": CAN_EXECUTE,
    }
    p = _finite_prob(forecast.get("calibrated_probability"))
    lower = _finite_prob(forecast.get("calibrated_lower_bound"))
    weight = _finite_prob(forecast.get("market_prior_weight"))
    when = _aware(forecast.get("immutable_model_timestamp"))
    sport = str(forecast.get("sport") or "").strip().upper()
    role = str(forecast.get("market_role") or "").strip().upper()
    if not forecast.get("prediction_id") or not sport or not role or when is None:
        return output
    if p is None or lower is None or lower > p or weight is None:
        return output
    market_dependent = weight > 0.50  # existing V17 diagnostic threshold
    output["market_dependent"] = market_dependent
    if not market_dependent:
        return {**output, "status": "NOT_MARKET_DEPENDENT", "shadow_lower_bound": lower}
    independent_prob = _finite_prob(forecast.get("independent_model_probability"))
    if independent_prob is None or isinstance(min_cohort_n, bool) or min_cohort_n < 30:
        return output
    output["independent_model_probability"] = independent_prob
    dependent_residuals: list[float] = []
    independent_residuals: list[float] = []
    seen_prediction_ids: set[str] = set()
    for record in frozen_history:
        if str(record.get("sport") or "").strip().upper() != sport:
            continue
        if str(record.get("market_role") or "").strip().upper() != role:
            continue
        identity = str(record.get("prediction_id") or "").strip()
        if not identity or identity in seen_prediction_ids or record.get("immutable_pregame") is not True:
            continue
        issued = _aware(record.get("immutable_model_timestamp"))
        settled = _aware(record.get("official_settlement_timestamp"))
        historical_p = _finite_prob(record.get("calibrated_probability"))
        historical_weight = _finite_prob(record.get("market_prior_weight"))
        outcome = record.get("official_binary_outcome")
        if issued is None or settled is None or not issued < settled < when:
            continue
        if historical_p is None or historical_weight is None:
            continue
        if type(outcome) not in (int, float) or outcome not in (0, 1):
            continue
        seen_prediction_ids.add(identity)
        adverse_residual = max(0.0, historical_p - float(outcome))
        (dependent_residuals if historical_weight > 0.50 else independent_residuals).append(adverse_residual)
    output["cohort_n"] = {
        "dependent": len(dependent_residuals),
        "independent": len(independent_residuals),
    }
    if min(len(dependent_residuals), len(independent_residuals)) < min_cohort_n:
        return {**output, "status": "INSUFFICIENT_SAMPLE"}
    excess = max(0.0, _quantile(dependent_residuals, 0.90) - _quantile(independent_residuals, 0.90))
    return {
        **output, "status": "CHALLENGER_DIAGNOSTIC_ONLY",
        "shadow_empirical_excess_tail": excess,
        "shadow_lower_bound": max(0.0, lower - excess),
        "independent_market_disagreement": abs(p - independent_prob),
    }


def combat_style_mixture_shadow(
    *,
    sport: str,
    regimes: Sequence[Mapping[str, Any]],
    style_features: Mapping[str, Any],
) -> dict[str, Any]:
    """Mixture of mutually exclusive, exhaustive fitted style regimes.

    Input frequencies/conditional probabilities must originate from frozen,
    pregame fitted artifacts. Feature values and provenance are gating inputs,
    not fitted coefficients. This module never invents them. A stress scenario
    sum omits uncertainty in regime weights and is not a calibrated lower bound.
    """
    output: dict[str, Any] = {
        "status": "MODEL_INPUTS_INSUFFICIENT", "sport": sport,
        "mixture_probability_shadow": None, "stress_floor_shadow": None,
        "failure_path_score": None, "material_failure_path": None,
        "probability_mutated": False, "publication_allowed": False,
        "automatic_parameter_mutation": False, "can_execute": CAN_EXECUTE,
    }
    required_feature = {
        "MMA": "takedown_defense_vs_chain_wrestling_delta",
        "BOXING": "high_guard_pressure_jab_absorption",
    }.get(str(sport).strip().upper())
    if not required_feature or not regimes:
        return output
    feature_value = style_features.get(required_feature)
    if isinstance(feature_value, bool) or not isinstance(feature_value, (float, int)):
        return output
    if not isfinite(float(feature_value)):
        return output
    if not style_features.get("feature_snapshot_hash"):
        return {**output, "status": "SOURCE_PROVENANCE_MISSING"}
    total_weight = mix = stress = failure = 0.0
    labels: set[str] = set()
    for regime in regimes:
        label = str(regime.get("regime") or "").strip()
        weight = _finite_prob(regime.get("regime_probability"))
        probability = _finite_prob(regime.get("conditional_win_probability"))
        floor = _finite_prob(regime.get("conditional_win_stress_floor"))
        if not label or label in labels or None in (weight, probability, floor):
            return output
        if not regime.get("fitted_artifact_hash") or not regime.get("pregame_evidence_hash"):
            return {**output, "status": "SOURCE_PROVENANCE_MISSING"}
        if floor > probability:
            return output
        labels.add(label)
        total_weight += weight
        mix += weight * probability
        stress += weight * floor
        if regime.get("adverse_style_path") is True:
            failure += weight * (1 - probability)
    if abs(total_weight - 1.0) > 1e-9:
        return output
    return {
        **output, "status": "CHALLENGER_DIAGNOSTIC_ONLY",
        "mixture_probability_shadow": mix,
        "stress_floor_shadow": stress,
        "failure_path_score": failure,
        "material_failure_path": failure > 0.20,  # research hypothesis only
        "required_style_feature": required_feature,
        "regime_count": len(regimes),
    }
