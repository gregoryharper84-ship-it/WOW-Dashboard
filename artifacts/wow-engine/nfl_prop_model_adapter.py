"""Governed runtime adapter for NFL_PROP_ROLLING_FITTED_V1.

The adapter consumes only an immutable certified artifact plus current pregame
PROP_EVIDENCE_V1 history. It never uses sportsbook prices, external projections,
recent hit-rate heuristics, or narrative judgment as model probability.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence

from prop_distribution_contract import (
    CoverageDecision,
    PropDistributionContractError,
    PropInferenceRequest,
    RawDiscreteDistribution,
)
from prop_fitted_provider import ResolvedArtifact, register_model_family_adapter

MODEL_FAMILY = "NFL_PROP_ROLLING_FITTED_V1"
FEATURE_NAMES = (
    "l10_stat_mean",
    "l5_stat_mean",
    "last_stat",
    "l10_opportunity_mean",
    "l5_opportunity_mean",
    "last_opportunity",
)
ALLOWED_KINDS = {"GAUSSIAN_RIDGE_BLEND_V1", "BERNOULLI_LOGISTIC_BLEND_V1"}


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PropDistributionContractError("NFL_PROP_HISTORY_INVALID", field)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PropDistributionContractError("NFL_PROP_HISTORY_INVALID", field) from exc
    if not math.isfinite(parsed):
        raise PropDistributionContractError("NFL_PROP_HISTORY_INVALID", field)
    return parsed


def _vector_from_pairs(pairs: Sequence[tuple[float, float]]) -> tuple[float, ...]:
    if len(pairs) < 10:
        raise PropDistributionContractError(
            "NFL_PROP_HISTORY_INSUFFICIENT", "NFL model requires ten aligned prior games"
        )
    recent = list(pairs)[-10:]
    recent5 = recent[-5:]
    stats10 = [v for v, _ in recent]
    stats5 = [v for v, _ in recent5]
    opp10 = [o for _, o in recent]
    opp5 = [o for _, o in recent5]
    return (
        sum(stats10) / 10.0,
        sum(stats5) / 5.0,
        stats10[-1],
        sum(opp10) / 10.0,
        sum(opp5) / 5.0,
        opp10[-1],
    )


def history_pairs(features: Mapping[str, Any]) -> tuple[tuple[float, float], ...]:
    game_log = features.get("game_log")
    box_score_log = features.get("box_score_log")
    if not isinstance(game_log, list) or not isinstance(box_score_log, list):
        raise PropDistributionContractError(
            "NFL_PROP_HISTORY_MISSING", "game_log and box_score_log must be lists"
        )
    if len(game_log) != len(box_score_log) or len(game_log) < 10:
        raise PropDistributionContractError(
            "NFL_PROP_HISTORY_MISALIGNED", "NFL model requires ten aligned prior games"
        )
    pairs: list[tuple[float, float]] = []
    for i, (stat, box) in enumerate(zip(game_log, box_score_log)):
        if not isinstance(box, Mapping):
            raise PropDistributionContractError("NFL_PROP_BOX_SCORE_INVALID", f"box_score_log[{i}]")
        value = _finite(stat, f"game_log[{i}]")
        opportunity = _finite(box.get("opportunity"), f"box_score_log[{i}].opportunity")
        if opportunity <= 0:
            raise PropDistributionContractError(
                "NFL_PROP_OPPORTUNITY_INVALID", "historical opportunity must be positive"
            )
        pairs.append((value, opportunity))
    return tuple(pairs[-10:])


def feature_vector(features: Mapping[str, Any]) -> tuple[float, ...]:
    return _vector_from_pairs(history_pairs(features))


def _parameters(payload: Mapping[str, Any], vector: tuple[float, ...]) -> tuple[str, list[float], float, float, float]:
    try:
        model_kind = str(payload["model_kind"])
        names = tuple(str(v) for v in payload["feature_names"])
        mean = [float(v) for v in payload["feature_mean"]]
        scale = [float(v) for v in payload["feature_scale"]]
        coef = [float(v) for v in payload["coef"]]
        intercept = float(payload["intercept"])
        blend = float(payload["blend_weight_fitted"])
        max_z = float(payload["max_abs_z_for_coverage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL fitted artifact payload is incomplete"
        ) from exc
    if model_kind not in ALLOWED_KINDS or names != FEATURE_NAMES:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL fitted feature/model contract mismatch"
        )
    if not (len(mean) == len(scale) == len(coef) == len(vector) == len(FEATURE_NAMES)):
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL fitted vector dimensions are invalid"
        )
    numeric = mean + scale + coef + [intercept, blend, max_z]
    if any(not math.isfinite(v) for v in numeric) or any(v <= 0 for v in scale):
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL fitted parameters are non-finite"
        )
    if not 0.10 <= blend <= 1.0 or max_z <= 0:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL fitted blend/coverage parameter invalid"
        )
    z = [(v - m) / s for v, m, s in zip(vector, mean, scale)]
    linear = intercept + sum(c * value for c, value in zip(coef, z))
    max_abs_z = max(abs(v) for v in z)
    return model_kind, z, linear, blend, max_abs_z


def expected_value(payload: Mapping[str, Any], vector: tuple[float, ...]) -> tuple[float, float]:
    model_kind, _z, linear, blend, max_abs_z = _parameters(payload, vector)
    baseline = float(vector[0])
    if model_kind == "GAUSSIAN_RIDGE_BLEND_V1":
        fitted = linear
        value = (1.0 - blend) * baseline + blend * fitted
    else:
        fitted = 1.0 / (1.0 + math.exp(-min(max(linear, -35.0), 35.0)))
        value = (1.0 - blend) * min(max(baseline, 0.0), 1.0) + blend * fitted
        value = min(max(value, 1e-9), 1.0 - 1e-9)
    if not math.isfinite(value):
        raise PropDistributionContractError(
            "NFL_PROP_LINEAR_PREDICTOR_INVALID", "NFL fitted expectation is non-finite"
        )
    return value, max_abs_z


def _normal_cdf(x: float, mean: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mean) / (sigma * math.sqrt(2.0))))


def distribution_for_vector(payload: Mapping[str, Any], vector: tuple[float, ...]) -> tuple[dict[int, float], float]:
    mean, max_abs_z = expected_value(payload, vector)
    kind = str(payload["model_kind"])
    if kind == "BERNOULLI_LOGISTIC_BLEND_V1":
        return {0: 1.0 - mean, 1: mean}, max_abs_z
    try:
        sigma = float(payload["residual_sigma"])
        artifact_support_min = int(payload["support_min"])
        support_max = int(payload["support_max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL Gaussian distribution metadata is invalid"
        ) from exc
    # V17's RawDiscreteDistribution contract admits only non-negative integer
    # outcomes. The original 2026-09-20 NFL Gaussian artifacts were trained with
    # support_min=-50, which made the otherwise valid fitted PMF impossible to
    # instantiate. Preserve the fitted mean/sigma and all probability mass by
    # censoring that legacy lower tail into outcome 0. For every supported
    # positive prop line this is probability-preserving: MORE/LESS mass is
    # unchanged; only impossible negative support labels are collapsed to zero.
    support_min = max(0, artifact_support_min)
    if not math.isfinite(sigma) or sigma <= 0 or support_max <= support_min:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "NFL Gaussian support/sigma is invalid"
        )
    support: dict[int, float] = {}
    for k in range(support_min, support_max + 1):
        lo = -math.inf if k == support_min else k - 0.5
        hi = math.inf if k == support_max else k + 0.5
        p_lo = 0.0 if lo == -math.inf else _normal_cdf(lo, mean, sigma)
        p_hi = 1.0 if hi == math.inf else _normal_cdf(hi, mean, sigma)
        support[k] = max(0.0, p_hi - p_lo)
    total = sum(support.values())
    if total <= 0 or not math.isfinite(total):
        raise PropDistributionContractError("NFL_PROP_PMF_INVALID", "NFL Gaussian PMF could not normalize")
    return {k: p / total for k, p in support.items()}, max_abs_z


def nfl_prop_rolling_fitted_v1_adapter(
    artifact: ResolvedArtifact,
    request: PropInferenceRequest,
    features: Mapping[str, Any],
) -> RawDiscreteDistribution:
    payload = artifact.artifact_payload
    if str(payload.get("model_family") or "") != MODEL_FAMILY:
        raise PropDistributionContractError("MODEL_CALIBRATOR_BUNDLE_MISMATCH", "NFL model family mismatch")
    if str(payload.get("stat_type") or "").upper() != request.stat_type.upper():
        raise PropDistributionContractError("MODEL_CALIBRATOR_BUNDLE_MISMATCH", "NFL artifact stat mismatch")
    vector = feature_vector(features)
    support, max_abs_z = distribution_for_vector(payload, vector)
    max_z = float(payload["max_abs_z_for_coverage"])
    failures: list[str] = []
    if max_abs_z > max_z:
        failures.append("NFL_PROP_FEATURE_VECTOR_OOD")
    coverage = CoverageDecision(
        in_distribution=not failures,
        ood_score=min(max(max_abs_z / max_z, 0.0), 1.0),
        coverage_failures=tuple(failures),
    )
    feature_snapshot_hash = sha256(
        "|".join([request.evidence_snapshot_id, request.stat_type.upper()] + [format(v, ".12g") for v in vector]).encode("utf-8")
    ).hexdigest()
    return RawDiscreteDistribution(
        support=support,
        coverage=coverage,
        model_artifact_version=artifact.bundle.model_artifact_version,
        training_code_sha=artifact.bundle.training_code_sha,
        training_dataset_hash=artifact.bundle.training_dataset_hash,
        feature_schema_version=artifact.bundle.feature_schema_version,
        feature_transform_sha=sha256(str(payload.get("feature_transform_version") or "").encode("utf-8")).hexdigest(),
        feature_snapshot_hash=feature_snapshot_hash,
        artifact_checksum=artifact.bundle.artifact_checksum,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def register() -> None:
    register_model_family_adapter(MODEL_FAMILY, nfl_prop_rolling_fitted_v1_adapter)


__all__ = [
    "MODEL_FAMILY",
    "FEATURE_NAMES",
    "feature_vector",
    "history_pairs",
    "expected_value",
    "distribution_for_vector",
    "nfl_prop_rolling_fitted_v1_adapter",
    "register",
]
