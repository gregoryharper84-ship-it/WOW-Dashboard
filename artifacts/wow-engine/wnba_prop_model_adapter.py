"""Governed runtime adapter for WNBA_PROP_POISSON_LOGGLM_V1.

Consumes only immutable certified artifact parameters plus hydrated prior-game
WNBA evidence. The adapter mirrors the offline OFFSET_POISSON_BLEND_V1 fit,
is direction-free, and never calibrates, publishes, persists, prices, or executes.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from prop_distribution_contract import (
    CoverageDecision,
    PropDistributionContractError,
    PropInferenceRequest,
    RawDiscreteDistribution,
)
from prop_fitted_provider import ResolvedArtifact, register_model_family_adapter

MODEL_FAMILY = "WNBA_PROP_POISSON_LOGGLM_V1"
MODEL_KIND = "OFFSET_POISSON_BLEND_V1"
MIN_GLM_BLEND_WEIGHT = 0.10
FEATURE_NAMES = (
    "l10_stat_mean",
    "l5_stat_mean",
    "last_stat",
    "l10_minutes_mean",
    "l5_minutes_mean",
    "last_minutes",
)
CORRECTION_FEATURE_NAMES = (
    "log_l5_to_l10_stat",
    "log_last_to_l10_stat",
    "log_l5_to_l10_minutes",
    "log_last_to_l10_minutes",
)


def poisson_pmf(mu: float, max_k: int) -> dict[int, float]:
    if not math.isfinite(mu) or mu <= 0:
        mu = 1e-9
    p0 = math.exp(-mu)
    support: dict[int, float] = {0: p0}
    running = p0
    pk = p0
    for k in range(1, max_k):
        pk = pk * mu / k
        support[k] = pk
        running += pk
    support[max_k] = max(0.0, 1.0 - running)
    total = sum(support.values())
    if not math.isfinite(total) or total <= 0:
        raise PropDistributionContractError(
            "WNBA_PROP_PMF_INVALID", "Poisson PMF could not be normalized"
        )
    return {k: p / total for k, p in support.items()}


def _count(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_STAT_INVALID", f"{field} must be a non-negative integer count"
        )
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or parsed != int(parsed):
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_STAT_INVALID", f"{field} must be a non-negative integer count"
        )
    return parsed


def _minutes(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_MINUTES_INVALID", "minutes must be a finite positive number"
        )
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 < parsed <= 60:
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_MINUTES_INVALID", "minutes must be within (0, 60]"
        )
    return parsed


def _date_key(value: Any) -> str:
    text = str(value or "").strip()[:10]
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_DATE_INVALID", "each box_score_log row requires an ISO game date"
        ) from exc
    return text


def feature_vector(features: Mapping[str, Any]) -> tuple[float, ...]:
    game_log = features.get("game_log")
    box_score_log = features.get("box_score_log")
    if not isinstance(game_log, list) or not isinstance(box_score_log, list):
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_MISSING", "game_log and box_score_log must be lists"
        )
    if len(game_log) != len(box_score_log) or len(game_log) < 10:
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_MISALIGNED", "WNBA model requires at least ten aligned prior games"
        )

    paired: list[tuple[str, float, float]] = []
    for i, (stat_value, box) in enumerate(zip(game_log, box_score_log)):
        if not isinstance(box, Mapping) or "minutes" not in box or "date" not in box:
            raise PropDistributionContractError(
                "WNBA_PROP_BOX_SCORE_FIELDS_MISSING", "box_score_log rows require date and minutes"
            )
        paired.append((_date_key(box["date"]), _count(stat_value, f"game_log[{i}]"), _minutes(box["minutes"])))

    paired.sort(key=lambda row: row[0])
    dates = [row[0] for row in paired]
    if len(set(dates)) != len(dates):
        raise PropDistributionContractError(
            "WNBA_PROP_HISTORY_DATE_DUPLICATE", "duplicate WNBA game dates are not accepted by this model family"
        )
    recent = paired[-10:]
    recent5 = recent[-5:]
    stats10 = [row[1] for row in recent]
    stats5 = [row[1] for row in recent5]
    mins10 = [row[2] for row in recent]
    mins5 = [row[2] for row in recent5]
    return (
        sum(stats10) / 10.0,
        sum(stats5) / 5.0,
        stats10[-1],
        sum(mins10) / 10.0,
        sum(mins5) / 5.0,
        mins10[-1],
    )


def _correction_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    eps = 0.25
    l10_stat, l5_stat, last_stat, l10_min, l5_min, last_min = vector
    return (
        math.log((l5_stat + eps) / (l10_stat + eps)),
        math.log((last_stat + eps) / (l10_stat + eps)),
        math.log(max(l5_min, 0.1) / max(l10_min, 0.1)),
        math.log(max(last_min, 0.1) / max(l10_min, 0.1)),
    )


def expected_count(artifact_payload: Mapping[str, Any], vector: tuple[float, ...]) -> tuple[float, float]:
    try:
        model_kind = str(artifact_payload["model_kind"])
        names = tuple(str(v) for v in artifact_payload["feature_names"])
        correction_names = tuple(str(v) for v in artifact_payload["correction_feature_names"])
        mean = [float(v) for v in artifact_payload["feature_mean"]]
        scale = [float(v) for v in artifact_payload["feature_scale"]]
        coef = [float(v) for v in artifact_payload["coef"]]
        intercept = float(artifact_payload["intercept"])
        blend_weight = float(artifact_payload["blend_weight_glm"])
        max_z = float(artifact_payload["max_abs_z_for_coverage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "WNBA Poisson artifact payload is incomplete"
        ) from exc
    if model_kind != MODEL_KIND or names != FEATURE_NAMES or correction_names != CORRECTION_FEATURE_NAMES:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "WNBA feature transform contract does not match the certified offset model"
        )
    if not (len(mean) == len(scale) == len(vector) == len(FEATURE_NAMES)) or len(coef) != len(CORRECTION_FEATURE_NAMES):
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "WNBA fitted vector dimensions are invalid"
        )
    numeric = mean + scale + coef + [intercept, blend_weight, max_z]
    if any(not math.isfinite(v) for v in numeric) or any(v <= 0 for v in scale) or max_z <= 0:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "WNBA fitted transform contains invalid numeric parameters"
        )
    if not MIN_GLM_BLEND_WEIGHT <= blend_weight <= 1.0:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "WNBA fitted model component weight is outside the certified range"
        )

    z = [(value - m) / s for value, m, s in zip(vector, mean, scale)]
    max_abs_z = max(abs(v) for v in z)
    correction = _correction_vector(vector)
    eta = intercept + sum(c * value for c, value in zip(coef, correction))
    eta = min(max(eta, -4.0), 4.0)
    baseline = max(vector[0], 0.05)
    glm_mu = baseline * math.exp(eta)
    mu = (1.0 - blend_weight) * baseline + blend_weight * glm_mu
    if not math.isfinite(mu) or mu <= 0:
        raise PropDistributionContractError(
            "WNBA_PROP_LINEAR_PREDICTOR_INVALID", "WNBA fitted expected count is non-finite"
        )
    return mu, max_abs_z


def wnba_prop_poisson_logglm_v1_adapter(
    artifact: ResolvedArtifact,
    request: PropInferenceRequest,
    features: Mapping[str, Any],
) -> RawDiscreteDistribution:
    payload = artifact.artifact_payload
    try:
        payload_stat = str(payload["stat_type"]).upper()
        max_support_k = int(payload["max_support_k"])
        max_z = float(payload["max_abs_z_for_coverage"])
        transform_version = str(payload["feature_transform_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID", "WNBA Poisson artifact metadata is incomplete"
        ) from exc
    if payload_stat != request.stat_type.upper() or max_support_k < 1 or max_z <= 0:
        raise PropDistributionContractError(
            "MODEL_CALIBRATOR_BUNDLE_MISMATCH", "WNBA artifact is not compatible with the requested stat"
        )

    vector = feature_vector(features)
    mu, max_abs_z = expected_count(payload, vector)
    coverage_failures: list[str] = []
    if max_abs_z > max_z:
        coverage_failures.append("WNBA_PROP_FEATURE_VECTOR_OOD")
    if not math.isfinite(mu) or mu <= 0 or mu > max_support_k * 4:
        coverage_failures.append("WNBA_PROP_EXPECTED_COUNT_OUTSIDE_SUPPORT")
    safe_mu = min(max(mu, 1e-9), max_support_k * 4.0)
    support = poisson_pmf(safe_mu, max_support_k)

    coverage = CoverageDecision(
        in_distribution=not coverage_failures,
        ood_score=min(max(max_abs_z / max_z, 0.0), 1.0),
        coverage_failures=tuple(coverage_failures),
    )
    feature_snapshot_hash = sha256(
        "|".join(
            [request.evidence_snapshot_id, request.stat_type.upper()]
            + [format(v, ".12g") for v in vector]
        ).encode("utf-8")
    ).hexdigest()
    return RawDiscreteDistribution(
        support=support,
        coverage=coverage,
        model_artifact_version=artifact.bundle.model_artifact_version,
        training_code_sha=artifact.bundle.training_code_sha,
        training_dataset_hash=artifact.bundle.training_dataset_hash,
        feature_schema_version=artifact.bundle.feature_schema_version,
        feature_transform_sha=sha256(transform_version.encode("utf-8")).hexdigest(),
        feature_snapshot_hash=feature_snapshot_hash,
        artifact_checksum=artifact.bundle.artifact_checksum,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def register() -> None:
    register_model_family_adapter(MODEL_FAMILY, wnba_prop_poisson_logglm_v1_adapter)
