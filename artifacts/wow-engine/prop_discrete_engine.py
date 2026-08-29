"""Governed generic player-prop scoring from a certified discrete PMF.

This is the production replacement for the legacy pitcher-shaped
``FittedParamsBundle`` path.  The fitted provider owns only the raw,
direction-free discrete distribution.  A separately reviewed calibration
adapter converts the requested side probability into calibrated probability
and numerical bounds.  Market evidence remains an objective-separated audit
and never becomes the sporting probability in this module.

No adapter, provider, calibrator, registry row, or result can authorize
execution. ``can_execute`` remains false at every boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping, Optional

from ledger import PredictionRow, determine_publishability
from market import MarketQuote, resolve_market_prior
from prop_distribution_contract import LineProbabilities, PropInferenceRequest, derive_line_probabilities
from prop_fitted_provider import CertifiedInference, infer_certified_distribution


PROP_PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
PROP_MARKET_TYPE = "PROP_DISCRETE_PMF"


class PropCalibrationUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PropCalibrationOutput:
    calibration_status: str
    calibration_method: str
    calibrated_probability: float
    lower_bound: float
    upper_bound: float
    bounds_method_version: str
    effective_sample_size: float

    def validate(self) -> None:
        values = (
            self.calibrated_probability,
            self.lower_bound,
            self.upper_bound,
            self.effective_sample_size,
        )
        if not all(isfinite(float(v)) for v in values):
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_OUTPUT_INVALID",
                "Calibration output contains a non-finite value.",
            )
        if not (0.0 < self.lower_bound <= self.calibrated_probability <= self.upper_bound < 1.0):
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_BOUNDS_INVALID",
                "Calibration must return ordered bounds strictly inside (0,1).",
            )
        if self.effective_sample_size <= 0:
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_ESS_INVALID",
                "Calibration must return a positive effective sample size.",
            )
        if not self.calibration_status.strip() or not self.calibration_method.strip():
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_IDENTITY_INVALID",
                "Calibration status and method are required.",
            )
        if not self.bounds_method_version.strip():
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_BOUNDS_METHOD_INVALID",
                "A governed numerical bounds method version is required.",
            )


CalibrationAdapter = Callable[
    [CertifiedInference, float, LineProbabilities, Mapping[str, Any], int],
    PropCalibrationOutput,
]
_CALIBRATION_ADAPTERS: dict[str, CalibrationAdapter] = {}


def register_prop_calibration_adapter(calibrator_version: str, adapter: CalibrationAdapter) -> None:
    """Register one reviewed calibration adapter by immutable version.

    Registration is code-controlled.  A request cannot select or inject this
    version; it is resolved from the certified artifact bundle in Supabase.
    """
    key = str(calibrator_version or "").strip().upper()
    if not key:
        raise ValueError("calibrator_version is required")
    _CALIBRATION_ADAPTERS[key] = adapter


def clear_prop_calibration_adapters() -> None:
    """Test helper. Production startup must not use this to bypass review."""
    _CALIBRATION_ADAPTERS.clear()


@dataclass(frozen=True)
class DiscretePropScoreResult:
    row: PredictionRow
    inference: CertifiedInference
    line_probabilities: LineProbabilities
    calibration: PropCalibrationOutput


def _directional_probability(line_probs: LineProbabilities, direction: str) -> float:
    side = str(direction or "").strip().upper()
    if side == "MORE":
        value = line_probs.probability_more
    elif side == "LESS":
        value = line_probs.probability_less
    else:
        raise PropCalibrationUnavailable(
            "PROP_DIRECTION_INVALID",
            "Governed prop direction must be MORE or LESS.",
        )
    if not isfinite(float(value)) or not (0.0 < float(value) < 1.0):
        raise PropCalibrationUnavailable(
            "PROP_RAW_PROBABILITY_INVALID",
            "Selected-side raw probability must satisfy strict 0<p<1.",
        )
    return float(value)


def _calibrate(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    version = inference.artifact.bundle.calibrator_version.strip().upper()
    adapter = _CALIBRATION_ADAPTERS.get(version)
    if adapter is None:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE",
            f"No reviewed runtime calibration adapter is registered for {version!r}.",
        )
    result = adapter(inference, raw_probability, line_probs, features, seed)
    if not isinstance(result, PropCalibrationOutput):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_ADAPTER_INVALID_OUTPUT",
            "Calibration adapter must return PropCalibrationOutput.",
        )
    result.validate()
    return result


def score_discrete_prop_end_to_end(
    *,
    client: Any,
    request: PropInferenceRequest,
    event_start_time: str,
    player: str,
    line: float,
    direction: str,
    source_snapshot_id: str,
    features: Mapping[str, Any],
    seed: int,
    money_lane_status: str = "PAYOUT_UNRESOLVED",
    market_side_a: Optional[MarketQuote] = None,
    market_side_b: Optional[MarketQuote] = None,
    infer_fn=infer_certified_distribution,
) -> DiscretePropScoreResult:
    """Score one exact prop without legacy pitcher-regime parameters.

    The raw PMF is resolved from the certified model registry. The direction is
    applied only after the PMF exists. Calibration is then selected from the
    certified bundle's immutable ``calibrator_version``. Market data is
    retained only as comparison evidence with zero sporting-probability weight.
    """
    inference = infer_fn(
        client,
        request=request,
        line=line,
        features=features,
    )
    if not isinstance(inference, CertifiedInference):
        raise PropCalibrationUnavailable(
            "PROP_CERTIFIED_INFERENCE_INVALID",
            "Fitted provider must return CertifiedInference with artifact provenance.",
        )
    distribution = inference.distribution
    if not distribution.coverage.in_distribution:
        reasons = ",".join(distribution.coverage.coverage_failures) or "UNSPECIFIED_OOD"
        raise PropCalibrationUnavailable(
            "PROP_MODEL_OUT_OF_DISTRIBUTION",
            f"Certified provider abstained for this candidate: {reasons}",
        )

    line_probs = derive_line_probabilities(distribution, line)
    raw_probability = _directional_probability(line_probs, direction)
    calibration = _calibrate(inference, raw_probability, line_probs, features, seed)

    market_prior = resolve_market_prior(direction, market_side_a, market_side_b, as_of=request.as_of_timestamp)
    artifact = inference.artifact
    bundle = artifact.bundle

    row = PredictionRow(
        event_id=request.event_id,
        event_start_time=event_start_time,
        sport=request.sport,
        player=player,
        market_type=PROP_MARKET_TYPE,
        stat_type=request.stat_type,
        line=float(line),
        direction=str(direction).upper(),
        source_snapshot_id=source_snapshot_id,
        model_timestamp=request.as_of_timestamp,
        raw_model_probability=raw_probability,
        independent_model_probability=calibration.calibrated_probability,
        effective_sample_size=calibration.effective_sample_size,
        market_prior_available=market_prior.market_prior_available,
        market_prior_probability=market_prior.market_prior_probability,
        market_prior_quality=market_prior.market_prior_quality,
        market_prior_weight=0.0,
        market_prior_weight_source=(
            "OBJECTIVE_SEPARATED_ZERO_WEIGHT"
            if market_prior.market_prior_available
            else "NO_MARKET_PRIOR"
        ),
        reference_market_probability_raw=market_prior.reference_market_probability_raw,
        reference_market_side=market_prior.reference_market_side,
        reference_market_price=market_prior.reference_market_price,
        calibration_status=calibration.calibration_status,
        calibration_method=calibration.calibration_method,
        calibration_version=bundle.calibrator_version,
        calibration_training_n=artifact.training_rows,
        bounds_method_version=calibration.bounds_method_version,
        calibrated_probability=calibration.calibrated_probability,
        calibrated_probability_lower_bound=calibration.lower_bound,
        calibrated_probability_upper_bound=calibration.upper_bound,
        money_lane_status=money_lane_status,
        model_provider_identity=PROP_PROVIDER_IDENTITY,
        model_family=artifact.model_family,
        model_artifact_version=bundle.model_artifact_version,
        model_artifact_checksum=bundle.artifact_checksum,
        model_bundle_fingerprint=bundle.bundle_fingerprint,
        model_artifact_lifecycle_state=bundle.lifecycle_state,
        feature_schema_version=bundle.feature_schema_version,
        feature_transform_version=bundle.feature_transform_version,
        feature_snapshot_hash=distribution.feature_snapshot_hash,
        training_dataset_hash=bundle.training_dataset_hash,
        training_code_sha=bundle.training_code_sha,
        specialist_version=bundle.specialist_version,
        certification_id=bundle.certification_id,
        distribution_type=distribution.distribution_type,
        probability_more=line_probs.probability_more,
        probability_less=line_probs.probability_less,
        push_probability=line_probs.push_probability,
    )
    return DiscretePropScoreResult(
        row=determine_publishability(row),
        inference=inference,
        line_probabilities=line_probs,
        calibration=calibration,
    )
