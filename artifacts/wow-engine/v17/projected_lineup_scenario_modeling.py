"""V17 Projected-Lineup Scenario Modeling + Confirmation Refresh.

This adapter separates three questions that must not be conflated:
1. can the certified sporting model produce a valid probability?
2. how certain is that probability while the lineup is provisional?
3. can the row reach final/rank publication before confirmation refresh?

The current certified MLB event specialist already models contextual lineup regimes.
This adapter therefore does *not* invent alternate probabilities or arbitrary
scenario weights.  It preserves the fitted model's valid projected-lineup package,
adds explicit lineup uncertainty/refresh metadata, and keeps rank/final publication
held until the downstream confirmation contract is satisfied.  If a future fitted
specialist exposes an explicit lineup_scenario_mixture, the mixture is validated and
surfaced without changing the model's probability.

can_execute is always false.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

CAN_EXECUTE = False
PROJECTED_STATES = {"PROJECTED", "PROJECTED_HIGH_CONFIDENCE", "PROJECTED_MEDIUM_CONFIDENCE"}
CONFIRMED_STATES = {"CONFIRMED", "OFFICIAL_CONFIRMED"}
CONFLICT_STATES = {"MATERIAL_CONFLICT", "DATA_UNOBTAINABLE", "UNRESOLVED"}
_NUMERIC_FIELDS = {
    "raw_home_probability", "raw_away_probability",
    "independent_home_probability", "independent_away_probability",
    "calibrated_home_probability", "calibrated_away_probability",
    "calibrated_home_lower_bound", "calibrated_home_upper_bound",
    "calibrated_away_lower_bound", "calibrated_away_upper_bound",
    "projected_runs_home", "projected_runs_away", "tie_after_9_probability",
}


def normalize_lineup_state(value: Any) -> str:
    state = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "OFFICIAL": "CONFIRMED",
        "OFFICIAL_CONFIRMED": "CONFIRMED",
        "HIGH_CONFIDENCE_PROJECTED": "PROJECTED_HIGH_CONFIDENCE",
        "PROJECTED_HIGH": "PROJECTED_HIGH_CONFIDENCE",
        "MEDIUM_CONFIDENCE_PROJECTED": "PROJECTED_MEDIUM_CONFIDENCE",
        "PROJECTED_MEDIUM": "PROJECTED_MEDIUM_CONFIDENCE",
        "TBD": "UNRESOLVED",
        "UNKNOWN": "DATA_UNOBTAINABLE",
    }
    return aliases.get(state, state or "DATA_UNOBTAINABLE")


def classify_event_lineup(home: Any, away: Any) -> str:
    states = {normalize_lineup_state(home), normalize_lineup_state(away)}
    if states <= CONFIRMED_STATES:
        return "CONFIRMED"
    if states & {"MATERIAL_CONFLICT"}:
        return "MATERIAL_CONFLICT"
    if states & {"DATA_UNOBTAINABLE", "UNRESOLVED"}:
        return "DATA_UNOBTAINABLE"
    if "PROJECTED_MEDIUM_CONFIDENCE" in states:
        return "PROJECTED_MEDIUM_CONFIDENCE"
    if states & PROJECTED_STATES:
        return "PROJECTED_HIGH_CONFIDENCE"
    return "DATA_UNOBTAINABLE"


def valid_probability_package(payload: dict[str, Any]) -> bool:
    required = (
        "calibrated_home_probability", "calibrated_away_probability",
        "calibrated_home_lower_bound", "calibrated_away_lower_bound",
    )
    try:
        values = [float(payload[key]) for key in required]
    except (KeyError, TypeError, ValueError):
        return False
    return all(isfinite(value) and 0.0 <= value <= 1.0 for value in values)


def validate_scenario_mixture(value: Any) -> dict[str, Any]:
    """Validate model-emitted scenario weights; never manufacture missing ones."""
    if not isinstance(value, list) or not value:
        return {"status": "NOT_EXPOSED_BY_CONTROLLING_MODEL", "scenario_n": 0}
    weights: list[float] = []
    for item in value:
        if not isinstance(item, dict):
            return {"status": "MODEL_SCENARIO_MIXTURE_INVALID", "scenario_n": len(value)}
        try:
            weight = float(item.get("weight"))
        except (TypeError, ValueError):
            return {"status": "MODEL_SCENARIO_MIXTURE_INVALID", "scenario_n": len(value)}
        if not isfinite(weight) or weight < 0 or weight > 1:
            return {"status": "MODEL_SCENARIO_MIXTURE_INVALID", "scenario_n": len(value)}
        weights.append(weight)
    if abs(sum(weights) - 1.0) > 1e-6:
        return {"status": "MODEL_SCENARIO_MIXTURE_INVALID", "scenario_n": len(value), "weight_sum": sum(weights)}
    return {"status": "PASS", "scenario_n": len(value), "weight_sum": sum(weights), "scenarios": deepcopy(value)}


def projected_probability_hold(req: Any, model_result: dict[str, Any], governance_detail: dict[str, Any] | None) -> dict[str, Any] | None:
    evidence = dict(getattr(req, "sport_specific_evidence", None) or {})
    lineup_state = classify_event_lineup(evidence.get("home_lineup_status"), evidence.get("away_lineup_status"))
    if lineup_state not in {"PROJECTED_HIGH_CONFIDENCE", "PROJECTED_MEDIUM_CONFIDENCE"}:
        return None
    if not valid_probability_package(model_result):
        return None
    if model_result.get("probability_fields_withheld") is True:
        return None

    scenario_meta = validate_scenario_mixture(model_result.get("lineup_scenario_mixture"))
    if scenario_meta.get("status") == "MODEL_SCENARIO_MIXTURE_INVALID":
        return None

    result = dict(model_result)
    result.update({
        "code": "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
        "lineup_state": lineup_state,
        "lineup_confirmation_required": True,
        "final_refresh_required": True,
        "lineup_scenario_modeling": {
            "status": "MODEL_EMITTED_MIXTURE_VALIDATED" if scenario_meta.get("status") == "PASS" else "CERTIFIED_CONTEXTUAL_PROJECTED_LINEUP_MODEL",
            "mixture": scenario_meta,
            "scenario_weights_invented_by_governor": False,
        },
        "sporting_probability_publishable": True,
        "probability_publishable": True,
        "rank_eligible": False,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "qualification_ceiling_reason": "LINEUP_CONFIRMATION_PENDING",
        "llp_governance": governance_detail or {"status": "HOLD"},
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    })
    blockers = list(result.get("blockers") or [])
    blockers.append("LINEUP_CONFIRMATION_PENDING")
    result["blockers"] = sorted(set(str(x) for x in blockers))
    return result


def enrich_canonical_lineup_resolution(resolution: dict[str, Any], req: Any, *, event_api: Any) -> dict[str, Any]:
    """Upgrade canonical PROJECTED to CONFIRMED when the immutable ledger proves it.

    If confirmation is absent, a hydrated pregame snapshot with both probable
    starters is classified PROJECTED_HIGH_CONFIDENCE.  No batting order is guessed.
    """
    if resolution.get("ok") is not True:
        return resolution
    result = deepcopy(resolution)
    evidence = dict(result.get("evidence") or {})
    status = "PROJECTED_HIGH_CONFIDENCE"
    try:
        client_fn = getattr(event_api, "get_client", None)
        if callable(client_fn):
            rows = (
                client_fn().table("wow_mlb_forward_shadow_events")
                .select("lineup_status,lineup_snapshot_id,lineup_confirmed_at")
                .eq("official_event_id", str(req.official_event_id))
                .order("snapshot_timestamp", desc=True)
                .limit(1).execute().data or []
            )
            if rows:
                row = dict(rows[0])
                if normalize_lineup_state(row.get("lineup_status")) == "CONFIRMED" and row.get("lineup_snapshot_id"):
                    status = "CONFIRMED"
                    result["canonical_lineup_snapshot_id"] = str(row.get("lineup_snapshot_id"))
                    result["canonical_lineup_confirmed_at"] = row.get("lineup_confirmed_at")
    except Exception:
        # Evidence query failure cannot silently claim confirmation.  Preserve the
        # already-valid projected evidence and let final refresh try again later.
        status = "PROJECTED_HIGH_CONFIDENCE"

    evidence["home_lineup_status"] = status
    evidence["away_lineup_status"] = status
    evidence["lineup_uncertainty_treatment"] = "CONFIRMED_NORMAL" if status == "CONFIRMED" else "PROJECTED_CONTEXTUAL_MODEL_PLUS_CONFIRMATION_REFRESH"
    result["evidence"] = evidence
    result["lineup_state"] = status
    result["final_refresh_required"] = status != "CONFIRMED"
    return result


def install_projected_lineup_semantics() -> bool:
    """Install active-V17 adapters idempotently."""
    from v17 import mlb_team_event_hydration as hydration
    from v17 import team_event_request_runtime as runtime

    if getattr(runtime, "_v17_projected_lineup_installed", False):
        return True

    original_resolve = hydration.resolve_mlb_team_event_evidence
    original_hold = runtime._llp_governance_hold

    def resolve_wrapper(req: Any, *, event_api: Any):
        return enrich_canonical_lineup_resolution(original_resolve(req, event_api=event_api), req, event_api=event_api)

    def hold_wrapper(req: Any, route: Any, model_result: dict[str, Any], *, governance_detail: dict[str, Any] | None = None):
        projected = projected_probability_hold(req, model_result, governance_detail)
        if projected is not None:
            projected.update({
                "requester_host_identity": route.requester_host_identity,
                "controlling_engine_identity": getattr(runtime, "LLP_TEAM_BETTING_ENGINE", "LLP_TEAM_BETTING_ENGINE"),
                "candidate_family": route.candidate_family,
            })
            return projected
        return original_hold(req, route, model_result, governance_detail=governance_detail)

    hydration.resolve_mlb_team_event_evidence = resolve_wrapper
    runtime.resolve_mlb_team_event_evidence = resolve_wrapper
    runtime._llp_governance_hold = hold_wrapper
    runtime._v17_projected_lineup_installed = True
    return True
