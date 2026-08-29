"""Fail-closed NCAAF fitted-model research inference.

This path proves training-serving wiring without claiming final Full Model publication.
Static calibration is applied to HOME probability only; AWAY is its complement so the
binary market remains normalized. Failure-path reconciliation and Dynamic Calibration
remain mandatory downstream gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

# Import registers the reviewed NCAAF_LOGISTIC_V1 adapter.
import ncaaf_logistic_adapter  # noqa: F401
from ncaaf_fitted_provider import (
    NCAAFFittedProviderUnavailable,
    NCAAFInferenceRequest,
    build_fitted_model_provider,
)
from ncaaf_static_calibrator import (
    NCAAFStaticCalibrationUnavailable,
    calibrate_from_registry,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
FEATURE_SCHEMA_VERSION = "NCAAF_FEATURES_V1"


class NCAAFResearchInferenceUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NCAAFResearchInferenceResult:
    official_event_id: str
    home_team: str
    away_team: str
    feature_as_of: str
    model_artifact_version: str
    calibrator_version: str
    raw_home_probability: float
    raw_away_probability: float
    static_calibrated_home_probability: float
    static_calibrated_away_probability: float
    static_home_lower_diagnostic: float
    static_home_upper_diagnostic: float
    static_away_lower_diagnostic: float
    static_away_upper_diagnostic: float
    calibration_health_status: str
    dynamic_calibration_status: str = "NOT_ATTEMPTED"
    failure_path_status: str = "NOT_ATTEMPTED"
    probability_publishable: bool = False
    can_execute: bool = False


def _feature_snapshot(client: Any, *, official_event_id: str, home_team: str, away_team: str) -> Mapping[str, Any]:
    try:
        result = client.rpc(
            "wow_ncaaf_latest_event_features",
            {
                "p_official_event_id": official_event_id,
                "p_feature_schema_version": FEATURE_SCHEMA_VERSION,
                "p_home_team": home_team,
                "p_away_team": away_team,
            },
        ).execute()
    except Exception as exc:
        raise NCAAFResearchInferenceUnavailable("NCAAF_EVENT_FEATURE_REGISTRY_UNAVAILABLE", "feature snapshot lookup failed") from exc
    payload = getattr(result, "data", None)
    if not isinstance(payload, Mapping):
        raise NCAAFResearchInferenceUnavailable("NCAAF_EVENT_FEATURE_REGISTRY_INVALID_RESPONSE", "feature snapshot response invalid")
    if payload.get("ok") is not True:
        raise NCAAFResearchInferenceUnavailable(str(payload.get("code") or "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND"), "feature snapshot unavailable")
    if payload.get("can_execute") is not False:
        raise NCAAFResearchInferenceUnavailable("NCAAF_FEATURE_EXECUTION_FLAG_INVALID", "feature snapshot can_execute must be false")
    return payload


def run_research_inference(client: Any, *, official_event_id: str, home_team: str, away_team: str) -> NCAAFResearchInferenceResult:
    if not official_event_id or not home_team or not away_team or home_team == away_team:
        raise NCAAFResearchInferenceUnavailable("NCAAF_EVENT_IDENTITY_INVALID", "exact event/home/away identity required")
    features = _feature_snapshot(client, official_event_id=official_event_id, home_team=home_team, away_team=away_team)
    feature_as_of = str(features.get("feature_as_of") or "")
    request = NCAAFInferenceRequest(
        official_event_id=official_event_id,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_as_of=feature_as_of,
        home_team=home_team,
        away_team=away_team,
    )
    try:
        provider = build_fitted_model_provider(client, feature_schema_version=FEATURE_SCHEMA_VERSION)
        raw = provider(request, features)
    except NCAAFFittedProviderUnavailable as exc:
        raise NCAAFResearchInferenceUnavailable(exc.code, str(exc)) from exc
    try:
        static = calibrate_from_registry(
            client,
            model_artifact_version=raw.model_artifact_version,
            raw_probability=raw.home_probability,
        )
    except NCAAFStaticCalibrationUnavailable as exc:
        raise NCAAFResearchInferenceUnavailable(exc.code, str(exc)) from exc

    home = float(static.calibrated_probability)
    away = 1.0 - home
    if not all(isfinite(v) and 0.0 < v < 1.0 for v in (home, away)) or abs(home + away - 1.0) > 1e-12:
        raise NCAAFResearchInferenceUnavailable("NCAAF_STATIC_CALIBRATED_OUTPUT_INVALID", "calibrated binary probabilities invalid")
    home_low = float(static.calibration_lower_diagnostic)
    home_high = float(static.calibration_upper_diagnostic)
    away_low = 1.0 - home_high
    away_high = 1.0 - home_low
    return NCAAFResearchInferenceResult(
        official_event_id=official_event_id,
        home_team=home_team,
        away_team=away_team,
        feature_as_of=feature_as_of,
        model_artifact_version=raw.model_artifact_version,
        calibrator_version=static.calibrator_version,
        raw_home_probability=float(raw.home_probability),
        raw_away_probability=float(raw.away_probability),
        static_calibrated_home_probability=home,
        static_calibrated_away_probability=away,
        static_home_lower_diagnostic=home_low,
        static_home_upper_diagnostic=home_high,
        static_away_lower_diagnostic=away_low,
        static_away_upper_diagnostic=away_high,
        calibration_health_status=static.calibration_health_status,
    )
