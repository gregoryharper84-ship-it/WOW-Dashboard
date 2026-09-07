from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException

import pick_request_runtime as runtime


def _completed_outcome() -> dict:
    return {
        "row_key": "cease-k-more-7p5-20260907",
        "terminal_status": "COMPLETED",
        "terminal_label": "FULL_MODEL",
        "model_evaluated": True,
        "scoring_attempted": True,
        "probability_publishable": True,
        "model_probability": 0.73,
        "calibrated_probability": 0.70,
        "calibrated_lower_bound": 0.66,
        "rank_eligible": True,
        "downstream_portfolio_evaluation_allowed": True,
        "can_execute": False,
    }


def test_downstream_portfolio_exception_preserves_sporting_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = _completed_outcome()
    sporting_before = {
        key: copy.deepcopy(outcome[key])
        for key in (
            "terminal_status",
            "terminal_label",
            "model_evaluated",
            "scoring_attempted",
            "probability_publishable",
            "model_probability",
            "calibrated_probability",
            "calibrated_lower_bound",
            "rank_eligible",
        )
    }
    leg = {
        "row_id": outcome["row_key"],
        "event_id": "cff274bc53dd8ab95d6cf9a14b59d6c7",
        "player": "Dylan Cease",
        "market_family": "PITCHER_STRIKEOUTS",
        "line": 7.5,
        "direction": "MORE",
    }

    def boom(_request_id, _scored_legs):
        raise RuntimeError("simulated downstream portfolio failure")

    monkeypatch.setattr(runtime, "_ORIGINAL_APPLY_PORTFOLIO_GOVERNANCE", boom)

    runtime._apply_portfolio_governance(
        "wow-single-row-test-20260907-cease-k-7p5-retry1",
        [(leg, outcome)],
    )

    for key, expected in sporting_before.items():
        assert outcome[key] == expected

    assert outcome["downstream_portfolio_evaluation_allowed"] is False
    assert outcome["portfolio_governance"] == {
        "status": "BLOCKED",
        "code": "PORTFOLIO_GOVERNANCE_UNAVAILABLE",
        "error_type": "RuntimeError",
        "blockers": ["PORTFOLIO_GOVERNANCE_UNAVAILABLE"],
        "receipt_preserved": True,
        "sporting_probability_mutated": False,
        "can_execute": False,
    }
    assert outcome["can_execute"] is False


def test_downstream_portfolio_success_remains_delegated(monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = _completed_outcome()
    leg = {"row_id": outcome["row_key"]}
    calls = []

    def success(request_id, scored_legs):
        calls.append((request_id, scored_legs))
        scored_legs[0][1]["portfolio_governance"] = {
            "status": "PASS",
            "sporting_probability_mutated": False,
            "can_execute": False,
        }

    monkeypatch.setattr(runtime, "_ORIGINAL_APPLY_PORTFOLIO_GOVERNANCE", success)
    runtime._apply_portfolio_governance("request-1", [(leg, outcome)])

    assert len(calls) == 1
    assert outcome["portfolio_governance"]["status"] == "PASS"
    assert outcome["downstream_portfolio_evaluation_allowed"] is True
    assert outcome["can_execute"] is False


def test_scorer_exception_remains_typed_and_does_not_become_model_unavailable() -> None:
    class FailingMarketApi:
        def score_prop(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    proxy = runtime._ScoringReceiptMarketApi(FailingMarketApi())

    with pytest.raises(HTTPException) as caught:
        proxy.score_prop(object())

    exc = caught.value
    assert exc.status_code == 500
    assert exc.detail["code"] == "MODEL_SCORER_FAILED"
    assert exc.detail["scoring_attempted"] is True
    assert exc.detail["specialist_invoked"] is True
    assert exc.detail["error_type"] == "RuntimeError"
    assert exc.detail["code"] != "MODEL_UNAVAILABLE"


def test_backend_http_detail_is_preserved_with_scoring_attempted() -> None:
    class FailingMarketApi:
        def score_prop(self, *_args, **_kwargs):
            raise HTTPException(
                status_code=422,
                detail={"code": "MODEL_INPUTS_INSUFFICIENT", "blocker": "OFFICIAL_EVENT_ID_UNRESOLVED"},
            )

    proxy = runtime._ScoringReceiptMarketApi(FailingMarketApi())

    with pytest.raises(HTTPException) as caught:
        proxy.score_prop(object())

    exc = caught.value
    assert exc.status_code == 422
    assert exc.detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert exc.detail["blocker"] == "OFFICIAL_EVENT_ID_UNRESOLVED"
    assert exc.detail["scoring_attempted"] is True
    assert exc.detail["specialist_invoked"] is True
