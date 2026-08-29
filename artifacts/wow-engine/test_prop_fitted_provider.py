from __future__ import annotations

from types import SimpleNamespace

import pytest

from prop_distribution_contract import CoverageDecision, PropInferenceRequest, RawDiscreteDistribution
from prop_fitted_provider import (
    PropFittedProviderUnavailable,
    clear_model_family_adapters,
    infer_distribution,
    register_model_family_adapter,
    resolve_certified_artifact,
)


class FakeRPC:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return SimpleNamespace(data=self.payload)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return FakeRPC(self.payload)


def _artifact_payload(**overrides):
    payload = {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "artifact_id": "11111111-1111-4111-8111-111111111111",
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": "TEST_DISCRETE_V1",
        "model_artifact_version": "TEST_MODEL_V1",
        "sport": "WNBA",
        "stat_type": "POINTS",
        "feature_schema_version": "PROP_FEATURES_V1",
        "feature_transform_version": "PROP_TRANSFORM_V1",
        "specialist_version": "wow.wnba-points-v1",
        "certification_id": "CERT-TEST-1",
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "artifact_format": "TEST_ONLY",
        "artifact_payload": {"calibrator_version": "CAL_TEST_V1"},
        "supported_line_min": 0,
        "supported_line_max": 60,
        "training_rows": 1000,
        "validation_metrics": {"brier": 0.2},
        "probability_publishable": False,
        "can_execute": False,
    }
    payload.update(overrides)
    return payload


def _request():
    return PropInferenceRequest(
        event_id="WNBA:TEST",
        player_id="player-1",
        sport="WNBA",
        league_season="2026",
        stat_type="POINTS",
        evidence_snapshot_id="22222222-2222-4222-8222-222222222222",
        market_identity_id="market-1",
        as_of_timestamp="2026-08-29T12:00:00+00:00",
        request_id="req-1",
        feature_schema_version="PROP_FEATURES_V1",
    )


def teardown_function():
    clear_model_family_adapters()


def test_missing_certified_artifact_returns_none():
    client = FakeClient(
        {
            "ok": False,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "probability_publishable": False,
            "can_execute": False,
        }
    )
    assert resolve_certified_artifact(
        client, sport="WNBA", stat_type="POINTS", feature_schema_version="PROP_FEATURES_V1"
    ) is None
    assert client.calls[0][0] == "wow_prop_certified_model_artifact"


def test_wrong_provider_identity_fails_closed():
    with pytest.raises(PropFittedProviderUnavailable) as exc:
        resolve_certified_artifact(
            FakeClient(_artifact_payload(provider_identity="THIRD_PARTY_MODEL")),
            sport="WNBA",
            stat_type="POINTS",
            feature_schema_version="PROP_FEATURES_V1",
        )
    assert exc.value.code == "PROP_PROVIDER_IDENTITY_MISMATCH"


def test_uncertified_lifecycle_fails_closed():
    with pytest.raises(PropFittedProviderUnavailable) as exc:
        resolve_certified_artifact(
            FakeClient(_artifact_payload(lifecycle_state="SHADOW")),
            sport="WNBA",
            stat_type="POINTS",
            feature_schema_version="PROP_FEATURES_V1",
        )
    assert exc.value.code == "PROP_BUNDLE_NOT_CERTIFIED"


def test_missing_runtime_adapter_abstains():
    with pytest.raises(PropFittedProviderUnavailable) as exc:
        infer_distribution(
            FakeClient(_artifact_payload()), request=_request(), line=22.5, features={"minutes": 32}
        )
    assert exc.value.code == "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE"


def test_reviewed_adapter_can_return_direction_free_distribution():
    def adapter(artifact, request, features):
        assert artifact.bundle.model_artifact_version == "TEST_MODEL_V1"
        assert request.stat_type == "POINTS"
        assert features["minutes"] == 32
        return RawDiscreteDistribution(
            support={20: 0.25, 21: 0.25, 22: 0.25, 23: 0.25},
            coverage=CoverageDecision(True, 0.1, ()),
            model_artifact_version=artifact.bundle.model_artifact_version,
            training_code_sha=artifact.bundle.training_code_sha,
            training_dataset_hash=artifact.bundle.training_dataset_hash,
            feature_schema_version=artifact.bundle.feature_schema_version,
            feature_transform_sha="d" * 64,
            feature_snapshot_hash="e" * 64,
            artifact_checksum=artifact.bundle.artifact_checksum,
            inference_timestamp="2026-08-29T12:00:01+00:00",
        )

    register_model_family_adapter("TEST_DISCRETE_V1", adapter)
    result = infer_distribution(
        FakeClient(_artifact_payload()), request=_request(), line=22.5, features={"minutes": 32}
    )
    assert result.publication_status == "NOT_EVALUATED"
    assert result.can_execute is False
    assert result.expected_value == pytest.approx(21.5)


def test_bundle_line_coverage_is_enforced_before_adapter():
    register_model_family_adapter("TEST_DISCRETE_V1", lambda *args: None)
    with pytest.raises(Exception) as exc:
        infer_distribution(
            FakeClient(_artifact_payload(supported_line_max=20)),
            request=_request(),
            line=22.5,
            features={},
        )
    assert getattr(exc.value, "code", None) == "MODEL_CALIBRATOR_BUNDLE_MISMATCH"
