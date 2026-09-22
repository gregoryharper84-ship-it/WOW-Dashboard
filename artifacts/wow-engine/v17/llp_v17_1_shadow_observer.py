"""Persistent shadow-observation layer for the LLP V17.1 sharpness challenger.

This module records research-only ranking views and evaluation metadata without
changing production ranking, calibration, market priors, terminal authority, or
execution posture.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from v17.llp_v17_1_sharpness_challenger import (
    CAN_EXECUTE,
    CandidateProbability,
    governance_classification,
    market_divergence_diagnostic,
    score_candidate,
)

SERVING_MODE = "SHADOW_ONLY"
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_RANKING_MUTATION_ALLOWED = False
PRODUCTION_CALIBRATION_MUTATION_ALLOWED = False
PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
SHADOW_SCHEMA_VERSION = "LLP_V17_1_SHADOW_OBSERVATION_V1"


class ShadowObserverError(ValueError):
    pass


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ShadowObservation:
    shadow_schema_version: str
    observation_id: str
    prediction_id: str
    candidate_id: str
    official_event_id: str
    sport: str
    league: str | None
    selection: str
    opponent_or_field: str | None
    scheduled_start_utc: str | None
    requested_slate_date: str | None
    research_run_id: str | None
    scan_stage: str | None
    market_role: str | None
    controlling_specialist: str | None
    model_artifact_id: str | None
    model_timestamp: str
    observed_at: str
    calibrated_probability: float
    calibrated_lower_bound: float
    calibrated_upper_bound: float | None
    lower_bound_width: float
    point_rank_score: float
    lower_bound_rank_score: float
    uncertainty_adjusted_score: float
    lambda_penalty: float
    governance_class: str
    hard_blockers: tuple[str, ...]
    soft_uncertainties: tuple[str, ...]
    rank_eligible_shadow: bool
    market_no_vig_probability: float | None
    market_divergence: float | None
    market_divergence_status: str | None
    market_prior_weight: float
    probability_mutated: bool
    production_policy_mutated: bool
    can_execute: bool
    terminal_authority: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_identity(row: Mapping[str, Any], field: str) -> str:
    value = _text(row.get(field))
    if value is None:
        raise ShadowObserverError(f"MODEL_INPUTS_INSUFFICIENT: {field}_required")
    return value


def build_shadow_observation(
    governed_row: Mapping[str, Any],
    *,
    lambda_penalty: float,
    hard_blockers: Iterable[str] = (),
    soft_uncertainties: Iterable[str] = (),
    market_no_vig_probability: float | None = None,
    divergence_threshold: float = 0.08,
    observed_at: str | None = None,
) -> ShadowObservation:
    """Build one append-oriented research shadow record from a governed row."""
    prediction_id = _require_identity(governed_row, "prediction_id")
    candidate_id = _require_identity(governed_row, "candidate_id")
    official_event_id = _require_identity(governed_row, "official_event_id")
    sport = _require_identity(governed_row, "sport").upper()
    selection = _require_identity(governed_row, "selection")
    model_timestamp = _text(governed_row.get("immutable_model_timestamp")) or _text(governed_row.get("model_timestamp"))
    if model_timestamp is None:
        raise ShadowObserverError("MODEL_INPUTS_INSUFFICIENT: model_timestamp_required")

    hard = tuple(str(code) for code in hard_blockers if code)
    soft = tuple(str(code) for code in soft_uncertainties if code)
    candidate = CandidateProbability(
        candidate_id=candidate_id,
        calibrated_probability=governed_row.get("calibrated_probability"),
        calibrated_lower_bound=governed_row.get("calibrated_lower_bound"),
        calibrated_upper_bound=governed_row.get("calibrated_upper_bound"),
        hard_blockers=hard,
        soft_uncertainties=soft,
    )
    ranking = score_candidate(candidate, lambda_penalty=lambda_penalty)

    divergence_value = None
    divergence_status = None
    market_prior_weight = 0.0
    if market_no_vig_probability is not None:
        diagnostic = market_divergence_diagnostic(
            ranking.calibrated_probability,
            market_no_vig_probability,
            threshold=divergence_threshold,
        )
        divergence_value = diagnostic.signed_divergence
        divergence_status = diagnostic.status
        market_prior_weight = diagnostic.market_prior_weight

    observation_id = f"{prediction_id}::{selection}::{lambda_penalty:.4f}::{SHADOW_SCHEMA_VERSION}"
    return ShadowObservation(
        shadow_schema_version=SHADOW_SCHEMA_VERSION,
        observation_id=observation_id,
        prediction_id=prediction_id,
        candidate_id=candidate_id,
        official_event_id=official_event_id,
        sport=sport,
        league=_text(governed_row.get("league")),
        selection=selection,
        opponent_or_field=_text(governed_row.get("opponent_or_field")) or _text(governed_row.get("opponent")),
        scheduled_start_utc=_text(governed_row.get("scheduled_start_utc")) or _text(governed_row.get("event_start_time")),
        requested_slate_date=_text(governed_row.get("requested_slate_date")),
        research_run_id=_text(governed_row.get("research_run_id")),
        scan_stage=_text(governed_row.get("scan_stage")),
        market_role=_text(governed_row.get("market_role")) or _text(governed_row.get("selected_market_role")),
        controlling_specialist=_text(governed_row.get("controlling_specialist")),
        model_artifact_id=_text(governed_row.get("model_artifact_id")) or _text(governed_row.get("model_artifact_version")),
        model_timestamp=model_timestamp,
        observed_at=observed_at or _utc_now_iso(),
        calibrated_probability=ranking.calibrated_probability,
        calibrated_lower_bound=ranking.calibrated_lower_bound,
        calibrated_upper_bound=governed_row.get("calibrated_upper_bound"),
        lower_bound_width=ranking.uncertainty_width_to_lower,
        point_rank_score=ranking.winner_likelihood_score,
        lower_bound_rank_score=ranking.confidence_floor_score,
        uncertainty_adjusted_score=ranking.uncertainty_adjusted_score,
        lambda_penalty=ranking.lambda_penalty,
        governance_class=governance_classification(hard, soft),
        hard_blockers=hard,
        soft_uncertainties=soft,
        rank_eligible_shadow=ranking.rank_eligible_shadow,
        market_no_vig_probability=market_no_vig_probability,
        market_divergence=divergence_value,
        market_divergence_status=divergence_status,
        market_prior_weight=market_prior_weight,
        probability_mutated=False,
        production_policy_mutated=False,
        can_execute=CAN_EXECUTE,
        terminal_authority=TERMINAL_AUTHORITY,
    )


def build_lambda_grid_observations(
    governed_row: Mapping[str, Any],
    *,
    lambdas: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0),
    hard_blockers: Iterable[str] = (),
    soft_uncertainties: Iterable[str] = (),
    market_no_vig_probability: float | None = None,
    divergence_threshold: float = 0.08,
    observed_at: str | None = None,
) -> list[ShadowObservation]:
    if not lambdas:
        raise ShadowObserverError("MODEL_INPUTS_INSUFFICIENT: lambda_grid_empty")
    return [
        build_shadow_observation(
            governed_row,
            lambda_penalty=float(lam),
            hard_blockers=hard_blockers,
            soft_uncertainties=soft_uncertainties,
            market_no_vig_probability=market_no_vig_probability,
            divergence_threshold=divergence_threshold,
            observed_at=observed_at,
        )
        for lam in lambdas
    ]


def cohort_key(observation: ShadowObservation) -> tuple[str, str | None, str, str]:
    width = observation.lower_bound_width
    if width < 0.05:
        width_band = "WIDTH_LT_5PP"
    elif width < 0.10:
        width_band = "WIDTH_5_TO_10PP"
    elif width < 0.15:
        width_band = "WIDTH_10_TO_15PP"
    else:
        width_band = "WIDTH_GE_15PP"
    return (observation.sport, observation.league, observation.governance_class, width_band)


def shadow_manifest() -> dict[str, Any]:
    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "serving_mode": SERVING_MODE,
        "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
        "production_ranking_mutation_allowed": PRODUCTION_RANKING_MUTATION_ALLOWED,
        "production_calibration_mutation_allowed": PRODUCTION_CALIBRATION_MUTATION_ALLOWED,
        "production_market_prior_mutation_allowed": PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "CAN_EXECUTE",
    "PRODUCTION_CALIBRATION_MUTATION_ALLOWED",
    "PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED",
    "PRODUCTION_RANKING_MUTATION_ALLOWED",
    "SERVING_MODE",
    "SHADOW_SCHEMA_VERSION",
    "TERMINAL_AUTHORITY",
    "ShadowObservation",
    "ShadowObserverError",
    "build_lambda_grid_observations",
    "build_shadow_observation",
    "cohort_key",
    "shadow_manifest",
]
