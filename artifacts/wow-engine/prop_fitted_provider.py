"""Production registry loader for WOW_PROP_FITTED_MODEL_V1.

This module resolves immutable, prospectively certified model metadata from
Supabase. It deliberately does not invent inference parameters or turn registry
presence into probability publication. Concrete model-family adapters must be
registered explicitly and return a RawDiscreteDistribution under the governed
contract.

V17 exact prop capability identity is:
    sport + league + market_family + stat_type + period

feature_schema_version remains immutable artifact compatibility metadata; it is
not part of the capability identity.
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
DEFAULT_MARKET_FAMILY = "PLAYER_PROP"


class PropFittedProviderUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PropCapabilityKey:
    sport: str
    league: str
    market_family: str
    stat_type: str
    period: str


@dataclass(frozen=True)
class ResolvedArtifact:
    artifact_id: str
    model_family: str
    artifact_format: str
    artifact_payload: Mapping[str, Any]
    training_rows: int
    validation_metrics: Mapping[str, Any]
    bundle: CertifiedBundle
    capability: PropCapabilityKey


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


def _normalize(value: Any) -> str:
    return str(value or "").strip().upper()


def canonical_prop_period(stat_type: str, period: Optional[str] = None) -> str:
    explicit = _normalize(period)
    if explicit:
        return explicit
    upper = _normalize(stat_type)
    return "FIRST_INNING" if "1IP" in upper or "FIRST_INNING" in upper else "FULL_GAME"


def capability_key(
    *,
    sport: str,
    stat_type: str,
    league: Optional[str] = None,
    market_family: Optional[str] = None,
    period: Optional[str] = None,
) -> PropCapabilityKey:
    """Build the normalized, server-owned V17 exact capability tuple.

    Existing player-prop request contracts do not yet carry a separate league,
    market-family, or period field. For those callers, league defaults to sport,
    market_family defaults to PLAYER_PROP, and period is deterministically
    derived from stat identity. No caller-controlled model metadata is used.
    """

    normalized_sport = _normalize(sport)
    normalized_league = _normalize(league) or normalized_sport
    normalized_market_family = _normalize(market_family) or DEFAULT_MARKET_FAMILY
    normalized_stat_type = _normalize(stat_type)
    normalized_period = canonical_prop_period(normalized_stat_type, period)

    missing = [
        name
        for name, value in (
            ("sport", normalized_sport),
            ("league", normalized_league),
            ("market_family", normalized_market_family),
            ("stat_type", normalized_stat_type),
            ("period", normalized_period),
        )
        if not value
    ]
    if missing:
        raise PropFittedProviderUnavailable(
            "PROP_CAPABILITY_IDENTITY_INCOMPLETE",
            f"Exact prop capability identity is incomplete: {missing}",
        )
    return PropCapabilityKey(
        sport=normalized_sport,
        league=normalized_league,
        market_family=normalized_market_family,
        stat_type=normalized_stat_type,
        period=normalized_period,
    )


def register_model_family_adapter(model_family: str, adapter: Adapter) -> None:
    """Register one reviewed model-family adapter.

    Registration is code-controlled, not caller-controlled. Production should
    only register adapters shipped in the repository and covered by tests.
    """
    key = _normalize(model_family)
    if not key:
        raise ValueError("model_family is required")
    _ADAPTERS[key] = adapter


def clear_model_family_adapters() -> None:
    """Test helper; production startup should not use this."""
    _ADAPTERS.clear()


def _rpc_payload(
    client: Any,
    capability: PropCapabilityKey,
    feature_schema_version: str,
) -> Mapping[str, Any]:
    try:
        result = client.rpc(
            "wow_prop_certified_model_artifact_v2",
            {
                "p_sport": capability.sport,
                "p_league": capability.league,
                "p_market_family": capability.market_family,
                "p_stat_type": capability.stat_type,
                "p_period": capability.period,
                "p_feature_schema_version": feature_schema_version,
            },
        ).execute()
    except Exception as exc:
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_REGISTRY_UNAVAILABLE",
            "Could not read the governed exact prop model registry.",
        ) from exc
    payload = result.data
    if not isinstance(payload, Mapping):
        raise PropFittedProviderUnavailable(
            "PROP_MODEL_REGISTRY_INVALID_RESPONSE",
            "Governed prop model registry returned an invalid response.",
        )
    return payload


def _assert_exact_route(payload: Mapping[str, Any], expected: PropCapabilityKey) -> None:
    actual = PropCapabilityKey(
        sport=_normalize(payload.get("sport")),
        league=_normalize(payload.get("league")),
        market_family=_normalize(payload.get("market_family")),
        stat_type=_normalize(payload.get("stat_type")),
        period=_normalize(payload.get("period")),
    )
    if actual != expected:
        raise PropFittedProviderUnavailable(
            "PROP_CAPABILITY_ROUTE_MISMATCH",
            "Resolved artifact does not match the exact normalized prop capability tuple.",
        )


def resolve_certified_artifact(
    client: Any,
    *,
    sport: str,
    stat_type: str,
    feature_schema_version: str,
    league: Optional[str] = None,
    market_family: Optional[str] = None,
    period: Optional[str] = None,
) -> Optional[ResolvedArtifact]:
    exact = capability_key(
        sport=sport,
        league=league,
        market_family=market_family,
        stat_type=stat_type,
        period=period,
    )
    payload = _rpc_payload(client, exact, feature_schema_version)
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
    _assert_exact_route(payload, exact)

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
        capability=exact,
    )


def infer_certified_distribution(
    client: Any,
    *,
    request: PropInferenceRequest,
    line: float,
    features: Mapping[str, Any],
) -> CertifiedInference:
    """Resolve one exact certified bundle and return its raw PMF with provenance.

    Absence of a certified artifact or reviewed adapter is a hard abstention.
    The provider never calibrates, publishes, persists, or executes.
    """
    artifact = resolve_certified_artifact(
        client,
        sport=request.sport,
        league=getattr(request, "league", None),
        market_family=getattr(request, "market_family", None),
        stat_type=request.stat_type,
        period=getattr(request, "period", None),
        feature_schema_version=request.feature_schema_version,
    )
    if artifact is None:
        raise PropFittedProviderUnavailable(
            "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "No active prospectively certified prop artifact exists for this exact route.",
        )
    artifact.bundle.assert_compatible(request, line)
    adapter = _ADAPTERS.get(_normalize(artifact.model_family))
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
