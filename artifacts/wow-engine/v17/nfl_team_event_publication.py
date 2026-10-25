"""Additive V17 NFL governed-probability publication bridge.

Installation wraps only NFL team-event requests. Every non-NFL request is
delegated to the exact pre-existing scorer, preserving MLB behavior.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from v17.nfl_daily_stage_receipts import attach_nfl_daily_stage_receipt

NFL_ALIASES = frozenset({"NFL", "FOOTBALL_NFL"})
_PATCH_MARKER = "_v17_nfl_team_event_publication_installed"


_NFL_SCHEDULE_MAX_AGE = timedelta(hours=24)


def _utc_instant(value: Any) -> datetime | None:
    """Parse a timezone-aware timestamp; naive or malformed values are not proof."""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _nfl_final_refresh(
    req: Any, model_result: dict[str, Any], *, db: Any, now: datetime | None = None,
) -> dict[str, Any]:
    """Re-read authoritative NFL schedule evidence after inference and before publication.

    This is not a synonym for SQL final_gates_status. A current read must match
    the modeled event and schedule contents, remain pregame, and be fresh enough
    to support a final status claim. Never manufacture a successful refresh.
    """
    checked_at = now if now is not None else datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("NFL_FINAL_REFRESH_NOW_MUST_BE_TIMEZONE_AWARE")
    checked_at = checked_at.astimezone(timezone.utc)
    reasons: list[str] = []
    changed = False
    start = _utc_instant(getattr(req, "event_start_time_utc", None))
    source_at = _utc_instant(getattr(req, "latest_material_update_timestamp", None))
    model_at = _utc_instant(model_result.get("model_timestamp"))
    prior_evidence = getattr(req, "sport_specific_evidence", None) or {}
    prior_hash = str(prior_evidence.get("schedule_content_sha256") or "")
    source_id = str(getattr(req, "source_snapshot_id", "") or "")
    details: dict[str, Any] = {
        "source": "NFL_CANONICAL_SCHEDULE_REQUERY",
        "checked_at": checked_at.isoformat(),
        "scored_source_snapshot_id": source_id or None,
        "model_timestamp": model_at.isoformat() if model_at else None,
        "can_execute": False,
    }
    if start is None or start <= checked_at:
        reasons.append("FINAL_REFRESH_EVENT_NOT_PREGAME")
    if not prior_hash or not source_id or source_at is None:
        reasons.append("FINAL_REFRESH_SCORED_SOURCE_PROVENANCE_MISSING")
    if model_at is None or (source_at is not None and model_at < source_at):
        reasons.append("FINAL_REFRESH_MODEL_TIMESTAMP_NOT_CURRENT")

    try:
        refreshed = resolve_nfl_team_event_evidence(req, db=db)
    except Exception:
        # A refresh failure must never erase the completed numerical model.
        reasons.append("FINAL_REFRESH_SOURCE_UNAVAILABLE")
        refreshed = None

    if not isinstance(refreshed, dict) or refreshed.get("ok") is not True:
        if refreshed is not None:
            reasons.append("FINAL_REFRESH_CANONICAL_EVENT_UNRESOLVED")
            details["source_failure_code"] = refreshed.get("code")
    else:
        latest_id = str(refreshed.get("canonical_source_snapshot_id") or "")
        latest_hash = str(refreshed.get("schedule_content_sha256") or "")
        latest_at = _utc_instant(refreshed.get("canonical_snapshot_timestamp"))
        details["refresh_source_snapshot_id"] = latest_id or None
        details["refresh_source_timestamp"] = latest_at.isoformat() if latest_at else None
        details["identity_resolution"] = refreshed.get("identity_resolution")
        if (
            str(refreshed.get("canonical_event_id") or "")
            != str(getattr(req, "official_event_id", "") or "")
            or str(refreshed.get("canonical_home_team") or "")
            != str(prior_evidence.get("canonical_home_team") or "")
            or str(refreshed.get("canonical_away_team") or "")
            != str(prior_evidence.get("canonical_away_team") or "")
        ):
            reasons.append("FINAL_REFRESH_EVENT_IDENTITY_CHANGED")
            changed = True
        if not latest_hash or not prior_hash or latest_hash != prior_hash:
            reasons.append("FINAL_REFRESH_MATERIAL_SOURCE_CHANGED")
            changed = True
        if (
            latest_at is None
            or latest_at > checked_at
            or checked_at - latest_at > _NFL_SCHEDULE_MAX_AGE
        ):
            reasons.append("FINAL_REFRESH_SOURCE_STALE_OR_INVALID")

    passed = not reasons
    return {
        "final_refresh_status": "PASS" if passed else "HOLD",
        "reasons": sorted(set(reasons)),
        "probability_invalidated": changed or start is None or start <= checked_at,
        "rerun_required": changed,
        **details,
    }


def _apply_nfl_final_refresh_ceiling(
    governed: dict[str, Any], refresh: dict[str, Any],
) -> dict[str, Any]:
    """Attach independent refresh proof and never upgrade a SQL HOLD."""
    out = dict(governed)
    out["llp_governance"] = {
        **dict(out.get("llp_governance") or {}),
        "final_refresh": refresh,
    }
    if refresh["final_refresh_status"] == "PASS":
        return out

    # Preserve the completed numerical sporting model and original SQL evidence.
    out["rank_eligible"] = False
    out["probability_publishable"] = False
    out["terminal_label"] = "MODEL_QUALIFIED_HOLD"
    out["terminal_ceiling"] = "MODEL_QUALIFIED_HOLD"
    out["code"] = "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
    out["blockers"] = sorted(set([
        *(str(x) for x in out.get("blockers", [])),
        *(str(x) for x in refresh["reasons"]),
    ]))
    out["terminal_reducer_input"] = {
        "status": "HOLD",
        "terminal_output": "MODEL_QUALIFIED_HOLD",
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
    }
    return out


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

    refresh = _nfl_final_refresh(effective_req, result, db=db)
    governed = _apply_nfl_final_refresh_ceiling(governed, refresh)
    # The fitted scorer has already persisted the immutable event prediction.
    # Re-read that exact row before emitting the two proof fields required by
    # the full-board auditor. Calibration health alone is not stage proof.
    governed = attach_nfl_daily_stage_receipt(db=db, req=effective_req, governed=governed)
    if governed["nfl_stage_receipt"]["status"] != "PASS":
        governed["probability_publishable"] = False
        governed["rank_eligible"] = False
        governed["terminal_label"] = "MODEL_QUALIFIED_HOLD"
        governed["terminal_ceiling"] = "MODEL_QUALIFIED_HOLD"
        governed["code"] = "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
        governed["blockers"] = sorted(set([
            *(str(v) for v in (governed.get("blockers") or [])),
            *(str(v) for v in governed["nfl_stage_receipt"]["blockers"]),
        ]))
        governed["terminal_reducer_input"] = {
            "status": "HOLD",
            "terminal_output": "MODEL_QUALIFIED_HOLD",
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        }
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
