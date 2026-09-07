"""Compatibility adapter from V17 weather probability package to existing downstream market gates.

The adapter never changes the model probability. It only exposes completed model
state in the legacy candidate shape so market/edge/portfolio checks can run later.
"""
from __future__ import annotations

from typing import Any


def package_to_legacy_candidate(package: dict[str, Any], candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(candidate or {})
    pmf = package.get("final_high_pmf") or {}
    pmf_sum = sum(float(v) for v in pmf.values()) if pmf else 0.0
    completed = package.get("probability_status") == "COMPLETED"
    calibrated = package.get("calibration_status") == "CALIBRATED"
    components = package.get("component_models") or []
    sigma_values = [float(c["sigma_f"]) for c in components if c.get("sigma_f") is not None]
    out.update({
        "weather_v17_probability_package": package,
        "model_probability": package.get("calibrated_probability") if calibrated else package.get("raw_probability"),
        "raw_probability": package.get("raw_probability"),
        "calibrated_probability": package.get("calibrated_probability"),
        "calibrated_prob_lower_bound": package.get("calibrated_lower_bound"),
        "calibrated_prob_upper_bound": package.get("calibrated_upper_bound"),
        "probability_normalization_pass": completed and abs(pmf_sum - 1.0) <= 1e-6,
        "settlement_station_verified": completed and bool(package.get("station_id")),
        "confidence_tier": "WEATHER_MODEL_READY" if calibrated else "WEATHER_WATCH",
        "sigma_f": max(sigma_values) if sigma_values else None,
        "weather_distribution_method": package.get("distribution_method"),
        "weather_probability_status": package.get("probability_status"),
        "weather_model_status": package.get("model_status"),
        "weather_model_blockers": package.get("blockers") or [],
    })
    return out


def assert_probability_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Portfolio/market layers may not mutate a completed weather probability package."""
    for key in ("raw_probability", "calibrated_probability", "calibrated_lower_bound", "calibrated_upper_bound"):
        if before.get(key) != after.get(key):
            raise ValueError(f"WEATHER_PROBABILITY_MUTATED_DOWNSTREAM:{key}")
