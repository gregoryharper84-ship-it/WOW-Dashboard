"""V17 MLB team/event handoff + probability-preservation repair.

Repairs coupled orchestration defects without relaxing governance:
1. A completed fitted MLB sporting probability must not be erased merely because
   downstream LLP publication/ranking governance is held.
2. The score->LLP bridge must materialize the canonical evidence/source-attempt/
   scoring-evidence rows consumed by the event gates before a final decision.
3. Probability-only winner/BEST_SIDE intent is passed explicitly so sporting
   probability publication can remain separate from downstream market/value work.
4. Weather/environment evidence is acquired through the shared V17 environmental
   provider and written to the same canonical evidence ledger used by LLP.
5. A valid projected-lineup fitted package remains a sporting probability while
   rank/final publication is held for confirmation refresh.
6. A verified market favorite may be annotated with the cross-sport upset-alert
   interpretation layer after a complete calibrated sporting package exists.

Market-relative FAVORITE/UNDERDOG/UPSET requests remain on the existing market
consensus path. The upset alert uses market context only to identify which outcome
is the market favorite; market probability magnitude never changes sporting
probability or alert severity. No probability is manufactured, no gate is
bypassed, and wager execution remains impossible.
"""
from __future__ import annotations

from threading import RLock
from typing import Any

from fastapi import FastAPI

from v17 import team_event_request_runtime as _base
from v17.projected_lineup_scenario_modeling import projected_probability_hold
from v17.team_event_upset_alert import evaluate_favorite_upset_alert

_original_hold = _base._llp_governance_hold
_original_run_mlb_llp_governance = _base._run_mlb_llp_governance
_repair_lock = RLock()
_PROBABILITY_ONLY_INTENTS = {"WINNER", "BEST_SIDE"}
_UPSET_ALERT_NUMERIC_FIELDS = (
    "calibrated_home_probability",
    "calibrated_home_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_probability",
    "calibrated_away_lower_bound",
    "calibrated_away_upper_bound",
)


