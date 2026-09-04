"""Runtime adapters for WOW_PROP_FITTED_MODEL_V1, model families
MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1 and
MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1 (MLB starting-pitcher pitch
composition).

Trained by scripts/train_mlb_pitch_composition.py against real
Retrosheet-derived rows in Supabase table wow_mlb_retrosplits_rows. See that
script's docstring for full data provenance and modeling design.

STATUS: NOT YET REGISTERED IN PRODUCTION -- same two preconditions as
prop_model_adapters_pitching_outs.py:
  1. governance ratification + promotion of each artifact to
     wow_prop_fitted_model_artifacts with a real lifecycle_state, and
  2. wow_prop_evidence_snapshots hydration for STRIKES_THROWN / BALLS_THROWN
     (zero rows for either as of 2026-09-04).

Follows the same invariants as prop_model_adapters_pitching_outs, and reuses
the same box_score_log evidence shape -- but strikes/balls require the
per-start pitch composition, so box_score_log entries here must additionally
carry "strikes" and "pitches" fields (outs alone is insufficient to derive
either target). See _parse_pitch_composition_log below for the exact
contract; this is a distinct, stricter parse than
prop_model_adapters._parse_box_score_log (outs-only), by design -- silently
reusing the looser outs-only parser here would let a row with no pitch data
pass coverage checks it should fail.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
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
from prop_model_adapters import nb_pmf, shrink

MLB_STRIKES_THROWN_MODEL_FAMILY = "MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1"
MLB_BALLS_THROWN_MODEL_FAMILY = "MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1"

TAG_EARLY_HOOK_RISK = "pitch-composition-early-hook-risk"


@dataclass(frozen=True)
class _CompositionEntry:
    outs: int
    strikes: int
    pitches: int

    @property
    def balls(self) -> int:
        return self.pitches - self.strikes


def _parse_pitch_composition_log(box_score_log: Any) -> list[_CompositionEntry]:
    """Contract for this module's box_score_log entries:
    {"outs": <int>, "strikes": <int>, "pitches": <int>}, pitches >= strikes.
    Anything else is a coverage failure -- no guessing a missing feature."""
    if not isinstance(box_score_log, list):
        raise PropDistributionContractError(
            "PROP_BOX_SCORE_LOG_INVALID", "box_score_log must be a list"
        )
    parsed = []
    for entry in box_score_log:
        if not isinstance(entry, Mapping) or not {"outs", "strikes", "pitches"}.issubset(entry):
            raise PropDistributionContractError(
                "PROP_BOX_SCORE_LOG_MISSING_COMPOSITION",
                "each box_score_log entry must be an object with 'outs', 'strikes', and 'pitches' fields",
            )
        outs, strikes, pitches = entry["outs"], entry["strikes"], entry["pitches"]
        for name, val in (("outs", outs), ("strikes", strikes), ("pitches", pitches)):
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
                raise PropDistributionContractError(
                    "PROP_BOX_SCORE_LOG_COMPOSITION_INVALID",
                    f"box_score_log '{name}' must be a non-negative number",
                )
        if pitches < strikes:
            raise PropDistributionContractError(
                "PROP_BOX_SCORE_LOG_COMPOSITION_INVALID",
                "box_score_log 'pitches' cannot be less than 'strikes'",
            )
        parsed.append(_CompositionEntry(outs=int(outs), strikes=int(strikes), pitches=int(pitches)))
    return parsed


def _adapter_impl(
    artifact: ResolvedArtifact,
    request: PropInferenceRequest,
    features: Mapping[str, Any],
    *,
    target: str,  # "strikes" or "balls"
    tag: str,
) -> RawDiscreteDistribution:
    payload = artifact.artifact_payload
    try:
        league_mean_normal = float(payload["league_mean_normal"])
        league_mean_short = float(payload["league_mean_short"])
        league_shortened_rate = float(payload["league_shortened_rate"])
        dispersion_r = float(payload["dispersion_r"])
        shortened_outs_threshold = float(payload["shortened_outs_threshold"])
        shrinkage_k_rate = float(payload["shrinkage_k_rate"])
        shrinkage_k_regime = float(payload["shrinkage_k_regime"])
        max_support_k = int(payload["max_support_k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID",
            f"{artifact.model_family} artifact_payload is missing required fitted constants",
        ) from exc

    entries = _parse_pitch_composition_log(features.get("box_score_log"))
    if not entries:
        raise PropDistributionContractError(
            "PROP_EVIDENCE_FEATURE_MISALIGNED",
            "box_score_log must be a non-empty prior-start history",
        )

    n_prior = len(entries)
    normal_entries = [e for e in entries if e.outs >= shortened_outs_threshold]
    short_entries = [e for e in entries if e.outs < shortened_outs_threshold]

    def _target_val(e: _CompositionEntry) -> int:
        return e.strikes if target == "strikes" else e.balls

    normal_vals = [_target_val(e) for e in normal_entries]
    short_vals = [_target_val(e) for e in short_entries]
    prior_mean_normal = (sum(normal_vals) / len(normal_vals)) if normal_vals else float("nan")
    prior_mean_short = (sum(short_vals) / len(short_vals)) if short_vals else float("nan")
    prior_shortened_rate = len(short_entries) / n_prior

    coverage_failures: list[str] = []
    if n_prior < 1:
        coverage_failures.append("ZERO_PRIOR_STARTS")

    ood_score = 1.0 / (1.0 + n_prior)

    p_short = shrink(prior_shortened_rate, league_shortened_rate, n_prior, shrinkage_k_regime)
    mu_normal = shrink(prior_mean_normal, league_mean_normal, n_prior, shrinkage_k_rate)
    mu_short = shrink(prior_mean_short, league_mean_short, n_prior, shrinkage_k_rate)

    pmf_normal = nb_pmf(mu_normal, dispersion_r, max_support_k)
    pmf_short = nb_pmf(mu_short, dispersion_r, max_support_k)
    support = mix_failure_paths(((p_short, pmf_short), (1.0 - p_short, pmf_normal)))

    failure_path_tags: list[str] = []
    if p_short >= 0.35:
        failure_path_tags.append(tag)

    failure_path_evidence: dict[str, Any] = {
        "tags": failure_path_tags,
        "target": target,
        "n_prior_starts": n_prior,
        "prior_mean_normal": prior_mean_normal if math.isfinite(prior_mean_normal) else None,
        "prior_mean_short": prior_mean_short if math.isfinite(prior_mean_short) else None,
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
                target,
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


def mlb_pitcher_strikes_thrown_workload_nb_v1_adapter(
    artifact: ResolvedArtifact, request: PropInferenceRequest, features: Mapping[str, Any]
) -> RawDiscreteDistribution:
    return _adapter_impl(artifact, request, features, target="strikes", tag=TAG_EARLY_HOOK_RISK)


def mlb_pitcher_balls_thrown_workload_nb_v1_adapter(
    artifact: ResolvedArtifact, request: PropInferenceRequest, features: Mapping[str, Any]
) -> RawDiscreteDistribution:
    return _adapter_impl(artifact, request, features, target="balls", tag=TAG_EARLY_HOOK_RISK)


def register() -> None:
    """Production registration seam -- DO NOT call from startup until both
    artifacts are governance-promoted (see module docstring)."""
    register_model_family_adapter(MLB_STRIKES_THROWN_MODEL_FAMILY, mlb_pitcher_strikes_thrown_workload_nb_v1_adapter)
    register_model_family_adapter(MLB_BALLS_THROWN_MODEL_FAMILY, mlb_pitcher_balls_thrown_workload_nb_v1_adapter)
