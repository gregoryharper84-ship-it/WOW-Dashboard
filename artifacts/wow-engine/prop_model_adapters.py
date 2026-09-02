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


def opponent_k_factor(
    opponent_context: Any, league_k_per_pa: float, opponent_factor_clip: tuple[float, float]
) -> tuple[float, bool, float | None]:
    """Single source of truth for the opponent strikeout-rate multiplier.

    Both the point-estimate adapter below and its calibration bootstrap
    (prop_calibration_adapters._mlb_pitcher_so_resample_fn) must derive
    opp_factor from this one function -- never reimplement the read/clip
    logic independently -- so a material contradiction shifts the point
    estimate and its calibrated bounds by the exact same factor instead of
    the bootstrap silently resampling around the wrong (unsuppressed)
    distribution. Returns (opp_factor, opp_factor_clipped, opp_k_per_pa).
    """
    opp_k_per_pa: float | None = None
    if isinstance(opponent_context, Mapping) and opponent_context.get("k_rate_per_pa") is not None:
        try:
            candidate = float(opponent_context["k_rate_per_pa"])
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and math.isfinite(candidate):
            opp_k_per_pa = candidate

    opp_factor = 1.0
    opp_factor_clipped = False
    if opp_k_per_pa is not None and league_k_per_pa > 0:
        raw_opp_factor = opp_k_per_pa / league_k_per_pa
        opp_factor = min(max(raw_opp_factor, opponent_factor_clip[0]), opponent_factor_clip[1])
        opp_factor_clipped = opp_factor != raw_opp_factor
    return opp_factor, opp_factor_clipped, opp_k_per_pa


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

# Typed failure-path identifiers (postmortem patch WOW-PATCH-2026-09-02, issues
# #116/#119: the 2026-09-01 Manaea miss). These label WHY the opponent factor
# moved mu; they never carry their own probability weight. STRIKEOUT_RATE_
# SUPPRESSION is numerically load-bearing -- it names an already-active,
# already-reviewed shift on mu_normal/mu_short (opp_factor, below). OPPONENT_
# CONTACT_EXTENSION is evidence-only/corroborating: contact_rate_per_pa and
# chase_rate have no fitted coefficient in this artifact, so they are captured
# and reported, never multiplied into mu -- adding an unfitted coefficient
# here would be exactly the kind of invented adjustment WOW governance
# prohibits. A future certified artifact revision that fits a real contact-
# rate coefficient would extend opp_factor's computation, not this tag.
TAG_STRIKEOUT_RATE_SUPPRESSION = "STRIKEOUT_RATE_SUPPRESSION"
TAG_OPPONENT_CONTACT_EXTENSION = "OPPONENT_CONTACT_EXTENSION"

# Below this fraction of the league K/PA rate, an opponent's strikeout
# suppression is material enough to name explicitly rather than leave as an
# unlabeled number inside mu. This is a labeling/explanation threshold only --
# it decides whether to attach a tag, not how much mu moves (opp_factor,
# already reviewed, does that). Adjustable by a future governance patch.
_MATERIAL_SUPPRESSION_OPP_FACTOR_THRESHOLD = 0.90

