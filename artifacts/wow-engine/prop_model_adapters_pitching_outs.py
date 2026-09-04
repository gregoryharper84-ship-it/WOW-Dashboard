"""Runtime adapter for WOW_PROP_FITTED_MODEL_V1, model family
MLB_PITCHER_OUTS_WORKLOAD_NB_V1 (MLB starting-pitcher outs recorded).

Trained by scripts/train_mlb_pitching_outs.py against real Retrosheet-derived
rows in Supabase table wow_mlb_retrosplits_rows. See that script's docstring
for the full data provenance and modeling design.

STATUS: NOT YET REGISTERED IN PRODUCTION. `register()` below is written but
must not be called from application startup until:
  1. the artifact produced by scripts/train_mlb_pitching_outs.py has been
     reviewed and explicitly promoted by governance (Greg, per the active
     ChatGPT-out-of-credit ratification substitution) to
     wow_prop_fitted_model_artifacts with a real lifecycle_state, and
  2. wow_prop_evidence_snapshots has real hydrated PITCHING_OUTS rows to
     serve as `features["game_log"]` / `features["box_score_log"]` at
     inference time (as of 2026-09-04 it has zero rows for this stat_type --
     this adapter cannot run against live evidence until that is populated).

This module deliberately follows the exact same invariants as
prop_model_adapters.mlb_pitcher_so_failure_path_nb_v1_adapter:
  * consumes only the resolved, immutable ResolvedArtifact
    (artifact_payload -- fitted constants -- comes from the certified
    registry row, never from a caller or a hardcoded literal here);
  * consumes only the server-owned PropInferenceRequest and router-hydrated
    evidence features;
  * never publishes, calibrates, persists, or executes;
  * returns a raw, direction-free RawDiscreteDistribution.
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
    mix_failure_paths,
)
from prop_fitted_provider import ResolvedArtifact, register_model_family_adapter
from prop_model_adapters import nb_pmf, shrink, _parse_box_score_log  # shared math/parsing, single source of truth

MLB_PITCHER_OUTS_MODEL_FAMILY = "MLB_PITCHER_OUTS_WORKLOAD_NB_V1"

TAG_EARLY_HOOK_RISK = "outs-early-hook-risk"


def mlb_pitcher_outs_workload_nb_v1_adapter(
    artifact: ResolvedArtifact,
    request: PropInferenceRequest,
    features: Mapping[str, Any],
) -> RawDiscreteDistribution:
    payload = artifact.artifact_payload
    try:
        league_mean_out_normal = float(payload["league_mean_out_normal"])
        league_mean_out_short = float(payload["league_mean_out_short"])
        league_shortened_rate = float(payload["league_shortened_rate"])
        dispersion_r = float(payload["dispersion_r"])
        shortened_outs_threshold = float(payload["shortened_outs_threshold"])
        shrinkage_k_rate = float(payload["shrinkage_k_rate"])
        shrinkage_k_regime = float(payload["shrinkage_k_regime"])
        max_support_k = int(payload["max_support_k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID",
            "MLB_PITCHER_OUTS_WORKLOAD_NB_V1 artifact_payload is missing required fitted constants",
        ) from exc

    # For this stat family the target (outs) IS the historical series itself,
    # so game_log here is expected to be the pitcher's own prior-start outs
    # values (equivalently box_score_log.outs) -- there is no separate rate
    # numerator/denominator the way SO has (so_values / out_values).
    box_score_log = _parse_box_score_log(features.get("box_score_log"))
    if not isinstance(box_score_log, list) or not box_score_log:
        raise PropDistributionContractError(
            "PROP_EVIDENCE_FEATURE_MISALIGNED",
            "box_score_log must be a non-empty prior-start history",
        )

    out_values = [entry.outs for entry in box_score_log]
    n_prior = len(out_values)

    normal_vals = [o for o in out_values if o >= shortened_outs_threshold]
    short_vals = [o for o in out_values if o < shortened_outs_threshold]
    prior_mean_out_normal = (sum(normal_vals) / len(normal_vals)) if normal_vals else float("nan")
    prior_mean_out_short = (sum(short_vals) / len(short_vals)) if short_vals else float("nan")
    prior_shortened_rate = len(short_vals) / n_prior

    coverage_failures: list[str] = []
    if n_prior < 1:
        coverage_failures.append("ZERO_PRIOR_STARTS")

    ood_score = 1.0 / (1.0 + n_prior)

    p_short = shrink(prior_shortened_rate, league_shortened_rate, n_prior, shrinkage_k_regime)
    mu_normal = shrink(prior_mean_out_normal, league_mean_out_normal, n_prior, shrinkage_k_rate)
    mu_short = shrink(prior_mean_out_short, league_mean_out_short, n_prior, shrinkage_k_rate)

    pmf_normal = nb_pmf(mu_normal, dispersion_r, max_support_k)
    pmf_short = nb_pmf(mu_short, dispersion_r, max_support_k)
    support = mix_failure_paths(((p_short, pmf_short), (1.0 - p_short, pmf_normal)))

    failure_path_tags: list[str] = []
    if p_short >= 0.35:
        failure_path_tags.append(TAG_EARLY_HOOK_RISK)

    failure_path_evidence: dict[str, Any] = {
        "tags": failure_path_tags,
        "n_prior_starts": n_prior,
        "prior_mean_out_normal": prior_mean_out_normal if math.isfinite(prior_mean_out_normal) else None,
        "prior_mean_out_short": prior_mean_out_short if math.isfinite(prior_mean_out_short) else None,
        "prior_shortened_rate": prior_shortened_rate,
        "shortened_outing_probability": p_short,
        "mu_normal": mu_normal,
        "mu_short": mu_short,
        "v1_scope_note": "no opponent contact/patience adjustment in this artifact version",
    }

    in_distribution = not coverage_failures
    coverage = CoverageDecision(
        in_distribution=in_distribution,
        ood_score=min(max(ood_score, 0.0), 1.0),
        coverage_failures=tuple(coverage_failures),
    )

    feature_snapshot_hash = sha256(
        "|".join(
            (
                request.evidence_snapshot_id,
                str(n_prior),
                format(prior_shortened_rate, ".12g"),
                format(mu_normal, ".12g"),
                format(mu_short, ".12g"),
            )
        ).encode("utf-8")
    ).hexdigest()

    return RawDiscreteDistribution(
        support=support,
        coverage=coverage,
        model_artifact_version=artifact.bundle.model_artifact_version,
        training_code_sha=artifact.bundle.training_code_sha,
        training_dataset_hash=artifact.bundle.training_dataset_hash,
        feature_schema_version=artifact.bundle.feature_schema_version,
        feature_transform_sha=sha256(str(payload.get("feature_transform_version", "")).encode("utf-8")).hexdigest(),
        feature_snapshot_hash=feature_snapshot_hash,
        artifact_checksum=artifact.bundle.artifact_checksum,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
        failure_path_evidence=failure_path_evidence,
    )


def register() -> None:
    """Production registration seam -- DO NOT call from startup until the
    artifact is governance-promoted (see module docstring)."""
    register_model_family_adapter(MLB_PITCHER_OUTS_MODEL_FAMILY, mlb_pitcher_outs_workload_nb_v1_adapter)
