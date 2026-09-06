"""Runtime adapter for WOW_PROP_FITTED_MODEL_V1, model family
MLB_BATTER_PLATE_APPEARANCES_NB_V1 (MLB batter plate appearances).

Trained by scripts/train_mlb_plate_appearances.py against real
Retrosheet-derived rows in Supabase table wow_mlb_retrosplits_rows. See that
script's docstring for full data provenance and modeling design.

Production evidence semantics match training:
- prior_pa_log: this batter's strictly prior official game PA history,
- batting_slot: current confirmed batting-order position, 1..9,
- team_alignment: current game side, 1=HOME and 0=AWAY.

The canonical frozen evidence schema already carries generic ``game_log`` and
``opportunity_ledger`` fields.  PA hydration stores the prior-PA series in
``game_log`` and the current lineup context in ``opportunity_ledger``.  This
adapter normalizes those frozen values into the model's training feature names
before inference; it never reads those values from caller model metadata.

A missing/unconfirmed batting slot is a genuine coverage failure, not a value
to guess.
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
from prop_model_adapters import nb_pmf, shrink

MLB_BATTER_PA_MODEL_FAMILY = "MLB_BATTER_PLATE_APPEARANCES_NB_V1"

COVERAGE_FAILURE_LINEUP_UNCONFIRMED = "BATTING_SLOT_UNCONFIRMED"
COVERAGE_FAILURE_ZERO_PRIOR = "ZERO_PRIOR_GAMES"
TAG_LOW_LINEUP_SLOT_CEILING = "pa-low-lineup-slot-ceiling"


def _parse_prior_pa_log(value: Any) -> list[int]:
    if not isinstance(value, list):
        raise PropDistributionContractError(
            "PROP_PRIOR_PA_LOG_INVALID", "prior_pa_log must be a list"
        )
    parsed = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise PropDistributionContractError(
                "PROP_PRIOR_PA_LOG_VALUE_INVALID", "each prior_pa_log entry must be a non-negative number"
            )
        parsed.append(int(v))
    return parsed


def _normalize_pa_features(features: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    """Resolve training features from the immutable generic evidence envelope.

    Direct top-level feature keys remain supported for deterministic unit/E2E
    fixtures. Production hydration uses game_log + opportunity_ledger.  When
    the incoming mapping is a mutable dict, populate the normalized names so
    the subsequent calibration adapter receives the exact same frozen values
    after fitted inference.
    """
    opportunity = features.get("opportunity_ledger")
    if not isinstance(opportunity, Mapping):
        opportunity = {}

    prior = features.get("prior_pa_log")
    if prior is None:
        prior = opportunity.get("prior_pa_log")
    if prior is None:
        prior = features.get("game_log")

    batting_slot = features.get("batting_slot")
    if batting_slot is None:
        batting_slot = opportunity.get("batting_slot")

    team_alignment = features.get("team_alignment")
    if team_alignment is None:
        team_alignment = opportunity.get("team_alignment")

    if isinstance(features, dict):
        if prior is not None:
            features.setdefault("prior_pa_log", prior)
        if batting_slot is not None:
            features.setdefault("batting_slot", batting_slot)
        if team_alignment is not None:
            features.setdefault("team_alignment", team_alignment)

    return prior, batting_slot, team_alignment


def mlb_batter_plate_appearances_nb_v1_adapter(
    artifact: ResolvedArtifact,
    request: PropInferenceRequest,
    features: Mapping[str, Any],
) -> RawDiscreteDistribution:
    payload = artifact.artifact_payload
    try:
        league_mean_pa_by_cell_raw = payload["league_mean_pa_by_cell"]
        league_mean_pa_overall = float(payload["league_mean_pa_overall"])
        dispersion_r = float(payload["dispersion_r"])
        shrinkage_k_rate = float(payload["shrinkage_k_rate"])
        max_support_k = int(payload["max_support_k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropDistributionContractError(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID",
            "MLB_BATTER_PLATE_APPEARANCES_NB_V1 artifact_payload is missing required fitted constants",
        ) from exc
    league_mean_pa_by_cell = {
        tuple(int(x) for x in k.split("_")): float(v)
        for k, v in league_mean_pa_by_cell_raw.items()
    }

    coverage_failures: list[str] = []
    prior_raw, batting_slot, team_alignment = _normalize_pa_features(features)

    if not isinstance(batting_slot, int) or not (1 <= batting_slot <= 9) or isinstance(batting_slot, bool):
        coverage_failures.append(COVERAGE_FAILURE_LINEUP_UNCONFIRMED)
        batting_slot = None
    if not isinstance(team_alignment, int) or team_alignment not in (0, 1) or isinstance(team_alignment, bool):
        coverage_failures.append(COVERAGE_FAILURE_LINEUP_UNCONFIRMED)
        team_alignment = None

    prior_pa_log = _parse_prior_pa_log(prior_raw if prior_raw is not None else [])
    n_prior = len(prior_pa_log)
    if n_prior < 1:
        coverage_failures.append(COVERAGE_FAILURE_ZERO_PRIOR)

    prior_mean_pa = (sum(prior_pa_log) / n_prior) if n_prior > 0 else float("nan")
    ood_score = 1.0 / (1.0 + n_prior)

    if batting_slot is not None and team_alignment is not None:
        cell_mean = league_mean_pa_by_cell.get((batting_slot, team_alignment), league_mean_pa_overall)
    else:
        cell_mean = league_mean_pa_overall

    mu = shrink(prior_mean_pa, cell_mean, n_prior, shrinkage_k_rate)
    support = nb_pmf(mu, dispersion_r, max_support_k)

    failure_path_tags: list[str] = []
    if batting_slot is not None and batting_slot >= 8:
        failure_path_tags.append(TAG_LOW_LINEUP_SLOT_CEILING)

    failure_path_evidence: dict[str, Any] = {
        "tags": failure_path_tags,
        "n_prior_games": n_prior,
        "prior_mean_pa": prior_mean_pa if math.isfinite(prior_mean_pa) else None,
        "batting_slot": batting_slot,
        "team_alignment": team_alignment,
        "league_cell_mean_pa": cell_mean,
        "mu": mu,
        "v1_scope_note": "no opposing-starter-length, bullpen, or game-script features in this artifact version",
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
                str(batting_slot),
                str(team_alignment),
                format(mu, ".12g"),
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
        feature_transform_sha=sha256(
            str(payload.get("feature_transform_version", "")).encode("utf-8")
        ).hexdigest(),
        feature_snapshot_hash=feature_snapshot_hash,
        artifact_checksum=artifact.bundle.artifact_checksum,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
        failure_path_evidence=failure_path_evidence,
    )


def register() -> None:
    """Production registration seam for the governance-promoted PA artifact."""
    register_model_family_adapter(
        MLB_BATTER_PA_MODEL_FAMILY,
        mlb_batter_plate_appearances_nb_v1_adapter,
    )