import pytest
from fastapi import HTTPException

import pick_request_runtime as runtime


def test_pre_scorer_construction_failure_is_not_scoring_attempted():
    out = runtime._terminal(
        "row-1",
        "HELD",
        "ROW_SCORING_UNAVAILABLE",
        detail={"error_type": "ValidationError"},
    )
    assert out["code"] == "ROW_SCORING_INVALID_REQUEST"
    assert out["terminal_label"] == "MODEL_INPUTS_INSUFFICIENT"
    assert out["scoring_attempted"] is False
    assert out["detail"]["specialist_invoked"] is False


def test_post_invocation_terminal_stays_model_scorer_failed():
    out = runtime._terminal(
        "row-2",
        "HELD",
        "MODEL_SCORER_FAILED",
        detail={
            "error_type": "RuntimeError",
            "scoring_attempted": True,
            "specialist_invoked": True,
        },
    )
    assert out["code"] == "MODEL_SCORER_FAILED"
    assert out["terminal_label"] == "MODEL_SCORER_FAILED"
    assert out["verdict_class"] == "SCORER_FAILED"
    assert out["scoring_attempted"] is True


def test_post_invocation_unexpected_exception_is_model_scorer_failed():
    class BrokenMarketApi:
        def score_prop(self, *args, **kwargs):
            raise RuntimeError("boom")

    proxy = runtime._ScoringReceiptMarketApi(BrokenMarketApi())
    with pytest.raises(HTTPException) as caught:
        proxy.score_prop(object())

    assert caught.value.status_code == 500
    assert caught.value.detail["code"] == "MODEL_SCORER_FAILED"
    assert caught.value.detail["scoring_attempted"] is True
    assert caught.value.detail["specialist_invoked"] is True
    assert caught.value.detail["error_type"] == "RuntimeError"


def test_typed_scorer_http_failure_preserves_exact_code_and_marks_attempted():
    class TypedFailureMarketApi:
        def score_prop(self, *args, **kwargs):
            raise HTTPException(
                status_code=422,
                detail={"code": "MODEL_OUTPUT_INVALID", "reason": "bad package"},
            )

    proxy = runtime._ScoringReceiptMarketApi(TypedFailureMarketApi())
    with pytest.raises(HTTPException) as caught:
        proxy.score_prop(object())

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "MODEL_OUTPUT_INVALID"
    assert caught.value.detail["reason"] == "bad package"
    assert caught.value.detail["scoring_attempted"] is True
    assert caught.value.detail["specialist_invoked"] is True


def test_successful_scorer_invocation_passes_through_unchanged():
    sentinel = {"prediction": {"calibrated_probability": 0.61}}

    class WorkingMarketApi:
        def score_prop(self, *args, **kwargs):
            return sentinel

    proxy = runtime._ScoringReceiptMarketApi(WorkingMarketApi())
    assert proxy.score_prop(object()) is sentinel


def test_facade_keeps_exact_line_field_in_core_request_payload_contract():
    source = open(runtime._core.__file__, encoding="utf-8").read()
    assert '"line": row.line' in source
    assert 'request_payload["line"]' not in source
