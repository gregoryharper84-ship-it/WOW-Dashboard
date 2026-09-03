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

Market-relative FAVORITE/UNDERDOG/UPSET requests remain on the existing market
consensus path. No probability is manufactured, no gate is bypassed, and wager
execution remains impossible.
"""
from __future__ import annotations

from threading import RLock
from typing import Any

from fastapi import FastAPI

from v17 import team_event_request_runtime as _base

_original_hold = _base._llp_governance_hold
_original_run_mlb_llp_governance = _base._run_mlb_llp_governance
_repair_lock = RLock()
_PROBABILITY_ONLY_INTENTS = {"WINNER", "BEST_SIDE"}


def _preserve_completed_probability_hold(
    req: Any,
    route: Any,
    model_result: dict[str, Any],
    *,
    governance_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed LLP hold without destroying completed model output."""
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
        # Market-relative requests must remain on the legacy consensus path.
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
            return _base.score_team_event_request(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
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
