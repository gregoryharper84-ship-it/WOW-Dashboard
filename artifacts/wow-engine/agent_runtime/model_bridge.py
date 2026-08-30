"""MLB governed event-model bridge (packet section 12's controlling-model
contract, for the OUTRIGHT_WINNER lane specifically). Ported near-verbatim
from PR #33 (feature/wow-agent-runtime-v1) during the convergence pass —
this calls the existing server-owned api_g11 bridge; the worker never
accepts a numeric probability from its own envelope.
"""
from __future__ import annotations

from typing import Any, Callable

from agent_runtime.idempotency import compute_request_hash as canonical_hash

HELD_CODE = "REAL_FITTED_MODEL_PATH_PROVEN"
PUBLISHED_CODE = "GOVERNED_PROBABILITY_PUBLISHED"


def _event_request(payload: dict[str, Any]):
    """Build the existing server-owned event contract without accepting caller probabilities."""
    import api_g11

    request_payload = payload.get("event_request")
    if not isinstance(request_payload, dict):
        raise ValueError("EVENT_MODEL_REQUEST_MISSING")
    # ScoreEventRequest is the canonical production contract. Extra or
    # malformed fields fail validation here rather than being silently ignored.
    return api_g11.ScoreEventRequest.model_validate(request_payload)


def score_mlb_event_bridge(
    payload: dict[str, Any],
    *,
    bridge_fn: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Invoke and validate the existing governed MLB event bridge.

    The worker never accepts a numeric probability from its envelope. Numeric
    output is exposed only when the production bridge itself returns the
    separately ratified PUBLISHED code and api_g11 validates that payload.
    """
    import api_g11

    req = _event_request(payload)
    fn = bridge_fn or api_g11._bridge_rpc
    result = fn(req)
    if not isinstance(result, dict):
        raise RuntimeError("EVENT_MODEL_BRIDGE_INVALID_RESPONSE")

    code = result.get("code")
    if code not in {HELD_CODE, PUBLISHED_CODE}:
        return {
            "code": str(code or "MODEL_UNAVAILABLE"),
            "probability_publishable": False,
            "probability_fields_withheld": True,
            "can_execute": False,
            "bridge_result_hash": canonical_hash(result),
            "bridge_blockers": list(result.get("blockers") or [str(code or "MODEL_UNAVAILABLE")]),
        }

    api_g11._validate_bridge_payload(result)
    base = {
        "code": code,
        "model_version": result.get("model_version"),
        "model_artifact_id": result.get("model_artifact_id") or result.get("spec_id"),
        "model_inputs_hash": result.get("model_inputs_hash"),
        "scoring_evidence_produced": result.get("scoring_evidence_produced") is True,
        "probability_fields_withheld": result.get("probability_fields_withheld") is True,
        "probability_publishable": result.get("probability_publishable") is True,
        "bridge_result_hash": canonical_hash(result),
        "can_execute": False,
    }
    if code == HELD_CODE:
        # Deliberately do not copy any numeric probability fields in held mode.
        base["probability_publishable"] = False
        base["probability_fields_withheld"] = True
        return base

    numeric = {
        key: result[key]
        for key in (
            "raw_home_probability", "raw_away_probability",
            "calibrated_home_probability", "calibrated_away_probability",
            "calibrated_home_lower_bound", "calibrated_home_upper_bound",
            "calibrated_away_lower_bound", "calibrated_away_upper_bound",
            "projected_runs_home", "projected_runs_away", "tie_after_9_probability",
        )
        if key in result
    }
    return {**base, **numeric}
