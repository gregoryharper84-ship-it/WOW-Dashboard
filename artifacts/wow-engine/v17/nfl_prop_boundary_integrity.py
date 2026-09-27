"""V17 NFL prop boundary integrity repairs.

This module is intentionally probability-neutral.  It repairs four orchestration
boundaries around the existing governed prop scorer:

* verified NFL provider identity may translate an opaque display event id into the
  canonical WOW/NFL event id before /score-pick-request;
* exact sport/stat capability is exposed before a host attempts scoring;
* interactive parallel exceptions preserve typed blockers and never claim a
  specialist ran without evidence; and
* durable rows that terminated before model scoring return an explicit
  NO_PREDICTION_CREATED receipt state instead of a prediction-ledger transport
  error.

It never computes, substitutes, calibrates, ranks, prices, or executes a sporting
probability.  V17_TERMINAL_REDUCER remains the terminal authority and
can_execute=false remains unconditional.
"""
from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Mapping, Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import nfl_prop_auto_hydration as nfl
import pick_request_runtime as pick_facade
import pick_request_runtime_core as pick_core
import prop_auto_hydration_router as hydration_router
from prop_auto_hydration import PropAutoHydrationError
from v17 import interactive_pick_hydration as interactive_hydration
from v17 import interactive_pick_parallel as interactive_parallel
from v17 import prediction_receipt_lookup_runtime as receipt_runtime


_CANONICAL_NFL_EVENT_RE = re.compile(r"^\d{4}_\d{2}_[A-Z0-9]{2,3}_[A-Z0-9]{2,3}$")
_STATE_KEY = "wow_v17_nfl_prop_boundary_integrity_installed"
_STARTUP_KEY = "wow_v17_nfl_prop_boundary_integrity_startup_scheduled"
_PATCH_KEY = "wow_v17_nfl_prop_boundary_integrity_patches_installed"

_ORIGINAL_ROUTER_HYDRATE = hydration_router.auto_hydrate_prop_evidence
_ORIGINAL_RECEIPT_LOOKUP = receipt_runtime.lookup_prediction_receipts


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _canonical_nfl_event_id(value: Any) -> bool:
    return bool(_CANONICAL_NFL_EVENT_RE.fullmatch(_text(value).upper()))


def _provider_alias_from_input(value: Any) -> Optional[str]:
    text = _text(value)
    if text.isdigit():
        return text
    if text.upper().startswith("ESPN:"):
        suffix = text.split(":", 1)[1].strip()
        return suffix or None
    return None


def resolve_nfl_event_identity(
    *,
    player: str,
    event_start_time: str,
    opponent: Optional[str] = None,
    provider_event_id: Optional[str] = None,
    current_event_id: Optional[str] = None,
    http_get: Callable[..., Any] = nfl.httpx.get,
) -> dict[str, Any]:
    """Resolve one NFL event through verified ESPN identity metadata.

    The ESPN event id remains a provider alias.  The canonical id is derived from
    provider season/week + normalized away/home teams using the same canonical
    metadata routine already used by the NFL evidence hydrator.
    """
    try:
        event_start = nfl._aware(event_start_time)
        athlete_id, official_name = nfl._resolve_espn_athlete(player, http_get=http_get)
        team = nfl._athlete_team(athlete_id, http_get=http_get)
        target = nfl._target_event(
            event_start=event_start,
            team=team,
            opponent=opponent,
            http_get=http_get,
        )
    except nfl.NFLPropHydrationError as exc:
        code = (
            "PROP_EVENT_IDENTITY_UNRESOLVED"
            if exc.code == "PROP_EVENT_IDENTITY_CONFLICT"
            else exc.code
        )
        raise PropAutoHydrationError(
            code,
            str(exc),
            detail={**exc.detail, "underlying_code": exc.code, "sport": "NFL"},
        ) from exc

    resolved_provider_id = _text(target.get("event_id"))
    requested_provider_id = _text(provider_event_id)
    if requested_provider_id and requested_provider_id != resolved_provider_id:
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_UNRESOLVED",
            "provided NFL event alias did not match the verified ESPN event",
            detail={
                "provider": "ESPN",
                "provided_provider_event_id": requested_provider_id,
                "verified_provider_event_id": resolved_provider_id,
                "player": official_name,
                "event_start_time": event_start.isoformat(),
            },
        )

    canonical_event_id = _text(target.get("verified_canonical_event_id")).upper()
    if not _canonical_nfl_event_id(canonical_event_id):
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_UNRESOLVED",
            "verified NFL provider event did not produce a valid canonical event id",
            detail={
                "provider": "ESPN",
                "provider_event_id": resolved_provider_id,
                "player": official_name,
            },
        )

    current = _text(current_event_id)
    if current and _canonical_nfl_event_id(current) and current.upper() != canonical_event_id:
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "caller canonical NFL event id conflicts with verified provider identity",
            detail={
                "blocker": "NFL_CANONICAL_EVENT_ID_MISMATCH",
                "caller_canonical_event_id": current,
                "verified_canonical_event_id": canonical_event_id,
                "provider": "ESPN",
                "provider_event_id": resolved_provider_id,
            },
        )

    return {
        "status": "PASS",
        "sport": "NFL",
        "player": official_name,
        "team": team,
        "opponent": target.get("opponent"),
        "canonical_event_id": canonical_event_id,
        "official_event_id": canonical_event_id,
        "provider_event_ids": {"ESPN": resolved_provider_id},
        "provider_season": target.get("provider_season"),
        "provider_week": target.get("provider_week"),
        "canonical_home_team": target.get("canonical_home_team"),
        "canonical_away_team": target.get("canonical_away_team"),
        "source_event_id_alias": current or None,
        "identity_binding_status": "VERIFIED_PROVIDER_TO_CANONICAL",
        "probability_publishable": False,
        "can_execute": False,
    }