def _preserve_completed_probability_hold(
    req: Any,
    route: Any,
    model_result: dict[str, Any],
    *,
    governance_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed LLP hold without destroying completed model output."""
    projected = projected_probability_hold(req, model_result, governance_detail)
    if projected is not None:
        projected.update({
            "requester_host_identity": route.requester_host_identity,
            "controlling_engine_identity": _base.LLP_TEAM_BETTING_ENGINE,
            "candidate_family": route.candidate_family,
            "sporting_probability_completed": True,
            "sporting_probability_status": "COMPLETED_HELD_LINEUP_CONFIRMATION",
            "probability_fields_withheld": False,
            "host_terminal_authority": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        })
        return projected

    safe_model = dict(model_result)
    numeric_fields_present = sorted(_base._MLB_NUMERIC_MODEL_FIELDS.intersection(model_result))
    sporting_probability_completed = bool(numeric_fields_present)

    blockers = [
        "LLP_PROBABILITY_CLAIM_AUDIT_NOT_PROVEN",
        "LLP_EVENT_DECISION_GOVERNOR_NOT_PROVEN",
        "V17_EVENT_LEDGER_LINK_NOT_PROVEN",
    ]
    if governance_detail and governance_detail.get("blockers"):
        blockers.extend(str(value) for value in governance_detail.get("blockers") or [])

    return {
        **safe_model,
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "upstream_model_code": model_result.get("code"),
        "requester_host_identity": route.requester_host_identity,
        "controlling_engine_identity": _base.LLP_TEAM_BETTING_ENGINE,
        "candidate_family": route.candidate_family,
        "llp_governance": governance_detail or {"status": "NOT_PROVEN"},
        "llp_probability_audit_result": "NOT_PROVEN",
        "llp_event_decision": "NOT_PROVEN",
        "event_mutex_status": "NOT_PROVEN",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "blockers": sorted(set(blockers)),
        "sporting_probability_completed": sporting_probability_completed,
        "sporting_probability_status": (
            "COMPLETED_HELD_DOWNSTREAM" if sporting_probability_completed else "NOT_COMPLETED"
        ),
        "probability_fields_withheld": not sporting_probability_completed,
        "probability_publishable": False,
        "rank_eligible": False,
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "terminal_reducer_input": {
            "status": (governance_detail or {}).get("status", "NOT_PROVEN"),
            "terminal_output": "MODEL_QUALIFIED_HOLD",
            "global_terminal_reducer": (governance_detail or {}).get(
                "global_terminal_reducer", "V17_TERMINAL_REDUCER"
            ),
        },
        "can_execute": False,
    }


def _run_mlb_llp_governance_with_evidence_handoff(
    req: Any,
    route: Any,
    model_result: dict[str, Any],
    envelope: Any | None = None,
    *,
    event_api: Any,
) -> dict[str, Any]:
    """Run governance, hydrate shared + canonical evidence/model metadata, replay once."""
    first = _original_run_mlb_llp_governance(
        req, route, model_result, envelope=envelope, event_api=event_api
    )
    if first.get("probability_publishable") is True:
        return first

    decision_intent = str(getattr(req, "decision_intent", "BEST_SIDE")).upper()
    if decision_intent not in _PROBABILITY_ONLY_INTENTS:
        return first

    governance = first.get("llp_governance")
    if not isinstance(governance, dict):
        return first
    event_prediction_id = governance.get("event_prediction_id")
    score_snapshot_id = (
        governance.get("score_snapshot_id")
        or model_result.get("score_snapshot_id")
        or model_result.get("base_score_snapshot_id")
    )
    get_client = getattr(event_api, "get_client", None)
    if not event_prediction_id or not score_snapshot_id or not callable(get_client):
        return first

    client = get_client()
    environmental: dict[str, Any] | None = None
    try:
        environmental_result = client.rpc(
            "wow_v17_hydrate_shared_environmental_evidence",
            {
                "p_event_prediction_id": str(event_prediction_id),
                "p_score_snapshot_id": str(score_snapshot_id),
            },
        ).execute()
        if isinstance(environmental_result.data, dict):
            environmental = environmental_result.data
    except Exception as exc:
        environmental = {
            "status": "UNAVAILABLE",
            "code": "SHARED_ENVIRONMENTAL_EVIDENCE_PROVIDER_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "probability_publishable": False,
            "can_execute": False,
        }

    evidence = dict(getattr(req, "sport_specific_evidence", None) or {})
    try:
        hydration_result = client.rpc(
            "wow_v17_hydrate_mlb_event_governance_evidence",
            {
                "p_event_prediction_id": str(event_prediction_id),
                "p_score_snapshot_id": str(score_snapshot_id),
                "p_evidence": evidence,
                "p_decision_intent": decision_intent,
            },
        ).execute()
        hydration = hydration_result.data
    except Exception as exc:
        out = dict(first)
        out["shared_environmental_evidence"] = environmental
        out["evidence_handoff_repair"] = {
            "status": "UNAVAILABLE",
            "error_type": type(exc).__name__,
            "can_execute": False,
        }
        out["blockers"] = sorted(set([
            *(out.get("blockers") or []),
            "V17_EVENT_EVIDENCE_HANDOFF_REPAIR_UNAVAILABLE",
        ]))
        return out

    if not isinstance(hydration, dict) or hydration.get("status") != "PASS":
        out = dict(first)
        out["shared_environmental_evidence"] = environmental
        out["evidence_handoff_repair"] = hydration if isinstance(hydration, dict) else {
            "status": "INVALID_RESPONSE",
            "can_execute": False,
        }
        out["blockers"] = sorted(set([
            *(out.get("blockers") or []),
            "V17_EVENT_EVIDENCE_HANDOFF_REPAIR_NOT_PASS",
        ]))
        return out

    second = _original_run_mlb_llp_governance(
        req, route, model_result, envelope=envelope, event_api=event_api
    )
    second["shared_environmental_evidence"] = environmental
    second["evidence_handoff_repair"] = hydration
    second["governance_replayed_after_evidence_handoff"] = True
    second["can_execute"] = False
    return second


def _market_favorite_identity(req: Any) -> tuple[str | None, bool, str]:
    """Resolve favorite identity from a verified exact market snapshot only."""
    market = _base._market_handoff(req)
    if market.get("status") != "EXACT_LINE" or market.get("market_role_status") != "ACTIVE":
        return None, False, "MARKET_FAVORITE_CLASSIFICATION_UNVERIFIED"
    prior = dict(getattr(req, "market_prior", None) or {})
    try:
        home = float(prior["home_probability"])
        away = float(prior["away_probability"])
    except (KeyError, TypeError, ValueError):
        return None, False, "MARKET_FAVORITE_CLASSIFICATION_UNVERIFIED"
    if not (0.0 <= home <= 1.0 and 0.0 <= away <= 1.0):
        return None, False, "MARKET_FAVORITE_CLASSIFICATION_INVALID"
    if home == away:
        return None, False, "MARKET_FAVORITE_TIE_UNRESOLVED"
    return (req.home_team if home > away else req.away_team), True, "MARKET_FAVORITE_VERIFIED"


def _unavailable_upset_alert(result: dict[str, Any], reason: str) -> dict[str, Any]:
    out = dict(result)
    payload = {
        "status": "UPSET_ALERT_UNAVAILABLE",
        "alert": False,
        "severity": "UNAVAILABLE",
        "sport": str(out.get("sport") or "MLB").upper(),
        "market_favorite": None,
        "upset_candidate": None,
        "reason_codes": [reason],
        "market_role_only": True,
        "probability_mutated": False,
        "admission_mutated": False,
        "cash_gate_mutated": False,
        "automatic_pick_promotion": False,
        "can_execute": False,
    }
    out["upset_alert"] = payload
    out["upset_alert_status"] = payload["status"]
    out["upset_alert_severity"] = payload["severity"]
    out["upset_alert_candidate"] = None
    return out


def _attach_upset_alert(req: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Attach an informational favorite-vulnerability flag without changing scoring."""
    out = dict(result)
    market_favorite, favorite_verified, market_reason = _market_favorite_identity(req)
    if not favorite_verified or market_favorite is None:
        return _unavailable_upset_alert(out, market_reason)

    if out.get("probability_fields_withheld") is True:
        return _unavailable_upset_alert(out, "GOVERNED_COMPLETE_OUTCOME_SPACE_WITHHELD")
    if out.get("calibration_health_status") != "PASS":
        return _unavailable_upset_alert(out, "GOVERNED_CALIBRATION_NOT_PASS")
    if any(out.get(field) is None for field in _UPSET_ALERT_NUMERIC_FIELDS):
        return _unavailable_upset_alert(out, "GOVERNED_COMPLETE_OUTCOME_SPACE_UNAVAILABLE")

    try:
        alert = evaluate_favorite_upset_alert(
            sport=str(getattr(req, "sport", "MLB") or "MLB"),
            market_favorite=market_favorite,
            market_favorite_verified=True,
            governed_outcomes=(
                {
                    "label": req.home_team,
                    "calibrated_probability": out["calibrated_home_probability"],
                    "calibrated_lower_bound": out["calibrated_home_lower_bound"],
                    "calibrated_upper_bound": out["calibrated_home_upper_bound"],
                },
                {
                    "label": req.away_team,
                    "calibrated_probability": out["calibrated_away_probability"],
                    "calibrated_lower_bound": out["calibrated_away_lower_bound"],
                    "calibrated_upper_bound": out["calibrated_away_upper_bound"],
                },
            ),
            favorite_failure_path_probability_if_modeled=out.get(
                "favorite_failure_path_probability_if_modeled",
                out.get("favorite_failure_path_probability"),
            ),
            largest_favorite_loss_path=out.get("largest_favorite_loss_path"),
            underdog_upset_path=out.get("underdog_upset_path_json", out.get("underdog_upset_path")),
        )
    except (KeyError, TypeError, ValueError):
        return _unavailable_upset_alert(out, "GOVERNED_UPSET_ALERT_PACKAGE_INVALID")

    payload = alert.to_dict()
    out["upset_alert"] = payload
    out["upset_alert_status"] = alert.status
    out["upset_alert_severity"] = alert.severity
    out["market_favorite"] = alert.market_favorite
    out["market_favorite_model_probability"] = alert.favorite_probability
    out["market_favorite_lower_bound"] = alert.favorite_lower_bound
    out["upset_alert_candidate"] = alert.upset_candidate
    out["upset_candidate_model_probability"] = alert.upset_candidate_probability
    out["upset_candidate_lower_bound"] = alert.upset_candidate_lower_bound
    out["upset_alert_probability_gap"] = alert.probability_gap
    out["can_execute"] = False
    return out


def score_team_event_request(
    req: Any,
    *,
    event_api: Any,
    canonical_hydration_required: bool = False,
) -> dict[str, Any]:
    """Execute the base V17 scorer with repair helpers scoped to this call."""
    with _repair_lock:
        previous_hold = _base._llp_governance_hold
        previous_governance = _base._run_mlb_llp_governance
        _base._llp_governance_hold = _preserve_completed_probability_hold
        _base._run_mlb_llp_governance = _run_mlb_llp_governance_with_evidence_handoff
        try:
            result = _base.score_team_event_request(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
            return _attach_upset_alert(req, result)
        finally:
            _base._llp_governance_hold = previous_hold
            _base._run_mlb_llp_governance = previous_governance


def install_team_event_routes(app: FastAPI, *, event_api: Any, auth_dependency: Any) -> None:
    """Install the active V17 team/event route through the scoped repair wrapper."""
    @app.post("/score-team-event", dependencies=[auth_dependency], operation_id="scoreWowTeamEvent")
    def score_team_event(req: _base.TeamEventRequest):
        return score_team_event_request(
            req,
            event_api=event_api,
            canonical_hydration_required=True,
        )


TeamEventRequest = _base.TeamEventRequest
TeamEventCapabilityResponse = _base.TeamEventCapabilityResponse
normalize_team_event_sport = _base.normalize_team_event_sport

__all__ = [
    "TeamEventRequest",
    "TeamEventCapabilityResponse",
    "score_team_event_request",
    "install_team_event_routes",
    "normalize_team_event_sport",
]
