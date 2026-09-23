from types import SimpleNamespace

import pytest

from v17.llp_rundown_value_shadow import install_llp_rundown_value_shadow


class Request:
    market_prior = {"home_probability": 0.55, "away_probability": 0.45, "source": "CALLER"}


def _base_result():
    return {
        "calibrated_home_probability": 0.624,
        "calibrated_away_probability": 0.376,
        "calibrated_home_lower_bound": 0.593,
        "calibrated_away_lower_bound": 0.345,
        "rank_eligible": True,
        "blockers": ["EXISTING_BLOCKER"],
        "llp_rundown_market_evidence": {
            "status": "EXACT_LINE",
            "home_probability": 0.58,
            "away_probability": 0.42,
            "quality": "CROSS_BOOK_NO_VIG",
            "book_count": 6,
            "timestamp": "2026-09-22T20:00:00Z",
            "prediction_authority": False,
            "can_execute": False,
        },
        "can_execute": False,
    }


def test_shadow_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WOW_LLP_RUNDOWN_VALUE_SHADOW_ENABLED", raising=False)
    module = SimpleNamespace(score_team_event_request=lambda *a, **k: _base_result())
    assert install_llp_rundown_value_shadow(module) is False
    assert not hasattr(module, "_v17_llp_rundown_value_shadow_installed")


def test_enabled_shadow_reuses_existing_evidence_without_mutating_core_output(monkeypatch):
    monkeypatch.setenv("WOW_LLP_RUNDOWN_VALUE_SHADOW_ENABLED", "true")
    calls = []
    req = Request()
    original_prior = dict(req.market_prior)

    def score(request, *, event_api, canonical_hydration_required=False):
        calls.append("score")
        return _base_result()

    module = SimpleNamespace(score_team_event_request=score)
    assert install_llp_rundown_value_shadow(module) is True
    result = module.score_team_event_request(req, event_api=object())

    assert calls == ["score"]
    assert req.market_prior == original_prior
    assert result["calibrated_home_probability"] == pytest.approx(0.624)
    assert result["calibrated_home_lower_bound"] == pytest.approx(0.593)
    assert result["rank_eligible"] is True
    assert result["blockers"] == ["EXISTING_BLOCKER"]
    shadow = result["llp_market_value_shadow"]
    assert shadow["sides"]["home"]["model_vs_consensus_pp"] == pytest.approx(4.4)
    assert shadow["sides"]["home"]["point_price_edge_pp"] is None
    assert shadow["sides"]["home"]["conservative_price_edge_pp"] is None
    assert shadow["probability_rank_mutated"] is False
    assert result["can_execute"] is False
