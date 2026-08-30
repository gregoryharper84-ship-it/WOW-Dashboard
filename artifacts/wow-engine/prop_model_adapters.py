"""Reviewed production model-family adapters for WOW_PROP_FITTED_MODEL_V1.

Each adapter here is registered by immutable, code-owned `model_family` with
``prop_fitted_provider.register_model_family_adapter`` and consumes only:
  * the resolved, immutable ``ResolvedArtifact`` (its ``artifact_payload`` --
    fitted constants -- comes from the certified registry row, never from a
    caller or a hardcoded literal in this module);
  * the server-owned ``PropInferenceRequest``;
  * hydrated evidence features assembled by the router (never caller input).

An adapter never publishes, calibrates, persists, or executes; it returns a
raw, direction-free ``RawDiscreteDistribution``.

``nb_pmf`` / ``shrink`` are the exact functions used by
``scripts/train_mlb_pitcher_strikeouts.py`` to fit and evaluate the artifact
this module serves -- training and inference share one source of truth for
the model math, so a change here cannot silently diverge fitted metrics from
runtime behavior.
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


def shrink(pitcher_value: float, league_value: float, n: float, k: float) -> float:
    """Empirical-Bayes shrinkage toward a league rate; NaN pitcher_value
    (no eligible history) falls back to the league value entirely."""
    if pitcher_value is None or (isinstance(pitcher_value, float) and math.isnan(pitcher_value)):
        return league_value
    lam = n / (n + k)
    return league_value + lam * (pitcher_value - league_value)


def nb_pmf(mu: float, r: float, max_k: int) -> dict[int, float]:
    """Negative-binomial PMF (mean mu, dispersion r) truncated at max_k, with
    the tail folded into the max_k bucket so the finite support sums to 1
    (max_k is interpreted as "max_k or more")."""
    if mu <= 0:
        mu = 1e-6
    p = r / (r + mu)
    pmf: dict[int, float] = {}
    log_p, log_1mp = math.log(p), math.log(1 - p)
    running = 0.0
    for k in range(max_k):
        log_coef = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        prob = math.exp(log_coef + r * log_p + k * log_1mp)
        pmf[k] = prob
        running += prob
    pmf[max_k] = max(0.0, 1.0 - running)
    return pmf


MLB_PITCHER_SO_MODEL_FAMILY = "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"


class _BoxScoreEntry:
    __slots__ = ("outs",)

    def __init__(self, outs: int):
        self.outs = outs


def _parse_box_score_log(box_score_log: Any) -> list[_BoxScoreEntry]:
    """Runtime contract for this adapter's box_score_log entries:
    ``{"outs": <non-negative int outs recorded that start>}``. Anything else
    is a coverage failure -- this adapter does not guess a missing feature."""
    if not isinstance(box_score_log, list):
        raise PropDistributionContractError(
            "PROP_BOX_SCORE_LOG_INVALID", "box_score_log must be a list"
        )
    parsed = []
    for entry in box_score_log:
        if not isinstance(entry, Mapping) or "outs" not in entry:
            raise PropDistributionContractError(
                "PROP_BOX_SCORE_LOG_MISSING_OUTS",
                "each box_score_log entry must be an object with an 'outs' field",
            )
        outs = entry["outs"]
        if isinstance(outs, bool) or not isinstance(outs, (int, float)) or outs < 0:
            raise PropDistributionContractError(
                "PROP_BOX_SCORE_LOG_OUTS_INVALID", "box_score_log 'outs' must be a non-negative number"
            )
        parsed.append(_BoxScoreEntry(outs=int(outs)))
    return parsed


def mlb_pitcher_so_failure_path_nb_v1_adapter(
    artifact: ResolvedArtifact,
    request: PropInferenceRequest,
    features: Mapping[str, Any],
) -> RawDiscreteDistribution:
    """Two-regime (normal-length vs. shortened outing) negative-binomial
    mixture for MLB starting-pitcher strikeouts. See
    scripts/train_mlb_pitcher_strikeouts.py for the offline fit that
    produced ``artifact.artifact_payload``'s fitted_constants and for the
    out-of-sample evaluation against a naive baseline.

    Feature contract consumed from the hydrated evidence (see
    api_prod_market._model_features):
      * game_log: list[number] -- this pitcher's own prior-start strikeout
        counts (>=10 enforced upstream by wow_prop_evidence_snapshot).
      * box_score_log: list[{"outs": number}] -- same length/order as
        game_log; outs recorded in that same prior start.
      * opponent_context (optional): {"k_rate_per_pa": number} -- the
        opponent lineup's rolling strikeout rate. The current evidence
        acquisition pipeline does not yet populate this field; when absent
        the adapter runs with a neutral (1.0) opponent factor rather than
        inventing a value.
    """
    payload = artifact.artifact_payload
    try:
        fitted = payload["fitted_constants"]
        league_so_per_out = float(fitted["league_so_per_out"])
        league_k_per_pa = float(fitted["league_k_per_pa"])
        league_shortened_rate = float(fitted["league_shortened_rate"])
        outs_normal_scale = float(fitted["outs_normal_scale"])
        outs_short_scale = float(fitted["outs_short_scale"])
        dispersion_r = float(fitted["dispersion_r"])
        shrinkage_k_rate = float(payload["shrinkage_k_rate"])
        shrinkage_k_regime = float(payload["shrinkage_k_regime"])
        shortened_outs_threshold = float(payload["shortened_outs_threshold"])
        max_support_k = int(payload["max_support_k"])
        opponent_factor_clip = tuple(float(v) for v in payload["opponent_factor_clip"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID",
            "MLB_PITCHER_SO_FAILURE_PATH_NB_V1 artifact_payload is missing required fitted constants",
        ) from exc

    game_log = features.get("game_log")
    box_score_log = _parse_box_score_log(features.get("box_score_log"))
    if not isinstance(game_log, list) or len(game_log) != len(box_score_log) or not game_log:
        raise PropDistributionContractError(
            "PROP_EVIDENCE_FEATURE_MISALIGNED",
            "game_log and box_score_log must be equal-length, non-empty parallel histories",
        )

    n_prior = len(game_log)
    so_values = [float(v) for v in game_log]
    out_values = [entry.outs for entry in box_score_log]
    total_out = sum(out_values)

    coverage_failures: list[str] = []
    if total_out <= 0:
        coverage_failures.append("ZERO_TOTAL_PRIOR_OUTS")
    prior_so_per_out = (sum(so_values) / total_out) if total_out > 0 else float("nan")
    prior_shortened_rate = (
        sum(1 for o in out_values if o < shortened_outs_threshold) / n_prior
    )

    opponent_context = features.get("opponent_context")
    opp_k_per_pa = None
    if isinstance(opponent_context, Mapping) and opponent_context.get("k_rate_per_pa") is not None:
        try:
            opp_k_per_pa = float(opponent_context["k_rate_per_pa"])
        except (TypeError, ValueError):
            opp_k_per_pa = None

    ood_score = 1.0 / (1.0 + n_prior)
    if not math.isfinite(prior_so_per_out) and total_out > 0:
        coverage_failures.append("PRIOR_RATE_NOT_FINITE")

    rate = shrink(prior_so_per_out, league_so_per_out, n_prior, shrinkage_k_rate)
    p_short = shrink(prior_shortened_rate, league_shortened_rate, n_prior, shrinkage_k_regime)
    opp_factor = 1.0
    if opp_k_per_pa is not None and math.isfinite(opp_k_per_pa) and league_k_per_pa > 0:
        opp_factor = min(max(opp_k_per_pa / league_k_per_pa, opponent_factor_clip[0]), opponent_factor_clip[1])

    mu_normal = rate * outs_normal_scale * opp_factor
    mu_short = rate * outs_short_scale * opp_factor

    pmf_normal = nb_pmf(mu_normal, dispersion_r, max_support_k)
    pmf_short = nb_pmf(mu_short, dispersion_r, max_support_k)
    support = mix_failure_paths(((p_short, pmf_short), (1.0 - p_short, pmf_normal)))

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
                format(prior_so_per_out, ".12g") if math.isfinite(prior_so_per_out) else "nan",
                format(prior_shortened_rate, ".12g"),
                format(opp_factor, ".12g"),
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
        feature_transform_sha=sha256(payload["feature_transform_version"].encode("utf-8")).hexdigest(),
        feature_snapshot_hash=feature_snapshot_hash,
        artifact_checksum=artifact.bundle.artifact_checksum,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def register() -> None:
    """Production registration seam -- called once at process startup."""
    register_model_family_adapter(MLB_PITCHER_SO_MODEL_FAMILY, mlb_pitcher_so_failure_path_nb_v1_adapter)
    from wnba_prop_model_adapter import MODEL_FAMILY as WNBA_MODEL_FAMILY, wnba_prop_poisson_logglm_v1_adapter
    register_model_family_adapter(WNBA_MODEL_FAMILY, wnba_prop_poisson_logglm_v1_adapter)
