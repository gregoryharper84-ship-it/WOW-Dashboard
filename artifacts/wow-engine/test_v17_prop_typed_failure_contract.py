from types import SimpleNamespace

import pytest

import api_prod_market as market
from prop_fitted_provider import PropFittedProviderUnavailable


class _FakeRPC:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return SimpleNamespace(data=self.payload)


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _FakeRPC(self.payload)


def test_market_preflight_resolves_exact_five_field_capability(monkeypatch):
    client = _FakeClient({"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"})
    monkeypatch.setattr(market._legacy.prod, "get_client", lambda: client)

    result = market._prop_route_artifact("nfl", "passing_yards")

    assert result["code"] == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"
    assert client.calls == [
        (
            "wow_prop_certified_model_artifact_v2",
            {
                "p_sport": "NFL",
                "p_league": "NFL",
                "p_market_family": "PLAYER_PROP",
                "p_stat_type": "PASSING_YARDS",
                "p_period": "FULL_GAME",
                "p_feature_schema_version": "PROP_FEATURES_V1",
            },
        )
    ]


def test_market_period_uses_shared_first_inning_normalizer():
    assert market._prop_period("PITCH_COUNT_1IP") == "FIRST_INNING"
    assert market._prop_period("FIRST_INNING_PITCHES_THROWN") == "FIRST_INNING"
    assert market._prop_period("1ST_INNING_PITCHES_THROWN") == "FIRST_INNING"
    assert market._prop_period("PITCHER_STRIKEOUTS") == "FULL_GAME"


def test_exact_route_absence_is_the_only_capability_not_found_case():
    assert market._route_failure_contract(
        {"code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}
    ) == (409, "MODEL_UNAVAILABLE", "CAPABILITY")
    assert market._route_failure_contract(
        {"code": "PROP_FEATURE_SCHEMA_MISMATCH", "blocking_scope": "EVIDENCE"}
    ) == (422, "MODEL_INPUTS_INSUFFICIENT", "EVIDENCE")
    assert market._route_failure_contract(
        {"code": "SPECIALIST_ROUTING_CONFLICT"}
    ) == (409, "SPECIALIST_ROUTING_CONFLICT", "CAPABILITY")
    assert market._route_failure_contract(
        {"code": "PROP_MODEL_REGISTRY_UNAVAILABLE"}
    ) == (503, "PROP_MODEL_REGISTRY_UNAVAILABLE", "INFRASTRUCTURE")


def test_invoked_scorer_crash_is_not_relabelled_model_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr(market, "_ORIGINAL_SCORE_DISCRETE", boom)
    with pytest.raises(PropFittedProviderUnavailable) as exc:
        market._guarded_score_discrete_prop_end_to_end()
    assert exc.value.code == "MODEL_SCORER_FAILED"
