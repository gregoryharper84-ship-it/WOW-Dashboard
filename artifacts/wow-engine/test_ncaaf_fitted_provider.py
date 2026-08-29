from pathlib import Path
from types import SimpleNamespace

import pytest

import ncaaf_fitted_provider as provider


class RPCClient:
    def __init__(self, payload):
        self.payload = payload
        self.last_rpc = None
        self.last_args = None

    def rpc(self, name, args):
        self.last_rpc = name
        self.last_args = args
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.payload))


def certified_payload(**overrides):
    payload = {
        "ok": True,
        "code": "NCAAF_CERTIFIED_MODEL_ARTIFACT_READY",
        "artifact_id": "artifact-1",
        "provider_identity": provider.PROVIDER_IDENTITY,
        "model_family": "LOGISTIC_V1",
        "model_artifact_version": "ncaaf-v1",
        "feature_schema_version": "NCAAF_FEATURES_V1",
        "feature_transform_version": "NCAAF_TRANSFORM_V1",
        "specialist_version": "wow.llp-ncaaf-game-win-probability-expert-v1",
        "certification_id": "cert-1",
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "training_dataset_hash": "dataset-hash",
        "training_code_sha": "code-sha",
        "artifact_checksum": "checksum",
        "artifact_format": "JSON_COEFFICIENTS",
        "artifact_payload": {"intercept": 0.0, "coefficients": {"power_delta": 1.0}},
        "training_rows": 2000,
        "training_seasons": [2022, 2023, 2024, 2025],
        "validation_start_date": "2025-08-01",
        "validation_end_date": "2025-12-31",
        "validation_metrics": {"brier": 0.22, "log_loss": 0.64},
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibrator_version": "ncaaf-cal-v1",
        "calibration_training_n": 500,
        "probability_publishable": True,
        "can_execute": False,
    }
    payload.update(overrides)
    return payload


def test_missing_artifact_returns_none():
    client = RPCClient({
        "ok": False,
        "code": "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
        "probability_publishable": False,
        "can_execute": False,
    })
    result = provider.resolve_certified_artifact(client, feature_schema_version="NCAAF_FEATURES_V1")
    assert result is None
    assert client.last_rpc == "wow_ncaaf_certified_model_artifact"


def test_non_publishable_registry_row_fails_closed():
    client = RPCClient(certified_payload(probability_publishable=False))
    with pytest.raises(provider.NCAAFFittedProviderUnavailable) as exc:
        provider.resolve_certified_artifact(client, feature_schema_version="NCAAF_FEATURES_V1")
    assert exc.value.code == "NCAAF_ARTIFACT_NOT_PUBLISHABLE"


def test_adapter_required_and_output_normalized():
    provider.clear_model_family_adapters()
    client = RPCClient(certified_payload())
    request = provider.NCAAFInferenceRequest(
        official_event_id="evt-1",
        feature_schema_version="NCAAF_FEATURES_V1",
        feature_as_of="2026-08-29T12:00:00+00:00",
        home_team="A",
        away_team="B",
    )
    with pytest.raises(provider.NCAAFFittedProviderUnavailable) as exc:
        provider.infer_raw_probability(client, request=request, features={})
    assert exc.value.code == "NCAAF_MODEL_FAMILY_ADAPTER_UNAVAILABLE"

    def adapter(artifact, req, features):
        return provider.RawNCAAFWinProbability(
            home_probability=0.61,
            away_probability=0.39,
            model_artifact_version=artifact.model_artifact_version,
            artifact_id=artifact.artifact_id,
        )

    provider.register_model_family_adapter("LOGISTIC_V1", adapter)
    result = provider.infer_raw_probability(client, request=request, features={"power_delta": 0.5})
    assert result.home_probability == pytest.approx(0.61)
    assert result.away_probability == pytest.approx(0.39)
    assert provider.CAN_EXECUTE is False


def test_invalid_probability_output_is_rejected():
    provider.clear_model_family_adapters()
    client = RPCClient(certified_payload())
    request = provider.NCAAFInferenceRequest(
        official_event_id="evt-1",
        feature_schema_version="NCAAF_FEATURES_V1",
        feature_as_of="2026-08-29T12:00:00+00:00",
        home_team="A",
        away_team="B",
    )

    provider.register_model_family_adapter(
        "LOGISTIC_V1",
        lambda artifact, req, features: provider.RawNCAAFWinProbability(
            home_probability=0.61,
            away_probability=0.41,
            model_artifact_version=artifact.model_artifact_version,
            artifact_id=artifact.artifact_id,
        ),
    )
    with pytest.raises(provider.NCAAFFittedProviderUnavailable) as exc:
        provider.infer_raw_probability(client, request=request, features={})
    assert exc.value.code == "NCAAF_MODEL_OUTPUT_NOT_NORMALIZED"


def test_sql_contracts_remain_fail_closed():
    registry = Path("ncaaf_fitted_model_registry.sql").read_text().lower()
    training = Path("ncaaf_training_contract.sql").read_text().lower()

    assert "wow_ncaaf_fitted_model_artifacts" in registry
    assert "probability_publishable = false" in registry
    assert "can_execute = false" in registry
    assert "security invoker" in registry
    assert "revoke all on table public.wow_ncaaf_fitted_model_artifacts from anon, authenticated" in registry

    assert "wow_ncaaf_training_games" in training
    assert "wow_ncaaf_training_features" in training
    assert "wow_ncaaf_assert_feature_pregame" in training
    assert "new.feature_as_of >= v_start" in training
    assert "new.market_timestamp >= v_start" in training
    assert "can_execute = false" in training
    assert "revoke all on table public.wow_ncaaf_training_features from anon, authenticated" in training