# Evidence-only corroboration thresholds for the contact/chase tag. Same
# caveat: labeling only, no probability weight.
_HIGH_CONTACT_RATE_PER_PA_THRESHOLD = 0.80
_LOW_CHASE_RATE_THRESHOLD = 0.22


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
      * opponent_context (optional): {"k_rate_per_pa": number,
        "contact_rate_per_pa": number, "chase_rate": number,
        "expected_batters_faced": number} -- the opponent lineup's rolling
        strikeout/contact/chase profile against this pitcher's handedness,
        and (evidence-only, see below) the expected workload for this start.
        Only k_rate_per_pa carries a reviewed fitted coefficient (opp_factor,
        below) and can move mu; contact_rate_per_pa, chase_rate, and
        expected_batters_faced have none and are captured/reported as
        explanatory evidence only -- see failure_path_evidence on the
        returned distribution and TAG_OPPONENT_CONTACT_EXTENSION above. When
        k_rate_per_pa is absent the adapter runs with a neutral (1.0)
        opponent factor rather than inventing a value.
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

    def _opt_float(key: str) -> float | None:
        if not isinstance(opponent_context, Mapping) or opponent_context.get(key) is None:
            return None
        try:
            value = float(opponent_context[key])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    # Evidence-only: captured/reported below, never multiplied into mu (see
    # module docstring on TAG_OPPONENT_CONTACT_EXTENSION for why).
    opp_contact_rate_per_pa = _opt_float("contact_rate_per_pa")
    opp_chase_rate = _opt_float("chase_rate")
    opp_expected_batters_faced = _opt_float("expected_batters_faced")

    ood_score = 1.0 / (1.0 + n_prior)
    if not math.isfinite(prior_so_per_out) and total_out > 0:
        coverage_failures.append("PRIOR_RATE_NOT_FINITE")

    rate = shrink(prior_so_per_out, league_so_per_out, n_prior, shrinkage_k_rate)
    p_short = shrink(prior_shortened_rate, league_shortened_rate, n_prior, shrinkage_k_regime)
    opp_factor, opp_factor_clipped, opp_k_per_pa = opponent_k_factor(
        opponent_context, league_k_per_pa, opponent_factor_clip
    )

    # Single application point: opp_factor multiplies mu exactly once, for
    # both regimes, here. The calibration bootstrap resampler
    # (prop_calibration_adapters._mlb_pitcher_so_resample_fn) calls this same
    # opponent_k_factor() so the point estimate and its calibrated bounds
    # move by the identical factor -- neither a second, independent penalty
    # nor a bootstrap left blind to the suppression. See
    # test_no_duplicate_suppression_penalty_single_multiplication_point in
    # test_prop_model_adapters.py.
    mu_normal_before_opponent_factor = rate * outs_normal_scale
    mu_short_before_opponent_factor = rate * outs_short_scale
    mu_normal = mu_normal_before_opponent_factor * opp_factor
    mu_short = mu_short_before_opponent_factor * opp_factor

    pmf_normal = nb_pmf(mu_normal, dispersion_r, max_support_k)
    pmf_short = nb_pmf(mu_short, dispersion_r, max_support_k)
    support = mix_failure_paths(((p_short, pmf_short), (1.0 - p_short, pmf_normal)))

    failure_path_tags: list[str] = []
    if opp_factor <= _MATERIAL_SUPPRESSION_OPP_FACTOR_THRESHOLD:
        failure_path_tags.append(TAG_STRIKEOUT_RATE_SUPPRESSION)
    if (
        opp_contact_rate_per_pa is not None and opp_contact_rate_per_pa >= _HIGH_CONTACT_RATE_PER_PA_THRESHOLD
    ) or (opp_chase_rate is not None and opp_chase_rate <= _LOW_CHASE_RATE_THRESHOLD):
        failure_path_tags.append(TAG_OPPONENT_CONTACT_EXTENSION)

    failure_path_evidence: dict[str, Any] = {
        "tags": failure_path_tags,
        "opp_k_rate_per_pa": opp_k_per_pa,
        "league_k_rate_per_pa": league_k_per_pa,
        "opponent_factor": opp_factor,
        "opponent_factor_clipped": opp_factor_clipped,
        "opponent_factor_source": "k_rate_per_pa" if opp_k_per_pa is not None else "NEUTRAL_NO_OPPONENT_EVIDENCE",
        "opponent_contact_rate_per_pa": opp_contact_rate_per_pa,
        "opponent_chase_rate": opp_chase_rate,
        "opponent_expected_batters_faced": opp_expected_batters_faced,
        "mu_normal_before_opponent_factor": mu_normal_before_opponent_factor,
        "mu_normal_after_opponent_factor": mu_normal,
        "mu_short_before_opponent_factor": mu_short_before_opponent_factor,
        "mu_short_after_opponent_factor": mu_short,
        "prior_so_per_out": prior_so_per_out if math.isfinite(prior_so_per_out) else None,
        "prior_shortened_rate": prior_shortened_rate,
        "shortened_outing_probability": p_short,
        "n_prior_starts": n_prior,
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
        failure_path_evidence=failure_path_evidence,
    )


def register() -> None:
    """Production registration seam -- called once at process startup."""
    register_model_family_adapter(MLB_PITCHER_SO_MODEL_FAMILY, mlb_pitcher_so_failure_path_nb_v1_adapter)
    from wnba_prop_model_adapter import MODEL_FAMILY as WNBA_MODEL_FAMILY, wnba_prop_poisson_logglm_v1_adapter
    register_model_family_adapter(WNBA_MODEL_FAMILY, wnba_prop_poisson_logglm_v1_adapter)
