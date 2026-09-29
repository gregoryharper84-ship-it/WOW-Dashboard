from __future__ import annotations

from fastapi import HTTPException

from v17.full_board_overlay import _safe_score_wrapper


def test_model_inputs_insufficient_http_exception_survives_full_board_wrapper():
    def insufficient(*_args, **_kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_INPUTS_INSUFFICIENT",
                "failure_class": "MODEL_INPUTS_INSUFFICIENT",
                "missing_fields": ["calibration_artifact"],
                "model_invoked": False,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
        )

    result = _safe_score_wrapper(insufficient)(object())

    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["model_status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["failure_class"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["missing_fields"] == ["calibration_artifact"]
    assert result["model_invoked"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_model_unavailable_regime_http_exception_survives_full_board_wrapper():
    def unsupported_regime(*_args, **_kwargs):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "reason": "MLB_PLAYOFFS_REGIME_NOT_SUPPORTED_BY_FITTED_MODEL",
                "regime": "PLAYOFFS",
                "supported_regimes": ["REGULAR_SEASON"],
                "model_invoked": False,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
        )

    result = _safe_score_wrapper(unsupported_regime)(object())

    assert result["code"] == "MODEL_UNAVAILABLE"
    assert result["model_status"] == "MODEL_UNAVAILABLE"
    assert result["reason"] == "MLB_PLAYOFFS_REGIME_NOT_SUPPORTED_BY_FITTED_MODEL"
    assert result["regime"] == "PLAYOFFS"
    assert result["supported_regimes"] == ["REGULAR_SEASON"]
    assert result["model_invoked"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_untyped_http_exception_still_fails_closed_as_scorer_failure():
    def untyped(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="provider returned an untyped failure")

    result = _safe_score_wrapper(untyped)(object())

    assert result["code"] == "MODEL_SCORER_FAILED"
    assert result["model_status"] == "MODEL_SCORER_FAILED"
    assert result["scorer_status"] == "UNTYPED_HTTP_EXCEPTION"
    assert result["error_type"] == "HTTPException"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert "provider returned an untyped failure" not in repr(result)
