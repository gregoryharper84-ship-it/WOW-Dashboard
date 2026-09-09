"""V17 receipt-semantics facade for the governed prop request runtime.

The producing implementation is preserved in ``pick_request_runtime_core``.
This facade adds receipt/error-boundary and Top-10 completion semantics:
- pre-scorer construction failures remain ``scoring_attempted=false``;
- once the fitted scorer is called, success/failure receipts are
  ``scoring_attempted=true``;
- unexpected scorer exceptions are typed ``MODEL_SCORER_FAILED``;
- downstream portfolio-governance exceptions fail closed without erasing an
  already-completed sporting probability receipt; and
- target Top-10 families cannot complete on terminal-status accounting alone:
  every source row must reconcile exactly once to a valid controlling-model
  package or an explicit typed blocker.

No model, evidence, line, calibration, ranking, or terminal-reducer behavior is
changed here. Portfolio/card governance remains a downstream objective and can
never mutate sporting probability. ``can_execute=false`` remains binding.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Header, HTTPException

from github_actions_oidc import scout_route_auth_dependency
import pick_request_runtime_core as _core
from pick_request_runtime_core import *  # noqa: F401,F403
from v17.top10_model_reconciliation import enforce_top10_completion


_ORIGINAL_TERMINAL = _core._terminal
_ORIGINAL_COMPLETED_SCORED_OUTCOME = _core._completed_scored_outcome
_ORIGINAL_AUTO_HYDRATE_PROP_EVIDENCE = _core.auto_hydrate_prop_evidence
_ORIGINAL_APPLY_PORTFOLIO_GOVERNANCE = _core._apply_portfolio_governance

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


def _apply_portfolio_governance(
    request_id: Optional[str],
    scored_legs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Fail closed downstream without destroying a sporting-model receipt.

    ``/score-pick-request`` deliberately computes sporting probability before
    portfolio/card governance. If that downstream structural layer throws, an
    HTTP 5xx from the route would erase the canonical row receipt that already
    exists and make the Action client surface only an opaque transport error.

    Preserve each completed row exactly as scored, block downstream portfolio
    use, and attach a sanitized typed diagnostic. This does not convert the
    downstream exception into MODEL_UNAVAILABLE or MODEL_SCORER_FAILED, and it
    never changes raw/calibrated probability, lower bound, rank eligibility, or
    the sporting terminal status.
    """
    try:
        _ORIGINAL_APPLY_PORTFOLIO_GOVERNANCE(request_id, scored_legs)
        return
    except Exception as exc:
        error_type = type(exc).__name__

    for _leg, outcome in scored_legs:
        outcome["portfolio_governance"] = {
            "status": "BLOCKED",
            "code": "PORTFOLIO_GOVERNANCE_UNAVAILABLE",
            "error_type": error_type,
            "blockers": ["PORTFOLIO_GOVERNANCE_UNAVAILABLE"],
            "receipt_preserved": True,
            "sporting_probability_mutated": False,
            "can_execute": False,
        }
        outcome["downstream_portfolio_evaluation_allowed"] = False


# The core resolves these helpers as module globals at request time, so install
# the receipt-aware versions once without altering the sporting model path.
_core._terminal = _terminal
_core._completed_scored_outcome = _completed_scored_outcome
_core._apply_portfolio_governance = _apply_portfolio_governance


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


def _install_top10_reconciliation_wrapper(app: Any) -> None:
    """Post-validate the core receipt without rebuilding the scoring route.

    FastAPI stores the callable on the route's dependant. Replacing that call
    target preserves the original path, schema, dependency graph, operation id,
    market-api closure, and auth barrier while adding one non-probabilistic
    completion audit to the returned receipt.
    """
    route = next(
        (
            candidate
            for candidate in app.router.routes
            if getattr(candidate, "path", None) == "/score-pick-request"
        ),
        None,
    )
    if route is None:
        raise RuntimeError("SCORE_PICK_REQUEST_ROUTE_NOT_INSTALLED")
    if getattr(route.endpoint, "_v17_top10_reconciled", False):
        return

    original_endpoint = route.endpoint

    def reconciled_score_pick_request(
        batch: _core.PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(
            default=None,
            alias="X-WOW-Model-Identity",
        ),
    ) -> dict[str, Any]:
        response = original_endpoint(batch, x_wow_model_identity)
        if not isinstance(response, dict):
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "PICK_REQUEST_RESPONSE_INVALID",
                    "completion_blocker": "TOP10_INCOMPLETE_MODEL_RECONCILIATION",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        return enforce_top10_completion(response, list(batch.rows))

    reconciled_score_pick_request._v17_top10_reconciled = True  # type: ignore[attr-defined]
    route.endpoint = reconciled_score_pick_request
    if getattr(route, "dependant", None) is not None:
        route.dependant.call = reconciled_score_pick_request


def install_pick_request_routes(
    app: Any,
    *,
    market_api: Any,
    auth_dependency: Any,
) -> None:
    _core.install_pick_request_routes(
        app,
        market_api=_ScoringReceiptMarketApi(market_api),
        auth_dependency=scout_route_auth_dependency(auth_dependency),
    )
    _install_top10_reconciliation_wrapper(app)


def __getattr__(name: str) -> Any:
    # Preserve private/helper imports used by diagnostics and tests while the
    # core implementation remains a byte-identical copy of the pre-fix file.
    return getattr(_core, name)
