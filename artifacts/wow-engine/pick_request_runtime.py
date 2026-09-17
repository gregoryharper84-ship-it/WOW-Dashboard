"""V17 receipt-semantics facade for the governed prop request runtime.

The producing implementation is preserved in ``pick_request_runtime_core``.
This facade adds receipt/error-boundary and Top-10 completion semantics:
- pre-scorer construction failures remain ``specialist_scoring_attempted=false``;
- once the fitted scorer is called, success/failure receipts are
  ``specialist_scoring_attempted=true``;
- unexpected scorer exceptions are typed ``MODEL_SCORER_FAILED``;
- downstream portfolio-governance exceptions fail closed without erasing an
  already-completed sporting probability receipt; and
- target Top-10 families cannot complete on terminal-status accounting alone:
  every source row must reconcile exactly once to a valid controlling-model
  package or an explicit typed blocker.

The facade also installs the reviewed sport-aware hydration router. This does
not grant model authority: specialist routing and exact production-artifact
preflight still own publishable scoring. A narrowly declared Fantasy Score
research candidate may clear only the evidence-acquisition preflight so the
system can collect immutable forward evidence needed for later calibration;
that compatibility path remains nonpublishable and non-rankable.

``specialist_scoring_attempted`` is this layer's unambiguous name for "the
controlling specialist scorer was invoked". It is deliberately distinct from the
host contract's ``action_invocation_attempted`` ("a required Action call
occurred"), because reporting the backend fact under the host's
``scoring_attempted`` name made a row that terminated before the scorer ran read
as though no Action call had happened at all. ``scoring_attempted`` is retained
here as a backward-compatible alias of the backend fact for existing consumers.

No model, evidence, line, calibration, ranking, or terminal-reducer behavior is
weakened here. Portfolio/card governance remains a downstream objective and can
never mutate sporting probability. ``can_execute=false`` remains binding.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Header, HTTPException

from github_actions_oidc import scout_route_auth_dependency
import pick_request_runtime_core as _core
from pick_request_runtime_core import *  # noqa: F401,F403
from prop_auto_hydration_router import auto_hydrate_prop_evidence as _sport_aware_auto_hydrate_prop_evidence
from v17.fantasy_score_pick_request_bridge import (
    research_candidate_outcome as _fantasy_research_candidate_outcome,
    research_candidate_preflight as _fantasy_research_candidate_preflight,
)
from v17.prediction_receipt_lookup_runtime import install_prediction_receipt_lookup_route
from v17.top10_model_reconciliation import enforce_top10_completion
from v17.mlb_1ip_line_expansion_maintenance import install_mlb_1ip_line_expansion_maintenance_route
from v17.wnba_prop_candidate_registry import install_wnba_prop_candidate_registration_route


_ORIGINAL_TERMINAL = _core._terminal
_ORIGINAL_COMPLETED_SCORED_OUTCOME = _core._completed_scored_outcome
_ORIGINAL_AUTO_HYDRATE_PROP_EVIDENCE = _core.auto_hydrate_prop_evidence
_ORIGINAL_APPLY_PORTFOLIO_GOVERNANCE = _core._apply_portfolio_governance

# Preserve the source-level exact-line contract used by V17 certification:
# frozen snapshot contains `"line": float(row.line)` and the score request
# contains `"line": row.line`; the request threshold is never overwritten.

# Normalize common WNBA board labels before specialist/artifact lookup. These
# aliases only resolve stat identity; they never grant model support.
_core.PROP_STAT_ALIASES.update(
    {
        ("WNBA", "PTS"): "POINTS",
        ("WNBA", "POINT"): "POINTS",
        ("WNBA", "REB"): "REBOUNDS",
        ("WNBA", "REBOUND"): "REBOUNDS",
        ("WNBA", "AST"): "ASSISTS",
        ("WNBA", "ASSIST"): "ASSISTS",
        ("WNBA", "3PM"): "THREE_POINTERS_MADE",
        ("WNBA", "3PT_MADE"): "THREE_POINTERS_MADE",
        ("WNBA", "3_PT_MADE"): "THREE_POINTERS_MADE",
        ("WNBA", "THREES_MADE"): "THREE_POINTERS_MADE",
        ("WNBA", "THREE_POINTERS"): "THREE_POINTERS_MADE",
    }
)

# The star import above intentionally preserves the historical public monkeypatch
# seam. Point that seam at the sport-aware router by default; tests/diagnostics
# may still replace it after installation.
auto_hydrate_prop_evidence = _sport_aware_auto_hydrate_prop_evidence


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
        _sport_aware_auto_hydrate_prop_evidence,
    )
    if current is _auto_hydrate_prop_evidence_delegate:
        current = _sport_aware_auto_hydrate_prop_evidence
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

    if code == "ROW_SCORING_UNAVAILABLE" and not scoring_attempted:
        normalized_code = "ROW_SCORING_INVALID_REQUEST"
        payload.setdefault("original_code", code)
        payload.setdefault("specialist_invoked", False)
        payload.setdefault("terminal_label", "MODEL_INPUTS_INSUFFICIENT")

    out = _ORIGINAL_TERMINAL(
        row_key,
        status,
        normalized_code,
        detail=payload,
        snapshot_id=snapshot_id,
        acquisition=acquisition,
    )
    out["specialist_scoring_attempted"] = scoring_attempted
    out["scoring_attempted"] = scoring_attempted
    return out


def _completed_scored_outcome(**kwargs: Any) -> dict[str, Any]:
    research_hold = _fantasy_research_candidate_outcome(**kwargs)
    if research_hold is not None:
        return research_hold
    out = _ORIGINAL_COMPLETED_SCORED_OUTCOME(**kwargs)
    out["specialist_scoring_attempted"] = True
    out["scoring_attempted"] = True
    return out


def _apply_portfolio_governance(
    request_id: Optional[str],
    scored_legs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Fail closed downstream without destroying a sporting-model receipt."""
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


