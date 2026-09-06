from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, stdev
from typing import Iterable

from .probability_core import CalibrationProfile


@dataclass(frozen=True)
class ForecastResidual:
    forecast_value_f: float
    settled_value_f: float
    forecast_as_of: str
    settlement_time: str

    @property
    def residual_f(self) -> float:
        return float(self.forecast_value_f) - float(self.settled_value_f)


@dataclass(frozen=True)
class CalibrationFitResult:
    status: str
    profile: CalibrationProfile | None
    sample_n: int
    blockers: tuple[str, ...]
    can_publish: bool = False
    can_execute: bool = False


def fit_candidate_calibration_profile(
    residual_rows: Iterable[ForecastResidual],
    *,
    station_id: str,
    lane: str,
    lead_time_bucket: str,
    minimum_n: int = 30,
) -> CalibrationFitResult:
    """Fit a research candidate station/horizon residual profile.

    This function never certifies a profile. Certification requires separate
    chronological replay/forward validation and governance. It exists to
    generate candidate parameters from immutable pre-settlement residuals.
    """
    rows = tuple(residual_rows)
    blockers: list[str] = []
    if len(rows) < minimum_n:
        blockers.append("CALIBRATION_SAMPLE_INSUFFICIENT")
    if not station_id.strip():
        blockers.append("CALIBRATION_STATION_ID_MISSING")
    if not lane.strip():
        blockers.append("CALIBRATION_LANE_MISSING")
    if not lead_time_bucket.strip():
        blockers.append("CALIBRATION_LEAD_TIME_BUCKET_MISSING")

    valid_residuals: list[float] = []
    for row in rows:
        if not row.forecast_as_of or not row.settlement_time:
            blockers.append("CALIBRATION_TEMPORAL_PROVENANCE_MISSING")
            continue
        # ISO strings are required upstream. Lexical comparison is valid only
        # for normalized UTC timestamps; upstream acceptance must enforce UTC.
        if not (row.forecast_as_of.endswith("Z") and row.settlement_time.endswith("Z")):
            blockers.append("CALIBRATION_TIMESTAMP_NOT_NORMALIZED_UTC")
            continue
        if row.forecast_as_of >= row.settlement_time:
            blockers.append("CALIBRATION_LOOKAHEAD_LEAKAGE")
            continue
        valid_residuals.append(row.residual_f)

    blockers = list(dict.fromkeys(blockers))
    if blockers:
        return CalibrationFitResult(
            status="CALIBRATION_CANDIDATE_HELD",
            profile=None,
            sample_n=len(valid_residuals),
            blockers=tuple(blockers),
        )

    bias = mean(valid_residuals)
    sigma = stdev(valid_residuals)
    if sigma <= 0:
        return CalibrationFitResult(
            status="CALIBRATION_CANDIDATE_HELD",
            profile=None,
            sample_n=len(valid_residuals),
            blockers=("CALIBRATION_RESIDUAL_VARIANCE_ZERO",),
        )

    # These form a conservative sensitivity envelope, not a confidence
    # interval. The candidate remains uncertified until replay/forward tests.
    lower_sigma = max(0.25, sigma * 0.85)
    upper_sigma = sigma * 1.20
    profile = CalibrationProfile(
        station_id=station_id.strip(),
        lane=lane.strip().upper(),
        lead_time_bucket=lead_time_bucket.strip().upper(),
        bias_f=bias,
        sigma_f=sigma,
        lower_sigma_f=lower_sigma,
        upper_sigma_f=upper_sigma,
        sample_n=len(valid_residuals),
        method="STATION_HORIZON_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
        certified=False,
    )
    return CalibrationFitResult(
        status="CALIBRATION_CANDIDATE_FIT_RESEARCH_ONLY",
        profile=profile,
        sample_n=len(valid_residuals),
        blockers=(),
        can_publish=False,
        can_execute=False,
    )
