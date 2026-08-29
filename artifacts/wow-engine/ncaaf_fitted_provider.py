"""Governed loader for WOW_NCAAF_FITTED_MODEL_V1.

The provider resolves only prospectively certified artifacts from Supabase.
It does not train, calibrate, publish, place wagers, or infer when the artifact
or reviewed model-family adapter is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping, Optional

PROVIDER_IDENTITY = "WOW_NCAAF_FITTED_MODEL_V1"
CERTIFIED_LIFECYCLE_STATES = {"PROSPECTIVE_CERTIFIED", "CHAMPION"}
CAN_EXECUTE = False


class NCAAFFittedProviderUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NCAAFInferenceRequest:
    official_event_id: str
    feature_schema_version: str
    feature_as_of: str
    home_team: str
    away_team: str


@dataclass(frozen=True)
class ResolvedNCAAFArtifact:
    artifact_id: str
    model_family: str
    artifact_format: str
    artifact_payload: Mapping[str, Any]
    model_artifact_version: str
    feature_schema_version: str
    feature_transform_version: str
    specialist_version: str
    certification_id: str
    lifecycle_state: str
    training_dataset_hash: str
    training_code_sha: str
    artifact_checksum: str
    training_rows: int
    validation_metrics: Mapping[str, Any]
    calibration_method: str
    calibrator_version: str
    calibration_training_n: int


@dataclass(frozen=True)
class RawNCAAFWinProbability:
    home_probability: float
    away_probability: float
    model_artifact_version: str
    artifact_id: str

    def validate(self) -> None:
        values = (self.home_probability, self.away_probability)
        if any(not isfinite(float(v)) or not (0.0 < float(v) < 1.0) for v in values):
            raise NCAAFFittedProviderUnavailable(
                "NCAAF_MODEL_OUTPUT_INVALID",
                "NCAAF model probabilities must be finite and strictly inside (0,1).",
            )
        if abs(float(self.home_probability) + float(self.away_probability) - 1.0) > 1e-9:
            raise NCAAFFittedProviderUnavailable(
                "NCAAF_MODEL_OUTPUT_NOT_NORMALIZED",
                "NCAAF home/away probabilities must sum to 1.",
            )


Adapter = Callable[[ResolvedNCAAFArtifact, NCAAFInferenceRequest, Mapping[str, Any]], RawNCAAFWinProbability]
_ADAPTERS: dict[str, Adapter] = {}


def register_model_family_adapter(model_family: str, adapter: Adapter) -> None:
    key = str(model_family or "").strip().upper()
    if not key:
        raise ValueError("model_family is required")
    _ADAPTERS[key] = adapter


def clear_model_family_adapters() -> None:
    _ADAPTERS.clear()


def resolve_certified_artifact(client: Any, *, feature_schema_version: str) -> Optional[ResolvedNCAAFArtifact]:
    try:
        result = client.rpc(
            "wow_ncaaf_certified_model_artifact",
            {"p_feature_schema_version": feature_schema_version},
        ).execute()
    except Exception as exc:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_REGISTRY_UNAVAILABLE",
            "Could not read the governed NCAAF model registry.",
        ) from exc

    payload = result.data
    if not isinstance(payload, Mapping):
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_REGISTRY_INVALID_RESPONSE",
            "Governed NCAAF model registry returned an invalid response.",
        )
    if payload.get("ok") is not True:
        if payload.get("code") == "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND":
            return None
        raise NCAAFFittedProviderUnavailable(
            str(payload.get("code") or "NCAAF_MODEL_REGISTRY_REJECTED"),
            "Governed NCAAF model registry rejected the route.",
        )
    if payload.get("provider_identity") != PROVIDER_IDENTITY:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_PROVIDER_IDENTITY_MISMATCH",
            "Resolved artifact does not belong to WOW_NCAAF_FITTED_MODEL_V1.",
        )
    lifecycle = str(payload.get("lifecycle_state") or "")
    if lifecycle not in CERTIFIED_LIFECYCLE_STATES:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_BUNDLE_NOT_CERTIFIED",
            "Resolved NCAAF artifact is not prospectively certified.",
        )
    if payload.get("probability_publishable") is not True:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_ARTIFACT_NOT_PUBLISHABLE",
            "Certified registry row has not cleared probability publication governance.",
        )

    artifact_payload = payload.get("artifact_payload")
    validation_metrics = payload.get("validation_metrics")
    if not isinstance(artifact_payload, Mapping) or not isinstance(validation_metrics, Mapping):
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_ARTIFACT_PAYLOAD_INVALID",
            "Certified NCAAF artifact payload or validation metrics are invalid.",
        )

    try:
        resolved = ResolvedNCAAFArtifact(
            artifact_id=str(payload["artifact_id"]),
            model_family=str(payload["model_family"]),
            artifact_format=str(payload["artifact_format"]),
            artifact_payload=artifact_payload,
            model_artifact_version=str(payload["model_artifact_version"]),
            feature_schema_version=str(payload["feature_schema_version"]),
            feature_transform_version=str(payload["feature_transform_version"]),
            specialist_version=str(payload["specialist_version"]),
            certification_id=str(payload["certification_id"]),
            lifecycle_state=lifecycle,
            training_dataset_hash=str(payload["training_dataset_hash"]),
            training_code_sha=str(payload["training_code_sha"]),
            artifact_checksum=str(payload["artifact_checksum"]),
            training_rows=int(payload["training_rows"]),
            validation_metrics=validation_metrics,
            calibration_method=str(payload["calibration_method"]),
            calibrator_version=str(payload["calibrator_version"]),
            calibration_training_n=int(payload["calibration_training_n"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_ARTIFACT_METADATA_INVALID",
            "Certified NCAAF artifact metadata is incomplete or malformed.",
        ) from exc

    if resolved.training_rows <= 0 or resolved.calibration_training_n <= 0:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_ARTIFACT_METADATA_INVALID",
            "Training and calibration sample sizes must be positive.",
        )
    if resolved.feature_schema_version != feature_schema_version:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_FEATURE_SCHEMA_MISMATCH",
            "Resolved NCAAF artifact does not match requested feature schema.",
        )
    return resolved


def infer_raw_probability(
    client: Any,
    *,
    request: NCAAFInferenceRequest,
    features: Mapping[str, Any],
) -> RawNCAAFWinProbability:
    artifact = resolve_certified_artifact(
        client,
        feature_schema_version=request.feature_schema_version,
    )
    if artifact is None:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "No active certified NCAAF fitted-model artifact exists for this feature schema.",
        )
    adapter = _ADAPTERS.get(artifact.model_family.strip().upper())
    if adapter is None:
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
            f"No reviewed runtime adapter is registered for model family {artifact.model_family!r}.",
        )
    result = adapter(artifact, request, features)
    if not isinstance(result, RawNCAAFWinProbability):
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_ADAPTER_INVALID_OUTPUT",
            "NCAAF model-family adapter must return RawNCAAFWinProbability.",
        )
    result.validate()
    return result
