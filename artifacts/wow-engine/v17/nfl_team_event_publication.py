"""Additive V17 NFL governed-probability publication bridge.

Installation wraps only NFL team-event requests. Every non-NFL request is
delegated to the exact pre-existing scorer, preserving MLB behavior.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from fastapi import HTTPException

from nfl_event_features_p2 import FEATURE_ORDER as NFL_FEATURE_ORDER, FEATURE_SCHEMA_VERSION as NFL_FEATURE_SCHEMA_VERSION
from nfl_event_model_v17 import (
    NFLModelInputsInsufficient,
    NFLModelOutputInvalid,
    NFLModelScorerFailed,
    NFLModelUnavailable,
    ensure_champion_model,
    load_champion_model,
)
from v17.nfl_team_event_specialist import resolve_nfl_team_event_evidence, score_nfl_team_event

NFL_ALIASES = frozenset({"NFL", "FOOTBALL_NFL"})
_PATCH_MARKER = "_v17_nfl_team_event_publication_installed"


def _nfl_sport(req: Any) -> bool:
    return str(getattr(req, "sport", "") or "").strip().upper() in NFL_ALIASES and str(
        getattr(req, "league", "") or ""
    ).strip().upper() == "NFL"


def _typed_failure(base: Any, req: Any, exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "MODEL_SCORER_FAILED")
    status = 409
    if code == "MODEL_SCORER_FAILED":
        status = 503
    elif code == "MODEL_OUTPUT_INVALID":
        status = 500
    return HTTPException(
        status_code=status,
        detail=base._augment_detail(
            {
                "code": code,
                "blocker_code": str(exc),
                "sport": "NFL",
                "failed_contract_scope": ["CONTROLLING_SPECIALIST"],
                "market_probability_substitution_allowed": False,
                "generic_reasoning_substitution_allowed": False,
                "probability_publishable": False,
                "can_execute": False,
            },
            req,
        ),
    )


def _canonicalize(req: Any, *, db: Any, base: Any) -> Any:
    try:
        resolution = resolve_nfl_team_event_evidence(req, db=db)
    except NFLModelInputsInsufficient as exc:
        raise _typed_failure(base, req, exc) from exc
    if resolution.get("ok") is not True:
        code = str(resolution.get("failure_code") or "MODEL_INPUTS_INSUFFICIENT")
        raise HTTPException(
            status_code=409,
            detail=base._augment_detail(
                {
                    "code": code,
                    "blocker_code": resolution.get("code"),
                    "missing_fields": list(resolution.get("missing_fields") or []),
                    "identity_mismatches": list(resolution.get("identity_mismatches") or []),
                    "canonical_acquisition_attempted": True,
                    "market_probability_substitution_allowed": False,
                    "generic_reasoning_substitution_allowed": False,
                    "probability_publishable": False,
                },
                req,
            ),
        )
    return req.model_copy(
        update={
            "sport": "NFL",
            "league": "NFL",
            "official_event_id": str(resolution["canonical_event_id"]),
            "sport_specific_evidence": dict(resolution["evidence"]),
            "source_snapshot_id": str(resolution["canonical_source_snapshot_id"]),
            "latest_material_update_timestamp": str(resolution["canonical_snapshot_timestamp"]),
        }
    )


def _nfl_envelope(base: Any, req: Any) -> Any:
    envelope = base._build_team_event_envelope(req)
    updates = {
        "official_event_id_source": "CANONICAL_NFLVERSE_LEDGER",
        "official_event_status_source": "CANONICAL_NFLVERSE_LEDGER",
        "home_starter_source": "NOT_APPLICABLE_NFL",
        "away_starter_source": "NOT_APPLICABLE_NFL",
        "home_lineup_source": "NOT_APPLICABLE_NFL",
        "away_lineup_source": "NOT_APPLICABLE_NFL",
        "injury_source": "NOT_USED_BY_NFL_FITTED_V1",
        "bullpen_source": "NOT_APPLICABLE_NFL",
        "weather_source": "NOT_USED_BY_NFL_FITTED_V1",
    }
    return replace(envelope, **updates)


def _attach_nfl_feature_consumption_metadata(model_result: dict[str, Any]) -> dict[str, Any]:
    """Attach scorer-proven feature consumption without changing model outputs.

    The certified scorer rejects schema/order mismatches before returning a
    probability. We still fail closed here: if the returned schema or immutable
    feature-row hash is absent, do not claim consumption and let the receipt stay
    UNVERIFIED.
    """
    out = dict(model_result)
    if str(out.get("feature_schema_version") or "") != NFL_FEATURE_SCHEMA_VERSION:
        return out
    feature_row_hash = str(out.get("feature_row_hash") or "").strip()
    if not feature_row_hash:
        return out

    out["consumed_feature_ids"] = list(NFL_FEATURE_ORDER)
    out["feature_observations"] = {
        feature_id: {
            "value_status": "AVAILABLE",
            "freshness_status": "UNKNOWN",
            "source": "NFL_CANONICAL_PREGAME_FEATURE_ROW",
            "provenance_id": feature_row_hash,
        }
        for feature_id in NFL_FEATURE_ORDER
    }
    out["can_execute"] = False
    return out


def _govern(base: Any, req: Any, route: Any, model_result: dict[str, Any], envelope: Any, *, db: Any) -> dict[str, Any]:
    package = base._build_governed_probability_package(envelope, model_result)
    if package is None:
        raise NFLModelOutputInvalid("NFL_GOVERNED_PROBABILITY_PACKAGE_INVALID")

    score_snapshot_id = model_result.get("score_snapshot_id")
    if not score_snapshot_id:
        raise NFLModelOutputInvalid("NFL_SCORE_SNAPSHOT_ID_MISSING")
    try:
        rpc = db.rpc(
            "wow_v17_nfl_team_event_governance_bridge",
            {
                "p_score_snapshot_id": str(score_snapshot_id),
                "p_research_run_id": req.research_run_id,
                "p_event_key": req.event_key,
                "p_requested_timezone": req.requested_timezone,
                "p_candidate_family": req.candidate_family,
                "p_decision_intent": req.decision_intent,
            },
        ).execute()
        governance = rpc.data
    except Exception as exc:
        governance = {
            "status": "UNAVAILABLE",
            "blockers": ["V17_NFL_EVENT_GOVERNANCE_BRIDGE_UNAVAILABLE"],
            "error_type": type(exc).__name__,
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }

    if not isinstance(governance, dict):
        governance = {
            "status": "INVALID",
            "blockers": ["V17_NFL_EVENT_GOVERNANCE_BRIDGE_INVALID_RESPONSE"],
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }

    required_pass = (
        governance.get("status") == "PASS"
        and governance.get("probability_audit_result") == "PASS_PROBABILITY_AUDIT"
        and governance.get("event_mutex_status") == "PASS"
        and governance.get("postmodel_gates_status") == "PASS"
        and governance.get("final_gates_status") == "PASS"
        and governance.get("global_terminal_reducer") == "V17_TERMINAL_REDUCER"
        and governance.get("probability_publishable") is True
        and governance.get("rank_eligible") is True
        and governance.get("can_execute") is False
        and governance.get("terminal_label") == "FINAL_APPROVED"
    )

    common = {
        **model_result,
        "requester_host_identity": route.requester_host_identity,
        "controlling_engine_identity": base.LLP_TEAM_BETTING_ENGINE,
        "candidate_family": route.candidate_family,
        "llp_governance": governance,
        "llp_probability_audit_result": governance.get("probability_audit_result"),
        "llp_event_decision": governance.get("event_decision"),
        "event_mutex_status": governance.get("event_mutex_status"),
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED",
        "probability_fields_withheld": False,
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "blend_publishable": False,
        "can_execute": False,
    }
    if required_pass:
        return {
            **common,
            "code": "GOVERNED_PROBABILITY_PUBLISHED",
            "terminal_label": "FINAL_APPROVED",
            "terminal_ceiling": "FINAL_APPROVED",
            "probability_publishable": True,
            "rank_eligible": True,
            "terminal_reducer_input": {"status": "PASS", "terminal_output": "FINAL_APPROVED", "global_terminal_reducer": "V17_TERMINAL_REDUCER"},
        }

    blockers = ["NFL_GOVERNED_PUBLICATION_NOT_PROVEN", *(str(v) for v in (governance.get("blockers") or []))]
    return {
        **common,
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "terminal_label": str(governance.get("terminal_label") or "MODEL_QUALIFIED_HOLD"),
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "blockers": sorted(set(blockers)),
        "probability_publishable": False,
        "rank_eligible": False,
        "terminal_reducer_input": {"status": governance.get("status", "HOLD"), "terminal_output": "MODEL_QUALIFIED_HOLD", "global_terminal_reducer": "V17_TERMINAL_REDUCER"},
    }


def score_nfl_team_event_request(
    team_event_module: Any,
    req: Any,
    *,
    event_api: Any,
    canonical_hydration_required: bool = False,
) -> dict[str, Any]:
    """Score one NFL team/event request through the governed publication chain.

    This is the single NFL implementation. Both entry points that can reach NFL
    — the additive publication wrapper and the production bridge registry — call
    it, so the two can never drift into two different NFL contracts.
    """
    try:
        route = team_event_module.resolve_host_route(req.requester_host_identity, req.candidate_family)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc), "probability_publishable": False, "can_execute": False}) from exc
    if route.controlling_engine_identity != team_event_module.LLP_TEAM_BETTING_ENGINE:
        raise HTTPException(status_code=422, detail={"code": "TEAM_EVENT_CONTROLLING_ENGINE_MISMATCH", "probability_publishable": False, "can_execute": False})

    errors = team_event_module._base_errors(req)
    if errors:
        raise HTTPException(status_code=422, detail=team_event_module._augment_detail({"code": "TEAM_EVENT_CONTRACT_INVALID", "errors": errors, "probability_publishable": False}, req))

    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        raise HTTPException(status_code=503, detail=team_event_module._augment_detail({"code": "NFL_EVENT_LEDGER_CLIENT_UNAVAILABLE", "probability_publishable": False}, req))
    db = get_client()
    effective_req = _canonicalize(req, db=db, base=team_event_module) if canonical_hydration_required else req
    scout_research_barrier = team_event_module._run_mandatory_scout_research(effective_req)

    try:
        try:
            load_champion_model(db)
        except NFLModelUnavailable:
            promotion = ensure_champion_model(db)
            if promotion.get("status") == "CERTIFICATION_BLOCKED":
                raise NFLModelUnavailable("NFL_MODEL_CERTIFICATION_BLOCKED")
        result = score_nfl_team_event(effective_req, db=db)
        result = _attach_nfl_feature_consumption_metadata(result)
        envelope = _nfl_envelope(team_event_module, effective_req)
        governed = _govern(team_event_module, effective_req, route, result, envelope, db=db)
    except (NFLModelUnavailable, NFLModelInputsInsufficient, NFLModelOutputInvalid, NFLModelScorerFailed) as exc:
        raise _typed_failure(team_event_module, effective_req, exc) from exc

    governed["scout_research_barrier"] = scout_research_barrier
    evidence = dict(effective_req.sport_specific_evidence or {})
    governed["canonical_acquisition"] = {
        "status": "PASS",
        "source_snapshot_id": effective_req.source_snapshot_id,
        "latest_material_update_timestamp": effective_req.latest_material_update_timestamp,
        "provider": "NFLVERSE_PUBLIC_DATA",
        "canonical_event_id": effective_req.official_event_id,
        "provider_event_id": evidence.get("provider_event_id"),
        "identity_resolution": evidence.get("identity_resolution"),
        "can_execute": False,
    }
    governed["candidate_envelope"] = {
        "research_run_id": envelope.research_run_id,
        "event_key": envelope.event_key,
        "official_event_id": envelope.official_event_id,
        "official_event_id_source": envelope.official_event_id_source,
        "event_start_time_utc": envelope.event_start_time_utc,
        "sport": envelope.sport,
        "league": envelope.league,
        "home_team": envelope.home_team,
        "away_team": envelope.away_team,
        "settlement_market": envelope.settlement_market,
        "settlement_basis": envelope.settlement_basis,
        "settlement_source": envelope.settlement_source,
        "source_snapshot_id": envelope.source_snapshot_id,
        "source_snapshot_timestamp": envelope.source_snapshot_timestamp,
        "latest_material_update_timestamp": envelope.latest_material_update_timestamp,
        "evidence_as_of": envelope.evidence_as_of,
    }
    return governed


def install_nfl_team_event_publication(team_event_module: Any) -> bool:
    if getattr(team_event_module, _PATCH_MARKER, False):
        return True
    original: Callable[..., dict[str, Any]] = team_event_module.score_team_event_request

    def score_team_event_request(req: Any, *, event_api: Any, canonical_hydration_required: bool = False) -> dict[str, Any]:
        if not _nfl_sport(req):
            return original(req, event_api=event_api, canonical_hydration_required=canonical_hydration_required)
        return score_nfl_team_event_request(
            team_event_module,
            req,
            event_api=event_api,
            canonical_hydration_required=canonical_hydration_required,
        )

    team_event_module.score_team_event_request = score_team_event_request
    setattr(team_event_module, _PATCH_MARKER, True)
    return True
