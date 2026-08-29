"""Production registry loader for WOW_PROP_FITTED_MODEL_V1.

This module resolves immutable, prospectively certified model metadata from
Supabase. It deliberately does not invent inference parameters or turn registry
presence into probability publication. Concrete model-family adapters must be
registered explicitly and return a RawDiscreteDistribution under the governed
contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from prop_distribution_contract import (
    CertifiedBundle,
    PropDistributionContractError,
    PropInferenceRequest,
    RawDiscreteDistribution,
)

PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
CERTIFIED_LIFECYCLE_STATES = {"PROSPECTIVE_CERTIFIED", "CHAMPION"}


class PropFittedProviderUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedArtifact:
    artifact_id: str
    model_family: str
    artifact_format: str
    artifact_payload: Mapping[str, Any]
    training_rows: int
    validation_metrics: Mapping[str, Any]
    bundle: CertifiedBundle


@dataclass(frozen=True)
class CertifiedInference:
    """One raw inference plus the immutable artifact that produced it.

    Returning the artifact alongside the PMF lets the downstream calibration
    and persistence layers bind their work to the exact certified bundle
    without asking Supabase a second time or trusting caller-supplied version
    fields.
    """

    artifact: ResolvedArtifact
    distribution: RawDiscreteDistribution


Adapter = Callable[[ResolvedArtifact, PropInferenceRequest, Mapping[str, Any]], RawDiscreteDistribution]
_ADAPTERS: dict[str, Adapter] = {}


def register_model_family_adapter(model_family: str, adapter: Adapter) -> None:
    """Register one reviewed model-family adapter.

    Registration is code-controlled, not caller-controlled. Production should
    only register adapters shipped in the repository and covered by tests.
    """
    key = str(model_family or "").strip().upper()
    if not key:
        raise ValueError("model_family is required")
    _ADAPTERS[key] = adapter


def clear_model_family_adapters() -> None:
    """Test helper; production startup should not use this."""
    _ADAPTERS.clear()


def _rpc_payload(client: Any, sport: str, stat_type: str, feature_schema_version: str) -> Mapping[str, Any]:
    try:
        result = client.rpc(
            "wow_prop_certified_model_artifact",
            {
                "p_sport": sport,
                "p_stat_type": stat_type,
                "p_feature_schema_version": feature_schema_version,
            },
        ).execute()
    except Exception as exc:
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_REGISTRY_UNAVAILABLE",
            "Could not read the governed prop model registry.",
        ) from exc
    payload = result.data
    if not isinstance(payload, Mapping):
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_REGISTRY_INVALID_RESPONSE",
            "Governed prop model registry returned an invalid response.",
        )
    return payload


def resolve_certified_artifact(
    client: Any,
    *,
    sport: str,
    stat_type: str,
    feature_schema_version: str,
) -> Optional[ResolvedArtifact]:
    payload = _rpc_payload(client, sport, stat_type, feature_schema_version)
    if payload.get("ok") is not True:
        if payload.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND":
            return None
        raise PropFittedProviderUnavailable(
            str(payload.get("code") or "PROP_MODEL_REGISTRY_REJECTED"),
            "Governed prop model registry did not return a usable certified artifact.",
        )

    if payload.get("provider_identity") != PROVIDER_IDENTITY:
        raise PropFittedProviderUnavailable(
            "PROP_PROVIDER_IDENTITY_MISMATCH",
            "Resolved artifact does not belong to WOW_PROP_FITTED_MODEL_V1.",
        )
    lifecycle = str(payload.get("lifecycle_state") or "")
    if lifecycle not in CERTIFIED_LIFECYCLE_STATES:
        raise PropFittedProviderUnavailable(
            "PROP_BUNDLE_NOT_CERTIFIED",
            "Resolved artifact is not prospectively certified.",
        )
    artifact_payload = payload.get("artifact_payload")
    validation_metrics = payload.get("validation_metrics")
    if not isinstance(artifact_payload, Mapping) or not isinstance(validation_metrics, Mapping):
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID",
            "Certified artifact payload or validation metrics are invalid.",
        )

    try:
        bundle = CertifiedBundle(
            model_artifact_version=str(payload["model_artifact_version"]),
            calibrator_version=str(payload["calibrator_version"]),
            feature_transform_version=str(payload["feature_transform_version"]),
            specialist_version=str(payload["specialist_version"]),
            certification_id=str(payload["certification_id"]),
            feature_schema_version=str(payload["feature_schema_version"]),
            training_dataset_hash=str(payload["training_dataset_hash"]),
            training_code_sha=str(payload["training_code_sha"]),
            artifact_checksum=str(payload["artifact_checksum"]),
            lifecycle_state=lifecycle,
            supported_sport=str(payload["sport"]),
            supported_stat_type=str(payload["stat_type"]),
            supported_line_min=float(payload["supported_line_min"]),
            supported_line_max=float(payload["supported_line_max"]),
        )
        training_rows = int(payload["training_rows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_ARTIFACT_METADATA_INVALID",
            "Certified artifact metadata is incomplete or malformed.",
        ) from exc
    if training_rows <= 0 or not bundle.calibrator_version.strip():
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_ARTIFACT_METADATA_INVALID",
            "Certified artifact training_rows and calibrator_version must be valid.",
        )

    return ResolvedArtifact(
        artifact_id=str(payload["artifact_id"]),
        model_family=str(payload["model_family"]),
        artifact_format=str(payload["artifact_format"]),
        artifact_payload=artifact_payload,
        training_rows=training_rows,
        validation_metrics=validation_metrics,
        bundle=bundle,
    )


def infer_certified_distribution(
    client: Any,
    *,
    request: PropInferenceRequest,
    line: float,
    features: Mapping[str, Any],
) -> CertifiedInference:
    """Resolve one certified bundle and return its raw PMF with provenance.

    Absence of a certified artifact or reviewed adapter is a hard abstention.
    The provider never calibrates, publishes, persists, or executes.
    """
    artifact = resolve_certified_artifact(
        client,
        sport=request.sport,
        stat_type=request.stat_type,
        feature_schema_version=request.feature_schema_version,
    )
    if artifact is None:
        raise PropFittedProviderUnavailable(
            "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "No active prospectively certified prop artifact exists for this route.",
        )
    artifact.bundle.assert_compatible(request, line)
    adapter = _ADAPTERS.get(artifact.model_family.strip().upper())
    if adapter is None:
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
            f"No reviewed runtime adapter is registered for model family {artifact.model_family!r}.",
        )
    distribution = adapter(artifact, request, features)
    if not isinstance(distribution, RawDiscreteDistribution):
        raise PropDistributionContractError(
            "PROP_MODEL_ADAPTER_INVALID_OUTPUT",
            "model-family adapter must return RawDiscreteDistribution",
        )
    return CertifiedInference(artifact=artifact, distribution=distribution)


def infer_distribution(
    client: Any,
    *,
    request: PropInferenceRequest,
    line: float,
    features: Mapping[str, Any],
) -> RawDiscreteDistribution:
    """Compatibility wrapper returning only the raw provider distribution."""
    return infer_certified_distribution(
        client,
        request=request,
        line=line,
        features=features,
    ).distribution