def prop_lane_capability(market_api: Any, sport: str, stat_type: str) -> dict[str, Any]:
    """Read-only exact lane preflight using existing specialist/artifact authority."""
    normalized_sport = _text(sport).upper()
    canonical_stat = pick_core._canonical_stat(normalized_sport, stat_type)

    try:
        specialist = market_api.prod.base_api._controlling_specialist_provider(
            normalized_sport,
            canonical_stat,
        )
    except Exception as exc:
        return {
            "sport": normalized_sport,
            "requested_stat_type": stat_type,
            "canonical_stat_type": canonical_stat,
            "status": "BLOCKED",
            "code": "SPECIALIST_ROUTING_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "scoreable": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    specialist_name = (
        specialist.get("controlling_specialist")
        if isinstance(specialist, Mapping)
        else None
    )
    specialist_ready = bool(
        specialist_name and specialist_name != "MODEL_UNAVAILABLE"
    )

    try:
        aggregate = market_api.prod._runtime_capability(
            market_api.prod.PROP_CAPABILITY_KEY
        )
    except Exception as exc:
        aggregate = {
            "capability_status": "UNAVAILABLE",
            "code": "PROP_CAPABILITY_LOOKUP_FAILED",
            "error_type": type(exc).__name__,
        }
    aggregate_ready = bool(
        isinstance(aggregate, Mapping)
        and aggregate.get("capability_status") == "AVAILABLE"
    )

    try:
        artifact = market_api._prop_route_artifact(normalized_sport, canonical_stat)
    except Exception as exc:
        artifact = {
            "ok": False,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_LOOKUP_FAILED",
            "error_type": type(exc).__name__,
        }
    artifact_ready = bool(
        isinstance(artifact, Mapping)
        and artifact.get("ok") is True
        and artifact.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_READY"
    )

    if not specialist_ready:
        code = "MODEL_UNAVAILABLE" if specialist_name == "MODEL_UNAVAILABLE" else "SPECIALIST_ROUTING_UNAVAILABLE"
    elif not aggregate_ready:
        code = "PROP_PROBABILITY_UNAVAILABLE"
    elif not artifact_ready:
        code = str(artifact.get("code") or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND")
    else:
        code = "PROP_LANE_CERTIFIED_PRODUCTION"

    scoreable = specialist_ready and aggregate_ready and artifact_ready
    provider = hydration_router.provider_for_sport(normalized_sport, canonical_stat)
    return {
        "sport": normalized_sport,
        "requested_stat_type": stat_type,
        "canonical_stat_type": canonical_stat,
        "status": "AVAILABLE" if scoreable else "UNAVAILABLE",
        "code": code,
        "scoreable": scoreable,
        "controlling_specialist": specialist_name or "MODEL_UNAVAILABLE",
        "specialist_registered": specialist_ready,
        "aggregate_prop_capability": (
            aggregate.get("capability_status")
            if isinstance(aggregate, Mapping)
            else "UNAVAILABLE"
        ),
        "artifact_ready": artifact_ready,
        "artifact_code": artifact.get("code") if isinstance(artifact, Mapping) else None,
        "model_artifact_version": artifact.get("model_artifact_version") if isinstance(artifact, Mapping) else None,
        "specialist_version": artifact.get("specialist_version") if isinstance(artifact, Mapping) else None,
        "automatic_row_hydration_provider": provider,
        "automatic_row_hydration_supported": provider != hydration_router.UNREGISTERED_PROVIDER,
        "probability_publishable": False,
        "can_execute": False,
    }


class PropLaneCapabilityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_key: Optional[str] = None
    sport: str
    stat_type: str


class PropLaneCapabilityBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[PropLaneCapabilityRow] = Field(min_length=1, max_length=50)


class NFLIdentityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_key: Optional[str] = None
    player: str
    event_start_time: str
    opponent: Optional[str] = None
    current_event_id: Optional[str] = None
    provider_event_id: Optional[str] = None


class NFLIdentityBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[NFLIdentityRow] = Field(min_length=1, max_length=50)


def install_boundary_read_routes(
    app: Any,
    *,
    market_api: Any,
    auth_dependency: Any,
) -> None:
    if not any(getattr(route, "path", None) == "/v17/prop-lane-capabilities" for route in app.router.routes):
        @app.post(
            "/v17/prop-lane-capabilities",
            dependencies=[auth_dependency],
            operation_id="preflightWowV17PropLanes",
        )
        def preflight_v17_prop_lanes(batch: PropLaneCapabilityBatch) -> dict[str, Any]:
            rows = []
            for index, requested in enumerate(batch.rows):
                result = prop_lane_capability(market_api, requested.sport, requested.stat_type)
                result["row_key"] = requested.row_key or f"row-{index + 1}"
                rows.append(result)
            return {
                "rows_in": len(rows),
                "rows_scoreable": sum(row.get("scoreable") is True for row in rows),
                "rows": rows,
                "aggregate_prop_capability_is_not_lane_authority": True,
                "can_execute": False,
            }

    if not any(getattr(route, "path", None) == "/v17/nfl-event-identity/resolve" for route in app.router.routes):
        @app.post(
            "/v17/nfl-event-identity/resolve",
            dependencies=[auth_dependency],
            operation_id="resolveWowV17NflEventIdentity",
        )
        def resolve_v17_nfl_event_identity(batch: NFLIdentityBatch) -> dict[str, Any]:
            rows: list[dict[str, Any]] = []
            for index, requested in enumerate(batch.rows):
                row_key = requested.row_key or f"row-{index + 1}"
                try:
                    result = resolve_nfl_event_identity(**requested.model_dump(exclude={"row_key"}))
                    result["row_key"] = row_key
                except PropAutoHydrationError as exc:
                    result = {
                        "row_key": row_key,
                        "status": "BLOCKED",
                        "code": exc.code,
                        "detail": exc.detail,
                        "probability_publishable": False,
                        "can_execute": False,
                    }
                rows.append(result)
            return {
                "rows_in": len(rows),
                "rows_resolved": sum(row.get("status") == "PASS" for row in rows),
                "rows": rows,
                "probability_publishable": False,
                "can_execute": False,
            }


def _install_hydration_error_semantics() -> None:
    if getattr(hydration_router, "_wow_nfl_identity_error_semantics", False):
        return

    def governed_hydrate(**kwargs: Any) -> dict[str, Any]:
        try:
            return _ORIGINAL_ROUTER_HYDRATE(**kwargs)
        except PropAutoHydrationError as exc:
            if _text(kwargs.get("sport")).upper() == "NFL" and exc.code == "PROP_EVENT_IDENTITY_CONFLICT":
                raise PropAutoHydrationError(
                    "PROP_EVENT_IDENTITY_UNRESOLVED",
                    str(exc),
                    detail={**exc.detail, "underlying_code": exc.code},
                ) from exc
            raise

    hydration_router.auto_hydrate_prop_evidence = governed_hydrate
    pick_facade.auto_hydrate_prop_evidence = governed_hydrate
    interactive_hydration.auto_hydrate_prop_evidence = governed_hydrate
    setattr(hydration_router, "_wow_nfl_identity_error_semantics", True)


def _typed_parallel_failure(row: Any, index: int, exc: Exception) -> dict[str, Any]:
    detail: dict[str, Any]
    status = "HELD"
    if isinstance(exc, HTTPException):
        detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        code = str(detail.get("code") or "ROW_ORCHESTRATION_FAILED")
        if exc.status_code < 500 and exc.status_code not in {409, 429}:
            status = "REJECTED"
    else:
        code = "ROW_ORCHESTRATION_FAILED"
        detail = {
            "error_type": type(exc).__name__,
            "specialist_scoring_attempted": False,
            "scoring_attempted": False,
            "specialist_invoked": False,
        }

    detail.setdefault("specialist_scoring_attempted", bool(detail.get("specialist_invoked") is True))
    detail.setdefault("scoring_attempted", bool(detail.get("specialist_scoring_attempted") is True))
    detail.setdefault("specialist_invoked", bool(detail.get("specialist_scoring_attempted") is True))
    return pick_core._terminal(
        interactive_parallel._row_key(row, index),
        status,
        code,
        detail=detail,
        acquisition={
            "mode": "INTERACTIVE_PARALLEL_ROW",
            "status": "FAILED",
            "can_execute": False,
        },
    )


def _typed_no_prediction(durable: dict[str, Any]) -> dict[str, Any]:
    state = durable.get("durable_row_state") if isinstance(durable.get("durable_row_state"), dict) else {}
    return {
        **durable,
        "status": "DURABLE_TERMINAL",
        "code": "NO_PREDICTION_CREATED",
        "prediction_created": False,
        "prediction_id": None,
        "detail": {
            "terminal_code": state.get("terminal_code"),
            "failure_domain": state.get("failure_domain"),
            "current_stage": state.get("current_stage"),
            "model_evaluated": state.get("model_evaluated") is True,
            "reason": "ROW_TERMINATED_BEFORE_PREDICTION_CREATION",
        },
        "matches": [],
        "retry_allowed": False,
        "can_execute": False,
    }


def _install_receipt_semantics() -> None:
    if getattr(receipt_runtime, "_wow_no_prediction_receipt_semantics", False):
        return

    def governed_lookup(db: Any, batch: receipt_runtime.PredictionReceiptLookupBatch) -> dict[str, Any]:
        outcomes: list[dict[str, Any]] = []
        for index, requested in enumerate(batch.rows):
            row_key = requested.row_key or f"row-{index + 1}"
            normalized_request = requested.model_copy(update={"row_key": row_key})
            durable: Optional[dict[str, Any]] = None
            if _text(batch.request_id) and not _text(requested.prediction_id):
                durable = receipt_runtime._durable_recovery_outcome(
                    db,
                    request_id=batch.request_id,
                    requested=normalized_request,
                    row_key=row_key,
                )
                if (
                    isinstance(durable, dict)
                    and durable.get("status") == "DURABLE_TERMINAL"
                    and (durable.get("durable_row_state") or {}).get("model_evaluated") is not True
                    and not _text((durable.get("durable_row_state") or {}).get("prediction_id"))
                ):
                    outcomes.append(_typed_no_prediction(durable))
                    continue

            single = receipt_runtime.PredictionReceiptLookupBatch(
                request_id=batch.request_id,
                rows=[normalized_request],
            )
            original_result = _ORIGINAL_RECEIPT_LOOKUP(db, single)
            original_rows = original_result.get("rows") if isinstance(original_result, dict) else None
            outcome = (
                dict(original_rows[0])
                if isinstance(original_rows, list) and len(original_rows) == 1 and isinstance(original_rows[0], dict)
                else {
                    "row_key": row_key,
                    "status": "BLOCKED",
                    "code": "PREDICTION_RECEIPT_LOOKUP_FAILED",
                    "matches": [],
                    "can_execute": False,
                }
            )
            if outcome.get("code") == "PREDICTION_RECEIPT_LOOKUP_FAILED" and isinstance(durable, dict):
                if (
                    durable.get("status") == "DURABLE_TERMINAL"
                    and (durable.get("durable_row_state") or {}).get("model_evaluated") is not True
                    and not _text((durable.get("durable_row_state") or {}).get("prediction_id"))
                ):
                    outcome = _typed_no_prediction(durable)
                elif durable.get("status") != "BLOCKED":
                    outcome = durable
            outcomes.append(outcome)

        matched = sum(row.get("status") == "MATCHED" for row in outcomes)
        ambiguous = sum(row.get("status") == "AMBIGUOUS" for row in outcomes)
        not_found = sum(row.get("status") == "NOT_FOUND" for row in outcomes)
        unresolved = sum(row.get("status") == "UNRESOLVED" for row in outcomes)
        durable_terminal = sum(row.get("status") in {"DURABLE_MODEL_COMPLETE", "DURABLE_TERMINAL"} for row in outcomes)
        blocked = len(outcomes) - matched - ambiguous - not_found - unresolved - durable_terminal
        return {
            "request_id": batch.request_id,
            "rows_in": len(batch.rows),
            "rows_matched": matched,
            "rows_ambiguous": ambiguous,
            "rows_not_found": not_found,
            "rows_blocked": blocked,
            "rows_unresolved": unresolved,
            "rows_durable_terminal": durable_terminal,
            "reconciliation_pass": len(outcomes) == len(batch.rows),
            "rows": outcomes,
            "can_execute": False,
        }

    receipt_runtime.lookup_prediction_receipts = governed_lookup
    setattr(receipt_runtime, "_wow_no_prediction_receipt_semantics", True)


def install_boundary_semantic_patches() -> None:
    if getattr(interactive_parallel, _PATCH_KEY, False):
        return
    _install_hydration_error_semantics()
    interactive_parallel._unexpected_row_failure = _typed_parallel_failure
    _install_receipt_semantics()
    setattr(interactive_parallel, _PATCH_KEY, True)


def _lane_scoreable(market_api: Any, row: Any) -> bool:
    result = prop_lane_capability(market_api, row.sport, row.stat_type)
    return result.get("scoreable") is True


def _evidence_verified_event(row: Any) -> Optional[str]:
    evidence = getattr(row, "evidence", None)
    role = getattr(evidence, "role_status", None) if evidence is not None else None
    if isinstance(role, Mapping):
        value = _text(role.get("verified_canonical_event_id")).upper()
        if _canonical_nfl_event_id(value):
            return value
    return None


def _resolve_batch_nfl_identity(batch: Any, *, market_api: Any) -> tuple[Any, dict[str, dict[str, Any]]]:
    rows = list(batch.rows)
    changed = False
    receipts: dict[str, dict[str, Any]] = {}
    cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for index, row in enumerate(rows):
        row_key = row.row_key or f"row-{index + 1}"
        if _text(row.sport).upper() != "NFL":
            continue
        if not _lane_scoreable(market_api, row):
            receipts[row_key] = {
                "status": "SKIPPED_EXACT_LANE_UNAVAILABLE",
                "source_event_id_alias": row.event_id,
                "can_execute": False,
            }
            continue
        if _canonical_nfl_event_id(row.event_id):
            receipts[row_key] = {
                "status": "CANONICAL_INPUT_PENDING_PROVIDER_VERIFICATION",
                "canonical_event_id": _text(row.event_id).upper(),
                "can_execute": False,
            }
            continue

        verified = _evidence_verified_event(row)
        result: Optional[dict[str, Any]] = None
        if verified:
            result = {
                "status": "PASS",
                "canonical_event_id": verified,
                "official_event_id": verified,
                "source_event_id_alias": row.event_id,
                "identity_binding_status": "VERIFIED_FROZEN_EVIDENCE_TO_CANONICAL",
                "can_execute": False,
            }
        else:
            key = (
                _text(row.player).casefold(),
                _text(row.event_start_time),
                _text(row.opponent).upper(),
                _text(row.event_id),
            )
            if key not in cache:
                try:
                    cache[key] = resolve_nfl_event_identity(
                        player=row.player,
                        event_start_time=row.event_start_time,
                        opponent=row.opponent,
                        provider_event_id=_provider_alias_from_input(row.event_id),
                        current_event_id=row.event_id,
                    )
                except PropAutoHydrationError as exc:
                    cache[key] = {
                        "status": "BLOCKED",
                        "code": exc.code,
                        "detail": exc.detail,
                        "source_event_id_alias": row.event_id,
                        "can_execute": False,
                    }
            result = cache[key]

        receipts[row_key] = dict(result)
        canonical = _text(result.get("canonical_event_id")).upper() if result.get("status") == "PASS" else ""
        if canonical and _canonical_nfl_event_id(canonical):
            rows[index] = row.model_copy(update={"event_id": canonical})
            changed = True

    return (batch.model_copy(update={"rows": rows}) if changed else batch), receipts


def _invoke_endpoint(endpoint: Any, batch: Any, model_identity: Optional[str]) -> Any:
    try:
        parameters = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "x_wow_model_identity" in parameters:
        return endpoint(batch, x_wow_model_identity=model_identity)
    return endpoint(batch)


def install_final_score_boundary_wrapper(app: Any, *, market_api: Any) -> bool:
    if getattr(app.state, _STATE_KEY, False):
        return True
    route = next(
        (
            candidate
            for candidate in app.router.routes
            if getattr(candidate, "path", None) == "/score-pick-request"
            and "POST" in (getattr(candidate, "methods", set()) or set())
        ),
        None,
    )
    if route is None or not callable(getattr(route, "endpoint", None)):
        return False

    captured = route.endpoint
    dependencies = list(getattr(route, "dependencies", None) or [])
    operation_id = getattr(route, "operation_id", None) or "scoreWowPickRequest"
    app.router.routes[:] = [candidate for candidate in app.router.routes if candidate is not route]

    @app.post(
        "/score-pick-request",
        dependencies=dependencies,
        operation_id=operation_id,
    )
    def score_pick_request_identity_bound(
        batch: pick_core.PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ) -> Any:
        prepared, identity_receipts = _resolve_batch_nfl_identity(batch, market_api=market_api)
        response = _invoke_endpoint(captured, prepared, x_wow_model_identity)
        if isinstance(response, dict):
            response["can_execute"] = False
            response["nfl_event_identity_resolution"] = {
                "rows": identity_receipts,
                "canonical_identity_rewrites": sum(
                    receipt.get("status") == "PASS"
                    and bool(receipt.get("canonical_event_id"))
                    and receipt.get("source_event_id_alias") != receipt.get("canonical_event_id")
                    for receipt in identity_receipts.values()
                ),
                "probability_mutated": False,
                "can_execute": False,
            }
            response_rows = response.get("rows")
            if isinstance(response_rows, list):
                for outcome in response_rows:
                    if not isinstance(outcome, dict):
                        continue
                    receipt = identity_receipts.get(str(outcome.get("row_key") or ""))
                    if receipt is not None:
                        outcome["event_identity_resolution"] = receipt
        return response

    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_final_score_boundary_wrapper(app: Any, *, market_api: Any) -> None:
    if getattr(app.state, _STARTUP_KEY, False):
        return

    @app.on_event("startup")
    async def _install_final_nfl_prop_boundary() -> None:
        install_final_score_boundary_wrapper(app, market_api=market_api)

    setattr(app.state, _STARTUP_KEY, True)


def install_nfl_prop_boundary_integrity(
    app: Any,
    *,
    market_api: Any,
    auth_dependency: Any,
) -> None:
    """Install read-only preflight surfaces and schedule final boundary repair."""
    install_boundary_semantic_patches()
    install_boundary_read_routes(
        app,
        market_api=market_api,
        auth_dependency=auth_dependency,
    )
    # This scheduler must be registered after the existing hydration, parallel,
    # durable-state, and run-control schedulers so it captures the final route.
    schedule_final_score_boundary_wrapper(app, market_api=market_api)


__all__ = [
    "NFLIdentityBatch",
    "NFLIdentityRow",
    "PropLaneCapabilityBatch",
    "PropLaneCapabilityRow",
    "install_boundary_read_routes",
    "install_boundary_semantic_patches",
    "install_final_score_boundary_wrapper",
    "install_nfl_prop_boundary_integrity",
    "prop_lane_capability",
    "resolve_nfl_event_identity",
    "schedule_final_score_boundary_wrapper",
]
