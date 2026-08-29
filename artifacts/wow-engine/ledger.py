"""
ledger.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.2

Thin wrapper around the Supabase client for wow_predictions /
wow_outcomes. Enforces, at the Python layer (in addition to the SQL
constraints in schema.sql), the rule that any incomplete/failed
component sets probability_publishable = false with no silent repair.

Generic player props use the direction-free WOW_PROP_FITTED_MODEL_V1 discrete
PMF contract. They are validated against explicit model/artifact/distribution
provenance rather than the legacy pitcher-regime/simulation fields.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
from math import isfinite
from typing import Optional
import os
import uuid

from calibration import CalibrationStatus

_RECOGNIZED_CALIBRATION_STATUSES = {
    CalibrationStatus.PRECALIBRATION_SHRINKAGE,
    CalibrationStatus.PLATT_TIME_SPLIT_V1,
    CalibrationStatus.ISOTONIC_V1,
}

_PROP_DISCRETE_MARKET_TYPE = "PROP_DISCRETE_PMF"
_PROP_PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
_PROP_CERTIFIED_STATES = {"PROSPECTIVE_CERTIFIED", "CHAMPION"}


def _valid_iso_timestamp(ts) -> bool:
    """A governed timestamp must represent an absolute instant."""
    if not isinstance(ts, str) or not ts:
        return False
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _nonempty_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


try:
    from supabase import create_client, Client  # type: ignore
except ImportError:  # pragma: no cover - allows import without the dep for testing
    create_client = None
    Client = None


def get_client() -> "Client":
    if create_client is None:
        raise RuntimeError("supabase-py is not installed. `pip install supabase`")
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


@dataclass
class PredictionRow:
    event_id: str
    event_start_time: str
    sport: str
    market_type: str
    stat_type: str
    line: float
    direction: str
    source_snapshot_id: str

    model_timestamp: Optional[str] = None

    player: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None

    # Legacy regime/simulation provenance. Required for legacy fitted-pitcher
    # rows, but not for PROP_DISCRETE_PMF rows.
    regime_model_version: Optional[str] = None
    regime_probabilities_json: Optional[dict] = None
    regime_probability_sum: Optional[float] = None
    primary_failure_path: Optional[str] = None
    failure_cause_tags: list[str] = field(default_factory=list)
    simulation_seed: Optional[int] = None
    simulation_draws: Optional[int] = None

    # Generic discrete-prop model provenance.
    model_provider_identity: Optional[str] = None
    model_family: Optional[str] = None
    model_artifact_version: Optional[str] = None
    model_artifact_checksum: Optional[str] = None
    model_bundle_fingerprint: Optional[str] = None
    model_artifact_lifecycle_state: Optional[str] = None
    feature_schema_version: Optional[str] = None
    feature_transform_version: Optional[str] = None
    feature_snapshot_hash: Optional[str] = None
    training_dataset_hash: Optional[str] = None
    training_code_sha: Optional[str] = None
    specialist_version: Optional[str] = None
    certification_id: Optional[str] = None
    distribution_type: Optional[str] = None
    probability_more: Optional[float] = None
    probability_less: Optional[float] = None
    push_probability: Optional[float] = None

    raw_model_probability: Optional[float] = None
    independent_model_probability: Optional[float] = None
    effective_sample_size: Optional[float] = None

    market_prior_available: bool = False
    market_prior_probability: Optional[float] = None
    market_prior_quality: Optional[str] = None
    market_prior_weight: float = 0.0
    market_prior_weight_source: Optional[str] = None
    reference_market_probability_raw: Optional[float] = None
    reference_market_side: Optional[str] = None
    reference_market_price: Optional[float] = None
    market_timestamp: Optional[str] = None

    calibration_status: Optional[str] = None
    calibration_method: Optional[str] = None
    calibration_version: Optional[str] = None
    calibration_training_n: Optional[int] = None
    calibration_parent_cohort: Optional[str] = None
    bounds_method_version: Optional[str] = None

    calibrated_probability: Optional[float] = None
    calibrated_probability_lower_bound: Optional[float] = None
    calibrated_probability_upper_bound: Optional[float] = None

    probability_publishable: bool = False
    probability_ceiling: Optional[str] = None
    money_lane_status: str = "PAYOUT_UNRESOLVED"
    data_gaps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def _validate_discrete_prop_provenance(row: PredictionRow, gaps: list[str]) -> None:
    required_text = {
        "model_provider_identity": row.model_provider_identity,
        "model_family": row.model_family,
        "model_artifact_version": row.model_artifact_version,
        "model_artifact_checksum": row.model_artifact_checksum,
        "model_bundle_fingerprint": row.model_bundle_fingerprint,
        "feature_schema_version": row.feature_schema_version,
        "feature_transform_version": row.feature_transform_version,
        "feature_snapshot_hash": row.feature_snapshot_hash,
        "training_dataset_hash": row.training_dataset_hash,
        "training_code_sha": row.training_code_sha,
        "specialist_version": row.specialist_version,
        "certification_id": row.certification_id,
        "distribution_type": row.distribution_type,
    }
    for name, value in required_text.items():
        if not _nonempty_text(value):
            gaps.append(f"{name} missing or empty")

    if row.model_provider_identity != _PROP_PROVIDER_IDENTITY:
        gaps.append("model_provider_identity is not WOW_PROP_FITTED_MODEL_V1")
    if row.model_artifact_lifecycle_state not in _PROP_CERTIFIED_STATES:
        gaps.append("model_artifact_lifecycle_state is not prospectively certified/champion")
    if row.distribution_type != "DISCRETE_PMF":
        gaps.append("distribution_type must be DISCRETE_PMF")
    if row.effective_sample_size is None or not isfinite(float(row.effective_sample_size)) or row.effective_sample_size <= 0:
        gaps.append("effective_sample_size missing, non-finite, or non-positive")

    probs = (row.probability_more, row.probability_less, row.push_probability)
    if any(v is None or not isfinite(float(v)) or not (0.0 <= float(v) <= 1.0) for v in probs):
        gaps.append("MORE/LESS/PUSH probabilities missing or outside [0,1]")
        return
    total = sum(float(v) for v in probs)
    if abs(total - 1.0) > 1e-9:
        gaps.append("MORE/LESS/PUSH probabilities do not normalize to 1")
    directional = row.probability_more if row.direction == "MORE" else row.probability_less if row.direction == "LESS" else None
    if directional is None:
        gaps.append("direction must be MORE or LESS")
    elif row.raw_model_probability is not None and abs(float(directional) - float(row.raw_model_probability)) > 1e-9:
        gaps.append("raw_model_probability does not match selected side of discrete PMF")


def determine_publishability(row: PredictionRow) -> PredictionRow:
    """Evaluate confidence publication separately from the money lane.

    Missing confidence/model evidence keeps probability_publishable false.
    Missing payout/price evidence lowers only the downstream money ceiling.
    Generic discrete prop rows are never forced through pitcher-regime or
    simulation-count requirements; they must instead prove certified model
    provenance, normalized MORE/LESS/PUSH outcomes, positive ESS, calibration,
    and numerical bounds.
    """
    gaps = list(row.data_gaps)

    if row.raw_model_probability is None or not (0 < row.raw_model_probability < 1):
        gaps.append("raw_model_probability missing or out of (0,1) bounds")
    if not row.source_snapshot_id:
        gaps.append("source_snapshot_id missing or empty")
    if not _valid_iso_timestamp(row.model_timestamp):
        gaps.append("model_timestamp missing or not a valid ISO 8601 timestamp (no auditable scoring time)")

    if row.market_type == _PROP_DISCRETE_MARKET_TYPE:
        _validate_discrete_prop_provenance(row, gaps)
    else:
        if row.regime_probability_sum is None or abs(row.regime_probability_sum - 1.0) > 1e-6:
            gaps.append("regime_probability_sum invalid or missing")
        if row.simulation_draws is None or row.simulation_draws < 50_000:
            gaps.append("simulation_draws below 50,000 minimum")

    if row.calibrated_probability is None:
        gaps.append("calibrated_probability not produced")
    else:
        lb, ub = row.calibrated_probability_lower_bound, row.calibrated_probability_upper_bound
        if lb is None or ub is None or not (0 < lb <= row.calibrated_probability <= ub < 1):
            gaps.append("calibrated_probability bounds invalid")
        if row.calibration_status not in _RECOGNIZED_CALIBRATION_STATUSES:
            gaps.append(f"calibration_status not recognized: {row.calibration_status!r}")

    row.data_gaps = gaps
    row.probability_publishable = (len(gaps) == 0)

    money_resolved = (row.money_lane_status == "RESOLVED")
    if not money_resolved and "money_lane_status != RESOLVED (payout unresolved)" not in row.blockers:
        row.blockers = list(row.blockers) + ["money_lane_status != RESOLVED (payout unresolved)"]

    if not row.probability_publishable:
        confidence_ceiling = "RESEARCH_INTEREST"
    elif row.calibration_status == "PRECALIBRATION_SHRINKAGE":
        confidence_ceiling = "MODEL_QUALIFIED_HOLD_PROHIBITED_PRECALIBRATION"
    else:
        confidence_ceiling = "MODEL_QUALIFIED_HOLD"

    if confidence_ceiling == "RESEARCH_INTEREST":
        row.probability_ceiling = "RESEARCH_INTEREST"
    elif not money_resolved:
        row.probability_ceiling = confidence_ceiling + "_MONEY_LANE_UNRESOLVED"
    else:
        row.probability_ceiling = confidence_ceiling

    return row


def insert_prediction(row: PredictionRow) -> dict:
    row = determine_publishability(row)
    client = get_client()
    payload = asdict(row)
    payload["prediction_id"] = str(uuid.uuid4())
    result = client.table("wow_predictions").insert(payload).execute()
    return result.data[0] if result.data else payload


def record_outcome(prediction_id: str, **outcome_fields) -> dict:
    client = get_client()
    payload = {"prediction_id": prediction_id, **outcome_fields}
    result = client.table("wow_outcomes").insert(payload).execute()
    return result.data[0] if result.data else payload
