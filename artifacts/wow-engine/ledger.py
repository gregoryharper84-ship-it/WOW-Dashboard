"""
ledger.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.2

Thin wrapper around the Supabase client for wow_predictions /
wow_outcomes. Enforces, at the Python layer (in addition to the SQL
constraints in schema.sql), the rule that any incomplete/failed
component sets probability_publishable = false with no silent repair.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional
import os
import uuid

from calibration import CalibrationStatus

_RECOGNIZED_CALIBRATION_STATUSES = {
    CalibrationStatus.PRECALIBRATION_SHRINKAGE,
    CalibrationStatus.PLATT_TIME_SPLIT_V1,
    CalibrationStatus.ISOTONIC_V1,
}

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

    # The scoring run's "as of" time (engine.py's `scored_at`) -- when
    # this candidate was actually scored, distinct from event_start_time
    # (the game's start) and created_at (the DB row-insert time). Also
    # the candidate_as_of value used for market freshness and, for
    # Phase B/C rows, PREDICTIVE_BOUNDS_V1's historical-row eligibility
    # filter -- recording it here makes that filter's input auditable
    # after the fact, not just enforced at scoring time.
    model_timestamp: Optional[str] = None

    player: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None

    regime_model_version: Optional[str] = None
    regime_probabilities_json: Optional[dict] = None
    regime_probability_sum: Optional[float] = None
    primary_failure_path: Optional[str] = None
    failure_cause_tags: list[str] = field(default_factory=list)
    simulation_seed: Optional[int] = None
    simulation_draws: Optional[int] = None

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


def determine_publishability(row: PredictionRow) -> PredictionRow:
    """
    Confidence lane (probability_publishable) and money lane
    (money_lane_status) are evaluated SEPARATELY. Per the review that
    caught this: missing Goblin/Demon payout blocks the MONEY lane, it
    must not erase an otherwise-valid governed CONFIDENCE-lane
    probability. The two are combined only at the very end, when
    deriving probability_ceiling / terminal_ceiling — mirroring WOW's
    existing CONFIDENCE/MARKET/MONEY/SLIP lane-separation pattern.

    No silent repair: if any required confidence-lane component is
    missing/inconsistent, probability_publishable stays False and the
    gap is recorded, independent of money-lane status.
    """
    gaps = list(row.data_gaps)

    if row.raw_model_probability is None or not (0 < row.raw_model_probability < 1):
        gaps.append("raw_model_probability missing or out of (0,1) bounds")
    if not row.source_snapshot_id:
        gaps.append("source_snapshot_id missing or empty")
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

    # Confidence-lane ceiling: what the probability itself supports,
    # independent of money/payout state.
    if not row.probability_publishable:
        confidence_ceiling = "RESEARCH_INTEREST"
    elif row.calibration_status == "PRECALIBRATION_SHRINKAGE":
        confidence_ceiling = "MODEL_QUALIFIED_HOLD_PROHIBITED_PRECALIBRATION"
    else:
        confidence_ceiling = "MODEL_QUALIFIED_HOLD"

    # Terminal (SLIP-eligible) ceiling additionally requires the money
    # lane to be resolved before anything can reach a money-qualified
    # or final-approved label — but a resolved confidence-lane
    # probability is still recorded and usable for research/CONFIDENCE
    # reporting even while MONEY remains unresolved.
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
