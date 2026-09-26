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
never mutate sporting probability. An explicit downstream money-evaluation hold
blocks card/final-money admission without suppressing independent portfolio or
dependence analysis, sporting publication, or probability ranking.
``can_execute=false`` remains binding.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Header, HTTPException

from github_actions_oidc import scout_route_auth_dependency
import pick_request_runtime_core as _core
from pick_request_runtime_core import *  # noqa: F401,F403
from prop_auto_hydration_router import (
    auto_hydrate_prop_evidence as _sport_aware_auto_hydrate_prop_evidence,
    hydration_request_context,
    provider_for_sport,
)
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
_ORIGINAL_VALIDATE_EVIDENCE = _core._validate_evidence

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


def _validate_evidence(row: Any, canonical_stat: str) -> dict[str, Any]:
    """Add canonical-event binding verification to the historical validator.

    NFL automatic hydration now carries a provider-derived nflverse canonical
    event id. The request may not self-attest a different canonical identity:
    fail before immutable evidence persistence or specialist scoring.
    """
    normalized = _ORIGINAL_VALIDATE_EVIDENCE(row, canonical_stat)
    evidence = getattr(row, "evidence", None)
    role_status = getattr(evidence, "role_status", None) if evidence is not None else None
    role = role_status if isinstance(role_status, dict) else {}
    bound_event_id = str(role.get("canonical_event_id") or "").strip()
    request_event_id = str(normalized.get("event_id") or "").strip()
    if bound_event_id and bound_event_id != request_event_id:
        raise ValueError("PROP_EVENT_IDENTITY_CONFLICT:CANONICAL_EVENT_ID_MISMATCH")

    if str(normalized.get("sport") or "").strip().upper() == "NFL":
        aliases = role.get("provider_event_ids")
        espn_alias = (
            str(aliases.get("ESPN") or "").strip()
            if isinstance(aliases, dict)
            else ""
        )
        provider_backed = bool(
            espn_alias
            or str(role.get("espn_athlete_id") or "").strip()
            or str(role.get("status") or "").strip().upper() == "ACTIVE_CURRENT_ESPN_ROSTER"
        )
        verified_event_id = str(role.get("verified_canonical_event_id") or "").strip()
        if provider_backed and not verified_event_id:
            raise ValueError("PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_UNVERIFIED")
        if verified_event_id and verified_event_id != request_event_id:
            raise ValueError("PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_MISMATCH")

    identity_status = str(role.get("identity_binding_status") or "").strip().upper()
    if identity_status and identity_status not in {"PASS", "PROVIDER_IDENTITY_ONLY"}:
        raise ValueError("PROP_EVENT_IDENTITY_CONFLICT:IDENTITY_BINDING_NOT_PASS")
    return normalized


_core._validate_evidence = _validate_evidence


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


def _prediction_id(outcome: dict[str, Any]) -> str | None:
    result = outcome.get("result")
    if not isinstance(result, dict):
        return None
    prediction = result.get("prediction")
    if not isinstance(prediction, dict):
        return None
    value = prediction.get("prediction_id")
    text = str(value or "").strip()
    return text or None


