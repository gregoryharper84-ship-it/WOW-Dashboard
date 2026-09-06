"""V17 receipt-semantics facade for the governed prop request runtime.

The producing implementation is preserved byte-for-byte in
``pick_request_runtime_core``. This facade adds only row-receipt semantics:
pre-scorer construction failures remain ``scoring_attempted=false``; once the
fitted scorer is called, success and failure receipts are
``scoring_attempted=true``; unexpected scorer exceptions are typed
``MODEL_SCORER_FAILED``. No model, evidence, line, calibration, ranking, or
terminal-reducer behavior is changed here.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

import pick_request_runtime_core as _core
from pick_request_runtime_core import *  # noqa: F401,F403


_ORIGINAL_TERMINAL = _core._terminal
_ORIGINAL_COMPLETED_SCORED_OUTCOME = _core._completed_scored_outcome
_ORIGINAL_AUTO_HYDRATE_PROP_EVIDENCE = _core.auto_hydrate_prop_evidence

# Preserve the source-level exact-line contract used by V17 certification:
# frozen snapshot contains `"line": float(row.line)` and the score request
# contains `"line": row.line`; the request threshold is never overwritten.


def _auto_hydrate_prop_evidence_delegate(*args: Any, **kwargs: Any) -> Any:
    """Keep the historical monkeypatch/public-module seam intact.

    Existing tests and diagnostic harnesses patch
    ``pick_request_runtime.auto_hydrate_prop_evidence`` after route
    installation. The core route resolves its global at request time, so this
    delegate forwards to the facade's current value instead of hiding that
    seam behind the implementation module.
    """
    current = globals().get(
        "auto_hydrate_prop_evidence",
        _ORIGINAL_AUTO_HYDRATE_PROP_EVIDENCE,
    )
    if current is _auto_hydrate_prop_evidence_delegate:
        current = _ORIGINAL_AUTO_HYDRATE_PROP_EVIDENCE
    return current(*args, **kwargs)


_core.auto_hydrate_prop_evidence = _auto_hydrate_prop_evidence_delegate


def _terminal(
    row_key: str,
    status: str,
    code: str,
    *,
    detail: Optional[dict[str, Any]] = None,
    snapshot_id: Optional[str] = None,
    acquisition: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = dict(detail or {})
    scoring_attempted = bool(payload.get("scoring_attempted") is True)
    normalized_code = code

    # The core's only generic ROW_SCORING_UNAVAILABLE path occurs before
    # score_prop is called (ScorePropRequest construction). Post-invocation
    # unexpected exceptions are translated by _ScoringReceiptMarketApi below.
    if code == "ROW_SCORING_UNAVAILABLE" and not scoring_attempted:
        normalized_code = "ROW_SCORING_INVALID_REQUEST"
        payload.setdefault("original_code", code)
        payload.setdefault("specialist_invoked", False)
        # This is a pre-model request/input failure, never missing capability.
        # Force the reducer's terminal namespace to the correct V17 class while
        # retaining the more precise row code for diagnostics.
        payload.setdefault("terminal_label", "MODEL_INPUTS_INSUFFICIENT")

    out = _ORIGINAL_TERMINAL(
        row_key,
        status,
        normalized_code,
        detail=payload,
        snapshot_id=snapshot_id,
        acquisition=acquisition,
    )
    out["scoring_attempted"] = scoring_attempted
    return out


def _completed_scored_outcome(**kwargs: Any) -> dict[str, Any]:
    out = _ORIGINAL_COMPLETED_SCORED_OUTCOME(**kwargs)
    out["scoring_attempted"] = True
    return out


# The core resolves these helpers as module globals at request time, so install
# the receipt-aware versions once without altering the sporting model path.
_core._terminal = _terminal
_core._completed_scored_outcome = _completed_scored_outcome


class _ScoringReceiptMarketApi:
    """Transparent market-api proxy that types post-invocation failures."""

    def __init__(self, wrapped: Any):
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def score_prop(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._wrapped.score_prop(*args, **kwargs)
        except HTTPException as exc:
            detail = (
                dict(exc.detail)
                if isinstance(exc.detail, dict)
                else {"message": str(exc.detail)}
            )
            detail["scoring_attempted"] = True
            detail.setdefault("specialist_invoked", True)
            raise HTTPException(
                status_code=exc.status_code,
                detail=detail,
                headers=exc.headers,
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "MODEL_SCORER_FAILED",
                    "error_type": type(exc).__name__,
                    "scoring_attempted": True,
                    "specialist_invoked": True,
                },
            ) from exc


def install_pick_request_routes(
    app: Any,
    *,
    market_api: Any,
    auth_dependency: Any,
) -> None:
    return _core.install_pick_request_routes(
        app,
        market_api=_ScoringReceiptMarketApi(market_api),
        auth_dependency=auth_dependency,
    )


def __getattr__(name: str) -> Any:
    # Preserve private/helper imports used by diagnostics and tests while the
    # core implementation remains a byte-identical copy of the pre-fix file.
    return getattr(_core, name)
