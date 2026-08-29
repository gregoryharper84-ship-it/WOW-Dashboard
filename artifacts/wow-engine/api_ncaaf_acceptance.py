"""NCAAF evaluation wrapper around the governed production API.

Adds one authenticated research-only maintenance boundary for automatic NCAAF
closing-line capture. All existing prop/event behavior is inherited unchanged.
The boundary cannot place or modify wagers; it only reads a configured market
feed and writes calibration evidence.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

import api_prod_market_acceptance as base
from ncaaf_closing_capture import run_from_environment

app = base.app


@app.post(
    "/internal/ncaaf/capture-closing-lines",
    dependencies=[Depends(base.market_api.prod._require_action_api_key)],
    operation_id="captureNcaafClosingLines",
)
def capture_ncaaf_closing_lines():
    try:
        result = run_from_environment()
    except RuntimeError as exc:
        message = str(exc)
        code = (
            "NCAAF_CLOSING_FEED_UNCONFIGURED"
            if "WOW_NCAAF_MARKET_FEED_URL" in message
            else "NCAAF_CLOSING_CAPTURE_CONFIGURATION_UNAVAILABLE"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": code,
                "message": message,
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "NCAAF_CLOSING_CAPTURE_FAILED",
                "error_type": type(exc).__name__,
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    return {
        "ok": True,
        "status": result.status,
        "candidates_checked": result.candidates_checked,
        "quotes_captured": result.quotes_captured,
        "no_close_marked": result.no_close_marked,
        "provider_failures": result.provider_failures,
        "identity_failures": result.identity_failures,
        "stale_quote_failures": result.stale_quote_failures,
        "probability_publishable": False,
        "can_execute": False,
    }
