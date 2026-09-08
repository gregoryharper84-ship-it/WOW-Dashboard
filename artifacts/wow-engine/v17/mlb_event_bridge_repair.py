"""V17 MLB team/event direct scorer bridge repair.

The V17 TEAM_EVENT ingress already owns canonical MLB hydration.  This module
repairs the remaining runtime seam: V17 must not depend on the legacy
``wow_mlb_score_event_bridge`` RPC merely to reach the registered fitted model.
Instead it resolves the canonical frozen-score identity from the backend ledger
and invokes ``mlb_event_specialist_v16.score_prospective_event`` in-process.

The module also installs canonical completion-failure taxonomy, stage-audit
fields, and a non-secret /health bridge-readiness block.  It never authorizes
wager execution; ``can_execute`` is false on every path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException

from v17.mlb_team_event_hydration import resolve_mlb_team_event_evidence
from v17.team_event_capability_manifest import team_event_capability

CAN_EXECUTE = False
_LOGGER = logging.getLogger("wow.v17.mlb.event_bridge")

_REQUIRED_MODEL_FIELDS = (
    "raw_home_probability",
    "raw_away_probability",
    "independent_home_probability",
    "independent_away_probability",
    "calibrated_home_probability",
    "calibrated_away_probability",
    "calibrated_home_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_lower_bound",
    "calibrated_away_upper_bound",
    "calibration_method",
    "calibration_version",
    "model_version",
    "model_timestamp",
    "source_snapshot_id",
)


def _stage_audit(
    *,
    event_identity_complete: bool = True,
    sport_model_selected: bool = False,
    sport_model_invoked: bool = False,
    probability_package_valid: bool = False,
    dynamic_calibration_complete: bool = False,
    probability_audit_passed: bool = False,
    event_governor_complete: bool = False,
    rank_eligible: bool = False,
    last_completed_stage: str = "EVENT_IDENTITY",
    model_provider: str | None = None,
    model_version: str | None = None,
    source_snapshot_id: str | None = None,
    source_snapshot_timestamp: str | None = None,
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "event_identity_complete": bool(event_identity_complete),
        "sport_model_selected": bool(sport_model_selected),
        "sport_model_invoked": bool(sport_model_invoked),
        "probability_package_valid": bool(probability_package_valid),
        "dynamic_calibration_complete": bool(dynamic_calibration_complete),
        "probability_audit_passed": bool(probability_audit_passed),
        "event_governor_complete": bool(event_governor_complete),
        "rank_eligible": bool(rank_eligible),
        "last_completed_stage": last_completed_stage,
        "model_provider": model_provider,
        "model_version": model_version,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_timestamp": source_snapshot_timestamp,
        "missing_fields": list(missing_fields or []),
        "can_execute": False,
    }


def _failure_detail(
    status: str,
    blocker_code: str,
    *,
    audit: dict[str, Any],
    error_type: str | None = None,
    scorer_error_code: str | None = None,
    failing_fields: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        **audit,
        "status": status,
        "code": status,
        "blocker_code": blocker_code,
        "probability_publishable": False,
        "rank_eligible": False,
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "can_execute": False,
    }
    if error_type:
        payload["error_type"] = error_type
    if scorer_error_code:
        payload["scorer_error_code"] = scorer_error_code
    if failing_fields:
        payload["failing_fields"] = list(failing_fields)
    if extra:
        payload.update(extra)
    return payload


def _raise_failure(status_code: int, detail: dict[str, Any]) -> None:
    raise HTTPException(status_code=status_code, detail=detail)


def _resolve_direct_scorer() -> Callable[..., dict[str, Any]]:
    from mlb_event_specialist_v16 import score_prospective_event

    if not callable(score_prospective_event):
        raise RuntimeError("MLB_PROSPECTIVE_SCORER_NOT_CALLABLE")
    return score_prospective_event


def _rows(call: Any) -> list[dict[str, Any]]:
    result = call.execute()
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _resolve_bridge_payload(req: Any, *, event_api: Any) -> dict[str, Any]:
    """Resolve the frozen baseline identity needed by the in-process scorer.

    This deliberately does not call ``wow_mlb_score_event_bridge``.  If a frozen
    base score is absent, it may invoke the existing internal forward scorer to
    materialize that baseline, then returns the immutable IDs to the Python
    prospective specialist.
    """
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return {
            "ok": False,
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_CLIENT_UNAVAILABLE",
            "missing_fields": [],
            "sport_model_invoked": False,
        }

    try:
        client = get_client()
        event_rows = _rows(
            client.table("wow_mlb_forward_shadow_events")
            .select(
                "shadow_event_id,spec_id,official_event_id,official_date,event_start_time,event_status,"
                "home_team,away_team,venue_name,home_probable_pitcher,away_probable_pitcher,"
                "snapshot_id,snapshot_timestamp,feature_hydration_status,lineup_status,"
                "lineup_snapshot_id,lineup_confirmed_at"
            )
            .eq("official_event_id", str(req.official_event_id))
            .eq("snapshot_id", str(req.source_snapshot_id))
            .limit(1)
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_QUERY_FAILED",
            "missing_fields": [],
            "error_type": type(exc).__name__,
            "sport_model_invoked": False,
        }

    if not event_rows:
        return {
            "ok": False,
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": ["canonical_source_snapshot_id"],
            "sport_model_invoked": False,
        }

    event = event_rows[0]
    missing = [
        field
        for field in (
            "venue_name",
            "home_probable_pitcher",
            "away_probable_pitcher",
            "event_status",
            "snapshot_id",
            "snapshot_timestamp",
        )
        if not str(event.get(field) or "").strip()
    ]
    if event.get("feature_hydration_status") != "PASS":
        missing.append("feature_hydration_status")

    # The direct prospective model consumes a confirmed strict-pregame lineup.
    # Detect that requirement before model invocation so missing lineup evidence
    # is classified as MODEL_INPUTS_INSUFFICIENT rather than scorer failure.
    lineup_rows: list[dict[str, Any]] = []
    if event.get("shadow_event_id"):
        try:
            lineup_rows = _rows(
                client.table("wow_mlb_forward_lineup_snapshots")
                .select("lineup_snapshot_id,lineup_status,strict_pregame_provenance,captured_at")
                .eq("shadow_event_id", str(event["shadow_event_id"]))
                .eq("lineup_status", "CONFIRMED")
                .eq("strict_pregame_provenance", True)
                .order("captured_at", desc=True)
                .limit(1)
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "MODEL_INPUTS_INSUFFICIENT",
                "blocker_code": "MLB_TEAM_EVENT_CANONICAL_LINEUP_QUERY_FAILED",
                "missing_fields": ["home_lineup_status", "away_lineup_status"],
                "error_type": type(exc).__name__,
                "sport_model_invoked": False,
                "source_snapshot_id": str(event.get("snapshot_id") or ""),
                "source_snapshot_timestamp": str(event.get("snapshot_timestamp") or ""),
            }
    if not lineup_rows:
        missing.extend(["home_lineup_status", "away_lineup_status"])

    if missing:
        return {
            "ok": False,
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": sorted(set(missing)),
            "sport_model_invoked": False,
            "source_snapshot_id": str(event.get("snapshot_id") or ""),
            "source_snapshot_timestamp": str(event.get("snapshot_timestamp") or ""),
        }

    try:
        score_rows = _rows(
            client.table("wow_mlb_forward_score_snapshots")
            .select("score_snapshot_id,shadow_event_id,spec_id,model_timestamp,model_version")
            .eq("shadow_event_id", str(event["shadow_event_id"]))
            .order("model_timestamp", desc=True)
            .limit(1)
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "MODEL_SCORER_FAILED",
            "blocker_code": "EVENT_MODEL_BRIDGE_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "scorer_error_code": "MLB_BASELINE_SCORE_LOOKUP_FAILED",
            "missing_fields": [],
            "sport_model_invoked": True,
            "source_snapshot_id": str(event["snapshot_id"]),
            "source_snapshot_timestamp": str(event["snapshot_timestamp"]),
        }

    if not score_rows:
        try:
            baseline = client.rpc(
                "wow_mlb_forward_score_event",
                {"p_shadow_event_id": str(event["shadow_event_id"])},
            ).execute()
            baseline_payload = getattr(baseline, "data", None)
        except Exception as exc:
            return {
                "ok": False,
                "status": "MODEL_SCORER_FAILED",
                "blocker_code": "EVENT_MODEL_BRIDGE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "scorer_error_code": "MLB_BASELINE_SCORER_CALL_FAILED",
                "missing_fields": [],
                "sport_model_invoked": True,
                "source_snapshot_id": str(event["snapshot_id"]),
                "source_snapshot_timestamp": str(event["snapshot_timestamp"]),
            }
        if not isinstance(baseline_payload, dict) or not str(baseline_payload.get("status") or "").startswith("SHADOW_SCORED"):
            return {
                "ok": False,
                "status": "MODEL_SCORER_FAILED",
                "blocker_code": "EVENT_MODEL_BRIDGE_UNAVAILABLE",
                "scorer_error_code": str((baseline_payload or {}).get("reason") or (baseline_payload or {}).get("status") or "MLB_BASELINE_SCORER_BLOCKED"),
                "missing_fields": [],
                "sport_model_invoked": True,
                "source_snapshot_id": str(event["snapshot_id"]),
                "source_snapshot_timestamp": str(event["snapshot_timestamp"]),
            }
        try:
            score_rows = _rows(
                client.table("wow_mlb_forward_score_snapshots")
                .select("score_snapshot_id,shadow_event_id,spec_id,model_timestamp,model_version")
                .eq("shadow_event_id", str(event["shadow_event_id"]))
                .order("model_timestamp", desc=True)
                .limit(1)
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "MODEL_SCORER_FAILED",
                "blocker_code": "EVENT_MODEL_BRIDGE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "scorer_error_code": "MLB_BASELINE_SCORE_RELOAD_FAILED",
                "missing_fields": [],
                "sport_model_invoked": True,
                "source_snapshot_id": str(event["snapshot_id"]),
                "source_snapshot_timestamp": str(event["snapshot_timestamp"]),
            }

    if not score_rows or not score_rows[0].get("score_snapshot_id"):
        return {
            "ok": False,
            "status": "MODEL_OUTPUT_INVALID",
            "blocker_code": "SCORED_MODEL_VALIDATION_FAILED",
            "failing_fields": ["score_snapshot_id"],
            "missing_fields": [],
            "sport_model_invoked": True,
            "source_snapshot_id": str(event["snapshot_id"]),
            "source_snapshot_timestamp": str(event["snapshot_timestamp"]),
        }

    score = score_rows[0]
    lineup_timestamp = str(lineup_rows[0].get("captured_at") or "")
    snapshot_timestamp = str(event.get("snapshot_timestamp") or "")
    latest_material = max(snapshot_timestamp, lineup_timestamp)
    return {
        "ok": True,
        "bridge_payload": {
            "status": "MODEL_SCORED_HELD",
            "code": "REAL_FITTED_MODEL_PATH_PROVEN",
            "controlling_specialist": "wow.mlb-game-win-probability-expert",
            "shadow_event_id": str(event["shadow_event_id"]),
            "score_snapshot_id": str(score["score_snapshot_id"]),
            "spec_id": str(score.get("spec_id") or event.get("spec_id") or ""),
            "server_snapshot_id": str(event["snapshot_id"]),
            "server_snapshot_timestamp": snapshot_timestamp,
            "model_timestamp": str(score.get("model_timestamp") or ""),
            "model_version": str(score.get("model_version") or ""),
            "scoring_evidence_produced": True,
            "probability_fields_withheld": True,
            "probability_publishable": False,
            "can_execute": False,
        },
        "client": client,
        "source_snapshot_id": str(event["snapshot_id"]),
        "source_snapshot_timestamp": snapshot_timestamp,
        "latest_material_update_timestamp": latest_material,
        "sport_model_invoked": False,
    }


def _validate_direct_package(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in _REQUIRED_MODEL_FIELDS:
        value = result.get(field)
        if value in (None, "", "NOT_CALLED", "UNKNOWN", "MISSING"):
            errors.append(field)

    numeric = (
        "raw_home_probability",
        "raw_away_probability",
        "independent_home_probability",
        "independent_away_probability",
        "calibrated_home_probability",
        "calibrated_away_probability",
        "calibrated_home_lower_bound",
        "calibrated_home_upper_bound",
        "calibrated_away_lower_bound",
        "calibrated_away_upper_bound",
    )
    values: dict[str, float] = {}
    for field in numeric:
        try:
            values[field] = float(result[field])
        except (KeyError, TypeError, ValueError):
            if field not in errors:
                errors.append(field)

    if not errors:
        for field in numeric:
            if not 0.0 <= values[field] <= 1.0:
                errors.append(field)
        if abs(values["raw_home_probability"] + values["raw_away_probability"] - 1.0) > 1e-6:
            errors.append("raw_probability_normalization")
        if abs(values["independent_home_probability"] + values["independent_away_probability"] - 1.0) > 1e-6:
            errors.append("independent_probability_normalization")
        if abs(values["calibrated_home_probability"] + values["calibrated_away_probability"] - 1.0) > 1e-6:
            errors.append("calibrated_probability_normalization")
        if not (
            values["calibrated_home_lower_bound"]
            <= values["calibrated_home_probability"]
            <= values["calibrated_home_upper_bound"]
        ):
            errors.append("calibrated_home_bounds")
        if not (
            values["calibrated_away_lower_bound"]
            <= values["calibrated_away_probability"]
            <= values["calibrated_away_upper_bound"]
        ):
            errors.append("calibrated_away_bounds")
    return sorted(set(errors))


def score_event_v17_bridge(
    req: Any,
    *,
    event_api: Any,
    scorer_override: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    capability = team_event_capability("MLB")
    provider = capability.controlling_specialist
    selected = capability.status == "AVAILABLE" and bool(provider)
    if not selected:
        audit = _stage_audit(
            event_identity_complete=True,
            sport_model_selected=False,
            last_completed_stage="EVENT_IDENTITY",
            model_provider=provider,
            source_snapshot_id=str(getattr(req, "source_snapshot_id", "") or ""),
        )
        _raise_failure(409, _failure_detail(
            "MODEL_UNAVAILABLE",
            capability.blocker or "MLB_EVENT_MODEL_NOT_REGISTERED",
            audit=audit,
        ))

    try:
        scorer = scorer_override or _resolve_direct_scorer()
    except Exception as exc:
        audit = _stage_audit(
            event_identity_complete=True,
            sport_model_selected=True,
            sport_model_invoked=False,
            last_completed_stage="SPORT_MODEL_SELECTED",
            model_provider=provider,
            source_snapshot_id=str(getattr(req, "source_snapshot_id", "") or ""),
        )
        _raise_failure(503, _failure_detail(
            "MODEL_SCORER_FAILED",
            "EVENT_MODEL_BRIDGE_UNAVAILABLE",
            audit=audit,
            error_type=type(exc).__name__,
            scorer_error_code="MLB_EVENT_SCORER_RESOLVE_FAILED",
        ))

    resolution = _resolve_bridge_payload(req, event_api=event_api)
    if resolution.get("ok") is not True:
        invoked = bool(resolution.get("sport_model_invoked"))
        audit = _stage_audit(
            event_identity_complete=True,
            sport_model_selected=True,
            sport_model_invoked=invoked,
            probability_package_valid=False,
            last_completed_stage="SPORT_MODEL_INVOKED" if invoked else "SPORT_MODEL_SELECTED",
            model_provider=provider,
            source_snapshot_id=str(resolution.get("source_snapshot_id") or getattr(req, "source_snapshot_id", "") or ""),
            source_snapshot_timestamp=str(resolution.get("source_snapshot_timestamp") or "") or None,
            missing_fields=list(resolution.get("missing_fields") or []),
        )
        status = str(resolution.get("status") or "MODEL_SCORER_FAILED")
        http_status = 422 if status == "MODEL_INPUTS_INSUFFICIENT" else (409 if status == "MODEL_OUTPUT_INVALID" else 503)
        _raise_failure(http_status, _failure_detail(
            status,
            str(resolution.get("blocker_code") or "EVENT_MODEL_BRIDGE_UNAVAILABLE"),
            audit=audit,
            error_type=resolution.get("error_type"),
            scorer_error_code=resolution.get("scorer_error_code"),
            failing_fields=list(resolution.get("failing_fields") or []),
        ))

    try:
        result = scorer(
            req=req,
            bridge_payload=dict(resolution["bridge_payload"]),
            client=resolution["client"],
        )
    except Exception as exc:
        audit = _stage_audit(
            event_identity_complete=True,
            sport_model_selected=True,
            sport_model_invoked=True,
            last_completed_stage="SPORT_MODEL_INVOKED",
            model_provider=provider,
            source_snapshot_id=str(resolution.get("source_snapshot_id") or ""),
            source_snapshot_timestamp=str(resolution.get("source_snapshot_timestamp") or "") or None,
        )
        _raise_failure(503, _failure_detail(
            "MODEL_SCORER_FAILED",
            "EVENT_MODEL_BRIDGE_UNAVAILABLE",
            audit=audit,
            error_type=type(exc).__name__,
            scorer_error_code=str(exc)[:160] or type(exc).__name__,
        ))

    if not isinstance(result, dict):
        result = {}
    result = dict(result)
    result.setdefault("source_snapshot_id", str(resolution.get("source_snapshot_id") or ""))
    result["source_snapshot_timestamp"] = str(resolution.get("source_snapshot_timestamp") or "")
    result["latest_material_update_timestamp"] = str(
        resolution.get("latest_material_update_timestamp")
        or result.get("latest_material_update_timestamp")
        or ""
    )
    result["normalized_outcome_space"] = {
        "HOME": result.get("calibrated_home_probability"),
        "AWAY": result.get("calibrated_away_probability"),
    }

    failing = _validate_direct_package(result)
    if failing:
        audit = _stage_audit(
            event_identity_complete=True,
            sport_model_selected=True,
            sport_model_invoked=True,
            probability_package_valid=False,
            dynamic_calibration_complete=False,
            last_completed_stage="SPORT_MODEL_INVOKED",
            model_provider=provider,
            model_version=str(result.get("model_version") or "") or None,
            source_snapshot_id=str(resolution.get("source_snapshot_id") or ""),
            source_snapshot_timestamp=str(resolution.get("source_snapshot_timestamp") or "") or None,
        )
        _raise_failure(409, _failure_detail(
            "MODEL_OUTPUT_INVALID",
            "SCORED_MODEL_VALIDATION_FAILED",
            audit=audit,
            failing_fields=failing,
        ))

    result.update(_stage_audit(
        event_identity_complete=True,
        sport_model_selected=True,
        sport_model_invoked=True,
        probability_package_valid=True,
        dynamic_calibration_complete=True,
        probability_audit_passed=False,
        event_governor_complete=False,
        rank_eligible=False,
        last_completed_stage="PROBABILITY_PACKAGE_VALIDATED",
        model_provider=str(result.get("controlling_specialist") or provider),
        model_version=str(result.get("model_version") or "") or None,
        source_snapshot_id=str(resolution.get("source_snapshot_id") or ""),
        source_snapshot_timestamp=str(resolution.get("source_snapshot_timestamp") or "") or None,
    ))
    result["status"] = str(result.get("status") or "MODEL_SCORED_PROSPECTIVE")
    result["probability_publishable"] = bool(result.get("probability_publishable") is True)
    result["can_execute"] = False
    return result


def canonicalize_public_mlb_request(req: Any, event_api: Any, *, team_runtime: Any) -> Any:
    capability = team_event_capability("MLB")
    if capability.status != "AVAILABLE":
        audit = _stage_audit(event_identity_complete=True, sport_model_selected=False)
        _raise_failure(409, team_runtime._augment_detail(_failure_detail(
            "MODEL_UNAVAILABLE",
            capability.blocker or "MLB_EVENT_MODEL_NOT_REGISTERED",
            audit=audit,
        ), req))

    resolution = resolve_mlb_team_event_evidence(req, event_api=event_api)
    if resolution.get("ok") is not True:
        missing = list(resolution.get("missing_fields") or [])
        mismatches = list(resolution.get("identity_mismatches") or [])
        audit = _stage_audit(
            event_identity_complete=not bool(mismatches),
            sport_model_selected=True,
            sport_model_invoked=False,
            last_completed_stage="SPORT_MODEL_SELECTED",
            model_provider=capability.controlling_specialist,
            source_snapshot_id=str(getattr(req, "source_snapshot_id", "") or ""),
            missing_fields=missing,
        )
        detail = _failure_detail(
            "MODEL_INPUTS_INSUFFICIENT",
            str(resolution.get("code") or "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"),
            audit=audit,
            error_type=resolution.get("error_type"),
            extra={"identity_mismatches": mismatches, "canonical_acquisition_attempted": True},
        )
        _raise_failure(422, team_runtime._augment_detail(detail, req))

    latest_material = str(
        resolution.get("canonical_latest_material_update_timestamp")
        or resolution.get("canonical_snapshot_timestamp")
    )
    return req.model_copy(update={
        "sport_specific_evidence": dict(resolution["evidence"]),
        "source_snapshot_id": str(resolution["canonical_source_snapshot_id"]),
        "latest_material_update_timestamp": latest_material,
    })


def mlb_request_with_taxonomy(req: Any, event_api: Any, *, team_runtime: Any, original: Callable[..., Any]) -> Any:
    try:
        return original(req, event_api)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") != "RUN_INVALID_ACQUISITION_INCOMPLETE":
            raise
        missing = list(detail.get("missing_fields") or [])
        capability = team_event_capability("MLB")
        audit = _stage_audit(
            event_identity_complete=True,
            sport_model_selected=capability.status == "AVAILABLE",
            sport_model_invoked=False,
            last_completed_stage="SPORT_MODEL_SELECTED" if capability.status == "AVAILABLE" else "EVENT_IDENTITY",
            model_provider=capability.controlling_specialist,
            source_snapshot_id=str(getattr(req, "source_snapshot_id", "") or ""),
            source_snapshot_timestamp=str(getattr(req, "latest_material_update_timestamp", "") or "") or None,
            missing_fields=missing,
        )
        mapped = _failure_detail(
            "MODEL_INPUTS_INSUFFICIENT",
            "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            audit=audit,
        )
        _raise_failure(422, team_runtime._augment_detail(mapped, req))


def governance_with_stage_audit(
    req: Any,
    route: Any,
    model_result: dict[str, Any],
    envelope: Any | None,
    *,
    event_api: Any,
    original: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    out = original(req, route, model_result, envelope=envelope, event_api=event_api)
    if not isinstance(out, dict):
        return out
    out = dict(out)
    package_valid = bool(model_result.get("probability_package_valid"))
    calibration_complete = bool(model_result.get("dynamic_calibration_complete"))
    probability_audit_passed = out.get("llp_probability_audit_result") == "PASS_PROBABILITY_AUDIT"
    governance = out.get("llp_governance")
    event_governor_complete = isinstance(governance, dict) and governance.get("status") == "PASS"
    last_stage = str(model_result.get("last_completed_stage") or "PROBABILITY_PACKAGE_VALIDATED")
    if probability_audit_passed:
        last_stage = "PROBABILITY_AUDIT"
    if event_governor_complete:
        last_stage = "EVENT_GOVERNOR"
    out.update({
        "event_identity_complete": bool(model_result.get("event_identity_complete", True)),
        "sport_model_selected": bool(model_result.get("sport_model_selected", True)),
        "sport_model_invoked": bool(model_result.get("sport_model_invoked", True)),
        "probability_package_valid": package_valid,
        "dynamic_calibration_complete": calibration_complete,
        "probability_audit_passed": probability_audit_passed,
        "event_governor_complete": event_governor_complete,
        "last_completed_stage": last_stage,
        "model_provider": model_result.get("model_provider") or model_result.get("controlling_specialist"),
        "model_version": model_result.get("model_version"),
        "source_snapshot_id": model_result.get("source_snapshot_id"),
        "source_snapshot_timestamp": model_result.get("source_snapshot_timestamp"),
        "missing_fields": list(model_result.get("missing_fields") or []),
        "rank_eligible": bool(out.get("rank_eligible")),
        "can_execute": False,
    })
    return out


def mlb_team_event_bridge_readiness(*, event_api: Any | None = None) -> dict[str, Any]:
    capability = team_event_capability("MLB")
    registered = capability.status == "AVAILABLE"
    adapter_importable = True
    try:
        scorer = _resolve_direct_scorer()
        scorer_resolvable = callable(scorer)
    except Exception:
        scorer_resolvable = False
    canonical_snapshot_provider_ready = callable(resolve_mlb_team_event_evidence)
    database_client_resolvable = True if event_api is None else callable(getattr(event_api, "get_client", None))
    up = all((registered, adapter_importable, scorer_resolvable, canonical_snapshot_provider_ready, database_client_resolvable))
    return {
        "registered_capability": registered,
        "adapter_importable": adapter_importable,
        "scorer_resolvable": scorer_resolvable,
        "canonical_snapshot_provider_ready": canonical_snapshot_provider_ready,
        "database_client_resolvable": database_client_resolvable,
        "status": "UP" if up else "DOWN",
        "model_provider": capability.controlling_specialist,
        "can_execute": False,
    }


def _install_health_route(app: Any, *, event_api: Any) -> None:
    if getattr(app.state, "v17_mlb_bridge_health_installed", False):
        return
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/health"
            and "GET" in (getattr(route, "methods", set()) or set())
        )
    ]

    @app.get("/health")
    def v17_health():
        return {
            "status": "ok",
            "host_type": "EXTERNAL_GOVERNED_BACKEND",
            "compute_provider": "RENDER",
            "database_provider": "SUPABASE",
            "batch_provider": "COLAB",
            "deployment_tier": "FREE",
            "runtime_generation": "V17_ACTIVE",
            "team_event_bridges": {
                "MLB": mlb_team_event_bridge_readiness(event_api=event_api),
            },
            "can_execute": False,
        }

    @app.on_event("startup")
    async def log_v17_mlb_bridge_readiness():
        state = mlb_team_event_bridge_readiness(event_api=event_api)
        _LOGGER.warning(
            "MLB_EVENT_BRIDGE_IMPORT=%s MLB_EVENT_SCORER_RESOLVE=%s MLB_CANONICAL_SNAPSHOT_PROVIDER=%s MLB_EVENT_BRIDGE_STATUS=%s can_execute=false",
            "PASS" if state["adapter_importable"] else "FAIL",
            "PASS" if state["scorer_resolvable"] else "FAIL",
            "PASS" if state["canonical_snapshot_provider_ready"] else "FAIL",
            state["status"],
        )

    app.state.v17_mlb_bridge_health_installed = True


def install_mlb_event_bridge_repair(*, market_api: Any, team_event_module: Any) -> bool:
    """Install V17-only bridge repair without mutating lower layers when inactive."""
    if getattr(market_api, "_v17_mlb_event_bridge_repair_installed", False):
        return True

    prod = getattr(market_api, "prod", None)
    event_api = getattr(prod, "event_api", None)
    if event_api is None:
        return False

    original_mlb_request = team_event_module._mlb_request
    original_governance = team_event_module._run_mlb_llp_governance

    def _canonicalize(req: Any, event_api_arg: Any) -> Any:
        return canonicalize_public_mlb_request(req, event_api_arg, team_runtime=team_event_module)

    def _mlb_request(req: Any, event_api_arg: Any) -> Any:
        return mlb_request_with_taxonomy(
            req,
            event_api_arg,
            team_runtime=team_event_module,
            original=original_mlb_request,
        )

    def _governance(req: Any, route: Any, model_result: dict[str, Any], envelope: Any | None = None, *, event_api: Any) -> dict[str, Any]:
        return governance_with_stage_audit(
            req,
            route,
            model_result,
            envelope,
            event_api=event_api,
            original=original_governance,
        )

    if not hasattr(event_api, "_v17_original_score_event"):
        event_api._v17_original_score_event = event_api.score_event

    def _direct_score_event(req: Any) -> dict[str, Any]:
        return score_event_v17_bridge(req, event_api=event_api)

    event_api.score_event = _direct_score_event
    team_event_module._canonicalize_public_mlb_request = _canonicalize
    team_event_module._mlb_request = _mlb_request
    team_event_module._run_mlb_llp_governance = _governance

    app = getattr(market_api, "app", None)
    if app is not None:
        _install_health_route(app, event_api=event_api)

    market_api._v17_mlb_event_bridge_repair_installed = True
    return True


__all__ = [
    "install_mlb_event_bridge_repair",
    "mlb_team_event_bridge_readiness",
    "score_event_v17_bridge",
]
