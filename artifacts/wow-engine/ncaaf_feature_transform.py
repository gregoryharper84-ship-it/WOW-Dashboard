"""Shared deterministic NCAAF_FEATURES_V1 -> fitted-model vector transform.

Training loaders and live inference must use this exact mapping to prevent
training-serving skew. Market prior fields are intentionally excluded.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

from ncaaf_trainer import FEATURES

CAN_EXECUTE = False


class NCAAFFeatureTransformError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _num(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NCAAFFeatureTransformError("NCAAF_FEATURE_TRANSFORM_INPUT_INVALID", key)
    out = float(value)
    if not isfinite(out):
        raise NCAAFFeatureTransformError("NCAAF_FEATURE_TRANSFORM_INPUT_INVALID", key)
    return out


def _delta(row: Mapping[str, Any], stem: str) -> float:
    return _num(row, f"home_{stem}") - _num(row, f"away_{stem}")


def model_features_from_snapshot(row: Mapping[str, Any]) -> dict[str, float]:
    out = {
        "power_delta": _delta(row, "power_rating"),
        "off_epa_delta": _delta(row, "off_epa"),
        "def_epa_delta": _delta(row, "def_epa"),
        "success_rate_delta": _delta(row, "success_rate"),
        "explosiveness_delta": _delta(row, "explosiveness"),
        "qb_value_delta": _delta(row, "qb_value"),
        "qb_certainty_delta": _delta(row, "qb_certainty"),
        "ol_health_delta": _delta(row, "ol_health"),
        "def_front_health_delta": _delta(row, "def_front_health"),
        "skill_availability_delta": _delta(row, "skill_availability"),
        "rest_days_delta": _delta(row, "rest_days"),
        "tempo_delta": _delta(row, "tempo"),
        "turnover_volatility_delta": _delta(row, "turnover_volatility"),
        "special_teams_delta": _num(row, "home_special_teams_rating") - _num(row, "away_special_teams_rating"),
        "travel_distance_miles": _num(row, "travel_distance_miles"),
        "weather_wind_mph": _num(row, "weather_wind_mph"),
        "weather_precip_probability": _num(row, "weather_precip_probability"),
        "neutral_site": 1.0 if row.get("neutral_site") is True else 0.0,
    }
    if tuple(out.keys()) != FEATURES:
        raise NCAAFFeatureTransformError("NCAAF_FEATURE_TRANSFORM_SCHEMA_DRIFT", "transform order does not match trainer FEATURES")
    return out