def _apply_portfolio_governance(
    request_id: Optional[str],
    scored_legs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Fail closed for card admission without mutating sporting probability.

    Portfolio/dependence governance remains separate from sporting probability
    and separate from payout economics. Rank/publication/terminal failures can
    block portfolio continuation as before. A completed row that explicitly
    reports ``downstream_money_evaluation_allowed=false`` receives a card-only
    hold: portfolio/dependence analysis may still run, while EV/money/card/final
    approval remains blocked.

    The emitted card-admission receipt binds the governed prediction id to the
    exact event/player/stat/line/direction sent through ``/score-pick-request``.
    A later line/direction change therefore requires a fresh scoring receipt;
    this layer never treats an adjacent line as the same thesis.
    """
    try:
        _ORIGINAL_APPLY_PORTFOLIO_GOVERNANCE(request_id, scored_legs)
    except Exception as exc:
        error_type = type(exc).__name__
        for leg, outcome in scored_legs:
            blocker = "PORTFOLIO_GOVERNANCE_UNAVAILABLE"
            outcome["portfolio_governance"] = {
                "status": "BLOCKED",
                "code": blocker,
                "error_type": error_type,
                "blockers": [blocker],
                "receipt_preserved": True,
                "sporting_probability_mutated": False,
                "can_execute": False,
            }
            outcome["downstream_portfolio_evaluation_allowed"] = False
            outcome["card_admission_eligible"] = False
            outcome["card_admission_blockers"] = [blocker]
            outcome["card_admission_receipt"] = {
                "prediction_id": _prediction_id(outcome),
                "event_id": leg.get("event_id"),
                "participant": leg.get("player"),
                "market_stat": leg.get("prop_type"),
                "exact_line": leg.get("line"),
                "direction": leg.get("direction"),
                "rank_eligible": outcome.get("rank_eligible") is True,
                "probability_publishable": outcome.get("probability_publishable") is True,
                "money_evaluation_allowed": outcome.get("downstream_money_evaluation_allowed") is True,
                "portfolio_eligible": False,
                "can_execute": False,
            }
        return

    for leg, outcome in scored_legs:
        portfolio_upstream_blockers: list[str] = []
        if outcome.get("rank_eligible") is not True:
            portfolio_upstream_blockers.append("CARD_ADMISSION:RANK_INELIGIBLE")
        if outcome.get("probability_publishable") is not True:
            portfolio_upstream_blockers.append("CARD_ADMISSION:PROBABILITY_NOT_PUBLISHABLE")
        if outcome.get("terminal_status") != "COMPLETED":
            portfolio_upstream_blockers.append("CARD_ADMISSION:TERMINAL_NOT_COMPLETED")
        if outcome.get("pick_rejected") is True:
            portfolio_upstream_blockers.append("CARD_ADMISSION:TERMINAL_REJECTED")

        card_only_blockers: list[str] = []
        if outcome.get("downstream_money_evaluation_allowed") is False:
            card_only_blockers.append("CARD_ADMISSION:MONEY_EVALUATION_HELD")

        governance = outcome.get("portfolio_governance")
        if not isinstance(governance, dict):
            governance = {
                "status": "BLOCKED",
                "code": "PORTFOLIO_GOVERNANCE_MISSING",
                "blockers": ["PORTFOLIO_GOVERNANCE_MISSING"],
                "sporting_probability_mutated": False,
                "can_execute": False,
            }
            outcome["portfolio_governance"] = governance
            outcome["downstream_portfolio_evaluation_allowed"] = False

        portfolio_allowed = outcome.get("downstream_portfolio_evaluation_allowed") is True
        portfolio_blockers = [str(item) for item in governance.get("blockers") or []]
        if not portfolio_allowed and not portfolio_blockers:
            portfolio_blockers.append("CARD_ADMISSION:PORTFOLIO_NOT_QUALIFIED")

        if portfolio_upstream_blockers:
            outcome["downstream_portfolio_evaluation_allowed"] = False
            governance["status"] = "BLOCKED"
            governance["portfolio_qualification"] = "HELD_FOR_UPSTREAM_ELIGIBILITY"
            governance["card_admission_blocked"] = True
            governance["blockers"] = list(dict.fromkeys([*portfolio_blockers, *portfolio_upstream_blockers]))
            governance["sporting_probability_mutated"] = False
            governance["can_execute"] = False
            portfolio_allowed = False
            portfolio_blockers = list(governance["blockers"])

        card_blockers = list(dict.fromkeys([
            *portfolio_upstream_blockers,
            *card_only_blockers,
            *portfolio_blockers,
        ]))
        outcome["card_admission_eligible"] = bool(not card_blockers and portfolio_allowed)
        outcome["card_admission_blockers"] = card_blockers
        outcome["card_admission_receipt"] = {
            "prediction_id": _prediction_id(outcome),
            "event_id": leg.get("event_id"),
            "participant": leg.get("player"),
            "market_stat": leg.get("prop_type"),
            "exact_line": leg.get("line"),
            "direction": leg.get("direction"),
            "rank_eligible": outcome.get("rank_eligible") is True,
            "probability_publishable": outcome.get("probability_publishable") is True,
            "money_evaluation_allowed": outcome.get("downstream_money_evaluation_allowed") is True,
            "portfolio_eligible": portfolio_allowed,
            "can_execute": False,
        }


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


def _normalize_hydration_provider_receipts(response: dict[str, Any], batch: Any) -> None:
    """Correct legacy core telemetry without changing any terminal semantics."""
    if str(response.get("response_mode") or "FULL").upper() == "COMPACT":
        return
    rows = response.get("rows")
    if not isinstance(rows, list):
        return
    for request_row, outcome in zip(batch.rows, rows):
        if not isinstance(outcome, dict):
            continue
        acquisition = outcome.get("acquisition")
        if not isinstance(acquisition, dict) or acquisition.get("mode") != "AUTO_HYDRATION":
            continue
        canonical_stat = _core._canonical_stat(request_row.sport, request_row.stat_type)
        acquisition["provider"] = provider_for_sport(request_row.sport, canonical_stat)


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
        # The producing core predates canonical-event propagation in its
        # hydration function call. Bind the request rows for the duration of the
        # call so fallback hydration receives the same event/opponent identity as
        # interactive prehydration without changing probability/model behavior.
        with hydration_request_context(batch.rows):
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
        _normalize_hydration_provider_receipts(response, batch)
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