_core._terminal = _terminal
_core._completed_scored_outcome = _completed_scored_outcome
_core._apply_portfolio_governance = _apply_portfolio_governance


class _ScoringReceiptMarketApi:
    """Transparent market-api proxy that types post-invocation failures."""

    def __init__(self, wrapped: Any):
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def _prop_route_artifact(self, sport: str, stat_type: str) -> dict[str, Any]:
        """Keep production readiness authoritative while permitting evidence-only candidates.

        The producing core currently has a binary certified/not-ready preflight.
        For the exact declared Fantasy Score research route, an explicitly active
        non-promoted candidate may pass only that acquisition gate. The original
        market API remains untouched, so the subsequent /score-prop bridge still
        sees the true CANDIDATE lifecycle and cannot mistake it for certification.
        """
        route = self._wrapped._prop_route_artifact(sport, stat_type)
        if isinstance(route, dict) and route.get("ok") is True and route.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
            return route
        research = _fantasy_research_candidate_preflight(
            self._wrapped,
            sport,
            stat_type,
            route,
        )
        return research if research is not None else route

    def score_prop(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._wrapped.score_prop(*args, **kwargs)
        except HTTPException as exc:
            detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            detail["specialist_scoring_attempted"] = True
            detail["scoring_attempted"] = True
            detail.setdefault("specialist_invoked", True)
            raise HTTPException(status_code=exc.status_code, detail=detail, headers=exc.headers) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "MODEL_SCORER_FAILED",
                    "error_type": type(exc).__name__,
                    "specialist_scoring_attempted": True,
                    "scoring_attempted": True,
                    "specialist_invoked": True,
                },
            ) from exc


def _install_top10_reconciliation_wrapper(app: Any) -> None:
    """Post-validate the core receipt without rebuilding the scoring route."""
    route = next(
        (candidate for candidate in app.router.routes if getattr(candidate, "path", None) == "/score-pick-request"),
        None,
    )
    if route is None:
        raise RuntimeError("SCORE_PICK_REQUEST_ROUTE_NOT_INSTALLED")
    if getattr(route.endpoint, "_v17_top10_reconciled", False):
        return

    original_endpoint = route.endpoint

    def reconciled_score_pick_request(
        batch: _core.PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
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
    wrapped_auth = scout_route_auth_dependency(auth_dependency)
    _core.install_pick_request_routes(
        app,
        market_api=_ScoringReceiptMarketApi(market_api),
        auth_dependency=wrapped_auth,
    )
    _install_top10_reconciliation_wrapper(app)

    # Candidate registration is an internal control-plane route only. It stores
    # validated WNBA fitted-model artifacts as CANDIDATE rows and cannot promote,
    # activate, certify, publish probabilities, or execute wagers.
    prod = getattr(market_api, "prod", None)
    get_client_fn = getattr(prod, "get_client", None)
    if callable(get_client_fn):
        install_prediction_receipt_lookup_route(
            app,
            auth_dependency=wrapped_auth,
            db_client_fn=get_client_fn,
        )
        install_wnba_prop_candidate_registration_route(
            app,
            auth_dependency=auth_dependency,
            db_client_fn=get_client_fn,
        )
        install_mlb_1ip_line_expansion_maintenance_route(
            app,
            auth_dependency=auth_dependency,
            db_client_fn=get_client_fn,
        )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
