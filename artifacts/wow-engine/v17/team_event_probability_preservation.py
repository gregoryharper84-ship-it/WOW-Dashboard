"""V17 team/event probability preservation shim.

This module repairs one narrow host-orchestration defect: a completed fitted MLB
sporting probability must not be erased merely because downstream LLP
publication/ranking governance is held.  It does not relax any governance gate,
does not make held rows rank eligible, and never enables execution.

The active team-event runtime is patched at import time so every existing caller
(including callers that imported ``score_team_event_request`` earlier) continues
to execute the same scorer and terminal-governance path while using the repaired
hold serializer.
"""
from __future__ import annotations

from typing import Any

from v17 import team_event_request_runtime as _base


def _preserve_completed_probability_hold(
    req: Any,
    route: Any,
    model_result: dict[str, Any],
    *,
    governance_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed LLP hold without destroying completed model output.

    Numeric fields are preserved only when the fitted scorer actually returned
    them.  No probability is synthesized, inferred from market data, or promoted
    to rank/publication eligibility here.
    """
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
        # A completed fitted probability remains visible as sporting evidence,
        # while official publication/ranking stays fail-closed.
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


# Patch the producing module global, not merely a re-exported function. Existing
# score_team_event_request references therefore pick up the repaired serializer
# when _run_mlb_llp_governance resolves _llp_governance_hold at runtime.
_base._llp_governance_hold = _preserve_completed_probability_hold

TeamEventRequest = _base.TeamEventRequest
TeamEventCapabilityResponse = _base.TeamEventCapabilityResponse
score_team_event_request = _base.score_team_event_request
install_team_event_routes = _base.install_team_event_routes
normalize_team_event_sport = _base.normalize_team_event_sport

__all__ = [
    "TeamEventRequest",
    "TeamEventCapabilityResponse",
    "score_team_event_request",
    "install_team_event_routes",
    "normalize_team_event_sport",
]
