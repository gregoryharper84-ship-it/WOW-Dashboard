"""Governed runtime adapters for certified NFL direct player-prop artifacts."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

import numpy as np

from prop_distribution_contract import CoverageDecision, PropDistributionContractError, PropInferenceRequest, RawDiscreteDistribution
from prop_fitted_provider import ResolvedArtifact, register_model_family_adapter

YARD_MODEL_FAMILY = "NFL_DIRECT_PROP_RIDGE_RESIDUAL_V1"
TD_MODEL_FAMILY = "NFL_DIRECT_PROP_LOGIT_V1"
FEATURE_TRANSFORM_VERSION = "NFL_DIRECT_PROP_STRICT_PRIOR_L10_V1"


def _floats(values: Any, field: str) -> list[float]:
    if not isinstance(values, list) or len(values) < 10:
        raise PropDistributionContractError("NFL_PROP_HISTORY_INSUFFICIENT", f"{field} requires at least 10 prior games")
    out = []
    for value in values[-10:]:
        if isinstance(value, bool):
            raise PropDistributionContractError("NFL_PROP_HISTORY_INVALID", field)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise PropDistributionContractError("NFL_PROP_HISTORY_INVALID", field) from exc
        if not math.isfinite(number) or number < 0:
            raise PropDistributionContractError("NFL_PROP_HISTORY_INVALID", field)
        out.append(number)
    return out


def _opportunities(box_score_log: Any, route: str) -> list[float]:
    if not isinstance(box_score_log, list) or len(box_score_log) < 10:
        raise PropDistributionContractError("NFL_PROP_BOX_SCORE_HISTORY_INSUFFICIENT", "box_score_log requires at least 10 rows")
    key = {"PASSING_YARDS": "attempts", "RUSHING_YARDS": "carries", "RECEIVING_YARDS": "targets"}.get(route)
    out = []
    for row in box_score_log[-10:]:
        if not isinstance(row, Mapping):
            raise PropDistributionContractError("NFL_PROP_BOX_SCORE_INVALID", "box score row must be an object")
        if route == "ANYTIME_TD":
            value = float(row.get("carries", 0) or 0) + float(row.get("targets", 0) or 0)
        else:
            value = row.get(key) if key else None
            if value is None:
                raise PropDistributionContractError("NFL_PROP_OPPORTUNITY_MISSING", key or route)
            value = float(value)
        if not math.isfinite(value) or value < 0:
            raise PropDistributionContractError("NFL_PROP_OPPORTUNITY_INVALID", route)
        out.append(value)
    return out


def feature_vector(route: str, game_log: Any, box_score_log: Any) -> list[float]:
    values = _floats(game_log, "game_log")
    opps = _opportunities(box_score_log, route)
    a = np.asarray(values, dtype=float)
    o = np.asarray(opps, dtype=float)
    if route == "ANYTIME_TD":
        return [float(a.mean()), float(a[-5:].mean()), float(a[-1]), float(o.mean()), float(o[-5:].mean())]
    return [float(a.mean()), float(a[-5:].mean()), float(a[-1]), float(a.std(ddof=0)), float(o.mean()), float(o[-5:].mean())]


def _standardized_prediction(payload: Mapping[str, Any], x: list[float]) -> float:
    try:
        mean = np.asarray(payload["feature_mean"], dtype=float)
        scale = np.asarray(payload["feature_scale"], dtype=float)
        coef = np.asarray(payload["coef"], dtype=float)
        intercept = float(payload["intercept"])
    except Exception as exc:
        raise PropDistributionContractError("NFL_PROP_ARTIFACT_PAYLOAD_INVALID", "fitted parameter payload is incomplete") from exc
    vector = np.asarray(x, dtype=float)
    if vector.shape != mean.shape or mean.shape != scale.shape or scale.shape != coef.shape or np.any(scale <= 0):
        raise PropDistributionContractError("NFL_PROP_ARTIFACT_DIMENSION_MISMATCH", "feature parameter dimensions differ")
    return float(intercept + np.dot((vector - mean) / scale, coef))


def _yard_support(payload: Mapping[str, Any], prediction: float) -> dict[int, float]:
    residual_pmf = payload.get("residual_pmf")
    if not isinstance(residual_pmf, Mapping) or not residual_pmf:
        raise PropDistributionContractError("NFL_PROP_RESIDUAL_PMF_MISSING", "yardage artifact lacks residual PMF")
    support: dict[int, float] = {}
    center = int(round(max(prediction, 0.0)))
    for raw_residual, raw_probability in residual_pmf.items():
        try:
            residual = int(raw_residual)
            probability = float(raw_probability)
        except (TypeError, ValueError) as exc:
            raise PropDistributionContractError("NFL_PROP_RESIDUAL_PMF_INVALID", "residual PMF entry invalid") from exc
        outcome = max(0, center + residual)
        support[outcome] = support.get(outcome, 0.0) + probability
    total = sum(support.values())
    if not math.isfinite(total) or total <= 0:
        raise PropDistributionContractError("NFL_PROP_RESIDUAL_PMF_INVALID", "residual PMF has no mass")
    return {k: v / total for k, v in support.items()}


def nfl_direct_prop_adapter(artifact: ResolvedArtifact, request: PropInferenceRequest, features: Mapping[str, Any]) -> RawDiscreteDistribution:
    payload = artifact.artifact_payload
    route = str(payload.get("route") or request.stat_type).strip().upper()
    if route != request.stat_type.upper():
        raise PropDistributionContractError("NFL_PROP_ARTIFACT_ROUTE_MISMATCH", "artifact route does not match request")
    x = feature_vector(route, features.get("game_log"), features.get("box_score_log"))
    score = _standardized_prediction(payload, x)
    if artifact.model_family == YARD_MODEL_FAMILY:
        support = _yard_support(payload, max(score, 0.0))
    elif artifact.model_family == TD_MODEL_FAMILY:
        probability = 1.0 / (1.0 + math.exp(-max(min(score, 35.0), -35.0)))
        support = {0: 1.0 - probability, 1: probability}
    else:
        raise PropDistributionContractError("NFL_PROP_MODEL_FAMILY_UNSUPPORTED", artifact.model_family)

    min_opp = {"PASSING_YARDS": 5.0, "RUSHING_YARDS": 1.0, "RECEIVING_YARDS": 1.0, "ANYTIME_TD": 1.0}.get(route, 1.0)
    opps = _opportunities(features.get("box_score_log"), route)
    failures = []
    if len(opps) < 10:
        failures.append("NFL_PROP_HISTORY_INSUFFICIENT")
    if float(np.mean(opps)) < min_opp:
        failures.append("NFL_PROP_PRIOR_OPPORTUNITY_BELOW_TRAINING_SUPPORT")
    coverage = CoverageDecision(
        in_distribution=not failures,
        ood_score=0.0 if not failures else 1.0,
        coverage_failures=tuple(failures),
    )
    snapshot_hash = sha256((request.evidence_snapshot_id + "|" + "|".join(format(v, ".12g") for v in x)).encode()).hexdigest()
    return RawDiscreteDistribution(
        support=support,
        coverage=coverage,
        model_artifact_version=artifact.bundle.model_artifact_version,
        training_code_sha=artifact.bundle.training_code_sha,
        training_dataset_hash=artifact.bundle.training_dataset_hash,
        feature_schema_version=artifact.bundle.feature_schema_version,
        feature_transform_sha=sha256(FEATURE_TRANSFORM_VERSION.encode()).hexdigest(),
        feature_snapshot_hash=snapshot_hash,
        artifact_checksum=artifact.bundle.artifact_checksum,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
        failure_path_evidence={"route": route, "history_games": 10, "market_probability_used": False},
    )


def register() -> None:
    register_model_family_adapter(YARD_MODEL_FAMILY, nfl_direct_prop_adapter)
    register_model_family_adapter(TD_MODEL_FAMILY, nfl_direct_prop_adapter)
