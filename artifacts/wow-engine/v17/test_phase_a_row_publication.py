import sys
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException

import v17.phase_a_row_publication as bridge


def _preflight(**overrides):
    payload = {
        "ok": True,
        "specialist_model_capability": "AVAILABLE",
        "governed_probability_capability": "AVAILABLE",
        "calibration_health_status": "PASS",
        "probability_publishable": False,
        "governed_publishable": False,
        "probability_claim_status": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "failed_contract_scope": [],
        "blockers": [],
        "capability_evidence": {
            "calibration_phase": "PHASE_A_PRECALIBRATION_SHRINKAGE",
            "money_qualified_allowed": False,
            "final_approved_allowed": False,
        },
    }
    payload.update(overrides)
    return payload


def test_phase_a_row_scoring_permitted_only_for_exact_typed_state():
    assert bridge.phase_a_row_scoring_permitted(_preflight()) is True


@pytest.mark.parametrize(
    "override",
    [
        {"specialist_model_capability": "UNAVAILABLE"},
        {"governed_probability_capability": "UNAVAILABLE"},
        {"calibration_health_status": "BLOCKED"},
        {"probability_publishable": True},
        {"probability_claim_status": "MODEL_UNAVAILABLE"},
        {"terminal_ceiling": "FINAL_APPROVED"},
        {"blockers": ["MODEL_INPUTS_INSUFFICIENT"]},
        {"failed_contract_scope": ["GLOBAL"]},
        {"capability_evidence": {"calibration_phase": "PHASE_B_PLATT", "money_qualified_allowed": True, "final_approved_allowed": True}},
        {"capability_evidence": {"calibration_phase": "PHASE_A_PRECALIBRATION_SHRINKAGE", "money_qualified_allowed": True, "final_approved_allowed": False}},
    ],
)
def test_phase_a_bridge_remains_fail_closed_for_other_states(override):
    assert bridge.phase_a_row_scoring_permitted(_preflight(**override)) is False


class FakeMarketApi:
    class ScorePropRequest:
        pass

    def __init__(self):
        self.fallback_calls = []

    def score_prop(self, req, identity=None):
        self.fallback_calls.append((req, identity))
        return {"source": "fallback", "probability_publishable": False, "can_execute": False}


def test_install_routes_phase_a_to_original_and_other_states_to_fallback(monkeypatch):
    original_calls = []

    def original(req, identity=None):
        original_calls.append((req, identity))
        return {
            "source": "original",
            "probability_publishable": True,
            "prediction": {
                "calibration_status": "PRECALIBRATION_SHRINKAGE",
                "calibration_method": "CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1",
                "calibrated_probability": 0.58,
                "calibrated_probability_lower_bound": 0.53,
                "calibrated_probability_upper_bound": 0.62,
            },
            "can_execute": False,
        }

    monkeypatch.setitem(sys.modules, "api_ncaaf_acceptance", SimpleNamespace(_original_market_score_prop=original))
    state = {"preflight": _preflight()}
    monkeypatch.setattr(bridge.lane_patch, "_governed_preflight", lambda _api: state["preflight"])

    app = FastAPI()
    market = FakeMarketApi()
    assert bridge.install_phase_a_row_publication(
        app,
        auth_dependency=Depends(lambda: True),
        market_api=market,
    ) is True

    req = object()
    result = market.score_prop(req, "WOW_BETTING_ENGINE")
    assert result["source"] == "original"
    assert result["probability_publishable"] is True
    assert result["can_execute"] is False
    assert len(original_calls) == 1
    assert market.fallback_calls == []

    state["preflight"] = _preflight(blockers=["MODEL_INPUTS_INSUFFICIENT"])
    fallback = market.score_prop(req, "WOW_BETTING_ENGINE")
    assert fallback["source"] == "fallback"
    assert len(market.fallback_calls) == 1


def test_install_fails_closed_when_original_scorer_is_not_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "api_ncaaf_acceptance", SimpleNamespace())
    app = FastAPI()
    market = FakeMarketApi()
    assert bridge.install_phase_a_row_publication(
        app,
        auth_dependency=Depends(lambda: True),
        market_api=market,
    ) is False
    assert getattr(market, "_wow_v17_phase_a_row_publication_installed", False) is False
