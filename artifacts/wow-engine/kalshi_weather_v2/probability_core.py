from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt

from .models import ContractSnapshot, ProbabilityPackage, WeatherEvidenceSnapshot


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    return 0.5 * (1.0 + erf((x - mu) / (sigma * sqrt(2.0))))


@dataclass(frozen=True)
class CalibrationProfile:
    station_id: str
    lane: str
    lead_time_bucket: str
    bias_f: float
    sigma_f: float
    lower_sigma_f: float
    upper_sigma_f: float
    sample_n: int
    method: str
    certified: bool = False


class WeatherProbabilityCore:
    """Deterministic probability math for exact Kalshi temperature contracts.

    Market prices are intentionally absent from this interface.
    """

    def build(
        self,
        *,
        contract: ContractSnapshot,
        evidence: WeatherEvidenceSnapshot,
        calibration: CalibrationProfile,
    ) -> ProbabilityPackage:
        if evidence.central_estimate is None:
            return self._missing("CENTRAL_ESTIMATE_MISSING")
        if contract.threshold_lower is None and contract.threshold_upper is None:
            return self._missing("CONTRACT_THRESHOLD_MISSING")
        if calibration.sigma_f <= 0 or calibration.lower_sigma_f <= 0 or calibration.upper_sigma_f <= 0:
            return self._missing("CALIBRATION_SIGMA_INVALID")

        mu = float(evidence.central_estimate) - float(calibration.bias_f)
        p_yes = self._event_probability(contract, mu, calibration.sigma_f)
        p_low = self._event_probability(contract, mu, calibration.upper_sigma_f)
        p_high = self._event_probability(contract, mu, calibration.lower_sigma_f)
        lower = min(p_low, p_high, p_yes)
        upper = max(p_low, p_high, p_yes)

        # Same-day maximum contracts cannot settle below an already-observed
        # official maximum. Apply only when the evidence represents a maximum.
        if (
            contract.lane == "DAILY_HIGH_TEMPERATURE"
            and evidence.observed_extreme_so_far is not None
            and contract.threshold_upper is not None
            and evidence.observed_extreme_so_far > contract.threshold_upper
        ):
            p_yes = 0.0
            lower = 0.0
            upper = 0.0

        p_yes = min(1.0, max(0.0, p_yes))
        lower = min(p_yes, max(0.0, lower))
        upper = max(p_yes, min(1.0, upper))
        p_no = 1.0 - p_yes

        return ProbabilityPackage(
            p_yes=p_yes,
            p_no=p_no,
            central_estimate=mu,
            lower_bound_yes=lower,
            upper_bound_yes=upper,
            threshold_distance=self._threshold_distance(contract, mu, calibration.sigma_f),
            calibration_method=calibration.method,
            probability_source="KALSHI_WEATHER_V2_STATION_HORIZON_MODEL",
            market_price_used_as_input=False,
            coherent=abs((p_yes + p_no) - 1.0) <= 1e-9,
            calibrated=bool(calibration.certified and calibration.sample_n > 0),
        )

    @staticmethod
    def _event_probability(contract: ContractSnapshot, mu: float, sigma: float) -> float:
        # Integer-temperature contracts use continuity correction. If live
        # rules specify another rounding convention, the contract parser must
        # encode equivalent numeric bounds before invoking this core.
        lower = contract.threshold_lower
        upper = contract.threshold_upper

        if lower is None:
            assert upper is not None
            boundary = upper + (0.5 if contract.upper_inclusive else -0.5)
            return _normal_cdf(boundary, mu, sigma)
        if upper is None:
            boundary = lower - (0.5 if contract.lower_inclusive else -0.5)
            return 1.0 - _normal_cdf(boundary, mu, sigma)

        lo = lower - (0.5 if contract.lower_inclusive else -0.5)
        hi = upper + (0.5 if contract.upper_inclusive else -0.5)
        if hi < lo:
            raise ValueError("upper threshold is below lower threshold")
        return _normal_cdf(hi, mu, sigma) - _normal_cdf(lo, mu, sigma)

    @staticmethod
    def _threshold_distance(contract: ContractSnapshot, mu: float, sigma: float) -> str:
        distances = []
        if contract.threshold_lower is not None:
            distances.append(abs(mu - contract.threshold_lower) / sigma)
        if contract.threshold_upper is not None:
            distances.append(abs(mu - contract.threshold_upper) / sigma)
        z = min(distances) if distances else 0.0
        if z < 0.35:
            return "BOUNDARY_ROUNDING_SENSITIVE"
        if z < 0.8:
            return "NEAR_THRESHOLD"
        if z < 1.5:
            return "MODERATELY_INSIDE_OR_OUTSIDE"
        return "COMFORTABLY_SEPARATED"

    @staticmethod
    def _missing(reason: str) -> ProbabilityPackage:
        return ProbabilityPackage(
            p_yes=None,
            p_no=None,
            central_estimate=None,
            lower_bound_yes=None,
            upper_bound_yes=None,
            threshold_distance=None,
            calibration_method=None,
            probability_source=reason,
            market_price_used_as_input=False,
            coherent=False,
            calibrated=False,
        )
