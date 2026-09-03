from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import v17.prop_forward_cohort_market_adapter as adapter_mod
from v17.prop_forward_cohort_market_adapter import (
    ForwardCohortMarketAdapter,
    _typed_research_only_state,
)


def _preflight(**overrides):
    payload = {
        "ok": True,
        "governed_probability_capability": "AVAILABLE",
        "specialist_model_capability": "AVAILABLE",
        "calibration_health_status": "PASS",
        "probability_publishable": False,
        "governed_publishable": False,
        "probability_claim_status": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "failed_contract_scope": [],
    }
    payload.update(overrides)
    return payload


class FakeProd:
    PROP_CAPABILITY_KEY = "PROP_PROBABILITY"

    def __init__(self):
        self.identity_seen = None

    def _runtime_capability(self, _key):
        return {"capability_status": "AVAILABLE", "evidence": {}}

    def _reject_llp_prop_identity(self, identity):
        self.identity_seen = identity
        return identity or "WOW_BETTING_ENGINE"


class FakeMarketApi:
    class ScorePropRequest:
        pass

    def __init__(self, result=None, error=None):
        self.prod = FakeProd()
        self._result = result
        self._error = error

    def score_prop(self, req, identity=None):
        if self._error is not None:
            raise self._error
        return self._result


def _generic_hold():
    return HTTPException(
        status_code=409,
        detail={"code": "PROP_PROBABILITY_UNAVAILABLE", "probability_publishable": False, "can_execute": False},
    )


def test_typed_empty_blocker_state_is_research_only_eligible():
    assert _typed_research_only_state(_preflight(), []) is True


@pytest.mark.parametrize(
    "override",
    [
        {"governed_probability_capability": "UNAVAILABLE"},
        {"specialist_model_capability": "UNAVAILABLE"},
        {"probability_claim_status": "MODEL_UNAVAILABLE"},
        {"terminal_ceiling": "MODEL_UNAVAILABLE"},
        {"failed_contract_scope": ["GLOBAL"]},
        {"failed_contract_scope": ["CONFIDENCE"]},
        {"probability_publishable": True},
    ],
)
def test_ambiguous_or_model_unavailable_state_remains_closed(override):
    assert _typed_research_only_state(_preflight(**override), []) is False


def test_nonpublication_blocker_remains_closed(monkeypatch):
    monkeypatch.setattr(adapter_mod.lane_patch, "_publication_only", lambda blockers: False)
    assert _typed_research_only_state(_preflight(), ["MODEL_INPUTS_INSUFFICIENT"]) is False


def test_adapter_passes_through_normal_score():
    expected = {"ok": True, "probability_publishable": True}
    wrapped = ForwardCohortMarketAdapter(FakeMarketApi(result=expected))
    assert wrapped.score_prop(object(), "WOW_BETTING_ENGINE") is expected


def test_adapter_reraises_non_generic_hold():
    exc = HTTPException(status_code=422, detail={"code": "MODEL_UNAVAILABLE"})
    wrapped = ForwardCohortMarketAdapter(FakeMarketApi(error=exc))
    with pytest.raises(HTTPException) as caught:
        wrapped.score_prop(object(), "WOW_BETTING_ENGINE")
    assert caught.value is exc


def test_adapter_uses_raw_research_only_for_exact_typed_state(monkeypatch):
    raw = {
        "ok": True,
        "research_only": True,
        "probability_publishable": False,
        "governed_publishable": False,
        "research_model_output": {"raw_specialist_probability": 0.61},
        "can_execute": False,
    }
    market = FakeMarketApi(error=_generic_hold())
    wrapped = ForwardCohortMarketAdapter(market)
    monkeypatch.setattr(adapter_mod.lane_patch, "_governed_preflight", lambda _api: _preflight())
    monkeypatch.setattr(adapter_mod.lane_patch, "_collect_blockers", lambda _value: [])
    calls = []

    def fake_raw(api, req, *, model_identity, lane, preflight, blockers):
        calls.append((api, req, model_identity, lane, preflight, blockers))
        return raw

    monkeypatch.setattr(adapter_mod.lane_patch, "_raw_specialist_research", fake_raw)
    req = object()
    assert wrapped.score_prop(req, "WOW_BETTING_ENGINE") is raw
    assert len(calls) == 1
    assert calls[0][0] is market
    assert calls[0][1] is req
    assert calls[0][2] == "WOW_BETTING_ENGINE"
    assert calls[0][5] == []


def test_adapter_reraises_generic_hold_when_typed_state_not_proven(monkeypatch):
    exc = _generic_hold()
    market = FakeMarketApi(error=exc)
    wrapped = ForwardCohortMarketAdapter(market)
    monkeypatch.setattr(
        adapter_mod.lane_patch,
        "_governed_preflight",
        lambda _api: _preflight(probability_claim_status="MODEL_UNAVAILABLE"),
    )
    monkeypatch.setattr(adapter_mod.lane_patch, "_collect_blockers", lambda _value: [])
    monkeypatch.setattr(
        adapter_mod.lane_patch,
        "_raw_specialist_research",
        lambda *args, **kwargs: pytest.fail("raw research must not be invoked"),
    )
    with pytest.raises(HTTPException) as caught:
        wrapped.score_prop(object(), "WOW_BETTING_ENGINE")
    assert caught.value is exc
