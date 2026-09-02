"""Canonical MLB 1IP pick-request ingress helper.

This helper is called only after the canonical pick-request runtime has passed
specialist, aggregate-capability, and certified-artifact preflight. That order
is deliberate: a genuinely missing certified model remains MODEL_UNAVAILABLE
without spending acquisition work, while acquisition/runtime failures after a
READY artifact are classified at their actual layer instead of masquerading as
model unavailability.

The helper preserves the mandatory Scout -> Research barrier before the
controlling 1IP specialist, and queues provisional-lineup results for automatic
final refresh. Missing refresh-queue infrastructure may hold scheduling but may
not erase an already-computed sporting probability. can_execute is always
false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mlb_1ip_empirical_specialist import score_mlb_1ip_empirical
from mlb_1ip_live_acquisition import PROVIDER as MLB_1IP_PROVIDER
from mlb_1ip_live_acquisition import hydrate_mlb_1ip_evidence
from mlb_1ip_specialist import starter_changed
from prop_auto_hydration import PropAutoHydrationError

CAN_EXECUTE = False
REFRESH_DELAY_SECONDS = 300
MLB_1IP_STAT_TYPE = "1ST_INNING_PITCHES_THROWN"


def _acquisition_failure(
    *,
    row_key: str,
    exc: PropAutoHydrationError,
    terminal: Callable[..., dict[str, Any]],
    acquisition: dict[str, Any],
) -> dict[str, Any]:
    code = str(exc.code)
    detail = {**exc.detail, "message": str(exc), "specialist_invoked": False}

    if code == "EVENT_ALREADY_STARTED":
        detail["terminal_label"] = "NO_PLAY"
        status = "REJECTED"
    elif code in {
        "MLB_1IP_PRIOR_SAMPLE_INSUFFICIENT",
        "PROP_PLAYER_IDENTITY_UNRESOLVED",
        "PROP_EVENT_IDENTITY_CONFLICT",
        "MLB_STARTER_STATUS_UNRESOLVED",
    }:
        detail["terminal_label"] = "REJECT_DATA_QUALITY"
        status = "REJECTED"
    else:
        detail["terminal_label"] = "RESEARCH_INTEREST"
        status = "HELD"

    return terminal(row_key, status, code, detail=detail, acquisition=acquisition)


def _queue_provisional_refresh(
    *,
    row: Any,
    row_key: str,
    request_id: str | None,
    lineup_evidence: dict[str, Any],
    market_api: Any,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    payload = {
        "row_key": row_key,
        "request_id": request_id,
        "event_id": str(row.event_id),
        "event_start_time": str(row.event_start_time),
        "player": str(row.player),
        "starter_name_at_capture": str(
            lineup_evidence.get("starter_name_at_capture")
            or lineup_evidence.get("starter_name")
            or row.player
        ),
        "line": float(row.line),
        "direction": str(row.direction).strip().upper(),
        "money_lane_status": str(row.money_lane_status or "PAYOUT_UNRESOLVED"),
        "status": "WAITING_FOR_OFFICIAL_LINEUP",
        "last_refresh_at": None,
        "next_refresh_at": (now + timedelta(seconds=REFRESH_DELAY_SECONDS)).isoformat(),
        "refresh_attempts": 0,
        "provisional_evidence": lineup_evidence,
        "refreshed_evidence": None,
        "rerun_result": None,
        "rerun_completed_at": None,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "last_error_code": None,
        "probability_publishable": False,
        "can_execute": False,
    }
    try:
        response = (
            market_api.prod.get_client()
            .table("wow_mlb_1ip_refresh_queue")
            .upsert(payload, on_conflict="row_key,event_id,player")
            .execute()
        )
        stored = list(getattr(response, "data", None) or [])
        return {
            "status": "QUEUED",
            "queue_id": stored[0].get("queue_id") if stored and isinstance(stored[0], dict) else None,
            "next_refresh_at": payload["next_refresh_at"],
            "can_execute": False,
        }
    except Exception as exc:
        return {
            "status": "PERSISTENCE_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "next_refresh_at": payload["next_refresh_at"],
            "can_execute": False,
        }


def _resolve_empirical_artifact(
    *,
    market_api: Any,
    row_key: str,
    terminal: Callable[..., dict[str, Any]],
    acquisition: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Re-resolve the exact artifact immediately before scoring.

    The outer pick runtime already performs this preflight. Re-resolution here
    closes a time-of-check/time-of-use gap and provides the exact payload to the
    controlling specialist. Registry transport failure is an infrastructure
    HOLD, while a genuinely absent certified artifact remains MODEL_UNAVAILABLE.
    """
    route = market_api._prop_route_artifact("MLB", MLB_1IP_STAT_TYPE)
    if route.get("ok") is True and route.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
        return route, None

    code = str(route.get("code") or "PROP_MODEL_REGISTRY_INVALID_RESPONSE")
    if code in {"PROP_MODEL_REGISTRY_UNAVAILABLE", "PROP_MODEL_REGISTRY_INVALID_RESPONSE"}:
        return None, terminal(
            row_key,
            "HELD",
            code,
            detail={
                "terminal_label": "RESEARCH_INTEREST",
                "specialist_invoked": False,
                "stage": "CERTIFIED_ARTIFACT_RECHECK",
            },
            acquisition=acquisition,
        )

    return None, terminal(
        row_key,
        "HELD",
        "MODEL_UNAVAILABLE",
        detail={
            "terminal_label": "MODEL_UNAVAILABLE",
            "blocker_code": code,
            "sport": "MLB",
            "stat_type": MLB_1IP_STAT_TYPE,
            "specialist_invoked": False,
            "stage": "CERTIFIED_ARTIFACT_RECHECK",
        },
        acquisition=acquisition,
    )


def score_mlb_1ip_ingress(
    *,
    row: Any,
    row_key: str,
    market_api: Any,
    request_id: str | None,
    run_research: Callable[..., tuple[bool, dict[str, Any]]],
    terminal: Callable[..., dict[str, Any]],
    reduce_terminal: Callable[..., Any],
) -> dict[str, Any]:
    """Acquire/score one preflight-approved MLB 1IP row."""
    caller_evidence = getattr(row, "evidence", None)
    supplied_lineup = getattr(caller_evidence, "lineup_evidence", None) if caller_evidence is not None else None

    if isinstance(supplied_lineup, dict):
        lineup_evidence = supplied_lineup
        role_status = getattr(caller_evidence, "role_status", None) or {
            "status": lineup_evidence.get("starter_status"),
            "role": "STARTING_PITCHER",
        }
        acquisition = {
            "mode": "CALLER_SUPPLIED_RAW_EVIDENCE",
            "status": "PASS",
            "source_type": row.source_type,
            "platform": row.platform,
            "can_execute": False,
        }
    else:
        acquisition = {
            "mode": "AUTO_HYDRATION",
            "status": "ATTEMPTED",
            "provider": MLB_1IP_PROVIDER,
            "source_type": row.source_type,
            "platform": row.platform,
            "can_execute": False,
        }
        try:
            lineup_evidence = hydrate_mlb_1ip_evidence(
                player=row.player,
                event_start_time=row.event_start_time,
            )
            acquisition["status"] = "PASS"
            role_status = {
                "status": lineup_evidence.get("starter_status"),
                "role": "STARTING_PITCHER",
                "confirmation_strength": "OFFICIAL_MLB_STATS_API",
                "provider": MLB_1IP_PROVIDER,
            }
        except PropAutoHydrationError as exc:
            acquisition["status"] = "FAILED"
            return _acquisition_failure(
                row_key=row_key,
                exc=exc,
                terminal=terminal,
                acquisition=acquisition,
            )
        except Exception as exc:
            acquisition["status"] = "FAILED"
            return terminal(
                row_key,
                "HELD",
                "MLB_1IP_AUTO_HYDRATION_INTERNAL_ERROR",
                detail={
                    "terminal_label": "RESEARCH_INTEREST",
                    "error_type": type(exc).__name__,
                    "specialist_invoked": False,
                },
                acquisition=acquisition,
            )

    if starter_changed(
        lineup_evidence.get("starter_name_at_capture"),
        lineup_evidence.get("starter_name"),
    ):
        return terminal(
            row_key,
            "REJECTED",
            "SLATE_PURGE",
            detail={
                "terminal_label": "SLATE_PURGE",
                "reason": "STARTER_CHANGED",
                "specialist_invoked": False,
            },
            acquisition=acquisition,
        )

    run_id = f"pick-request-1ip-{row_key}"
    candidate = {
        "sport": "MLB",
        "market_family": "PLAYER_PROP",
        "official_event_id": row.event_id,
        "event_start_utc": row.event_start_time,
        "evidence": {
            "lineup_evidence": lineup_evidence,
            "role_status": role_status,
        },
    }
    ok, barrier_detail = run_research(row_key=row_key, run_id=run_id, candidate=candidate)
    if not ok:
        return terminal(
            row_key,
            "HELD",
            "SCOUT_RESEARCH_BARRIER_BLOCKED",
            detail={
                "terminal_label": "RESEARCH_INTEREST",
                "stage": barrier_detail["stage"],
                "blocker": barrier_detail["blockers"][0] if barrier_detail["blockers"] else "SCOUT_RESEARCH_BARRIER_BLOCKED",
                "scout_research_barrier": barrier_detail,
                "specialist_invoked": False,
            },
            acquisition=acquisition,
        )

    artifact, artifact_failure = _resolve_empirical_artifact(
        market_api=market_api,
        row_key=row_key,
        terminal=terminal,
        acquisition=acquisition,
    )
    if artifact_failure is not None:
        return artifact_failure
    assert artifact is not None

    money_lane_status = str(row.money_lane_status or "").strip().upper()
    market_evidence_present = money_lane_status not in {"", "PAYOUT_UNRESOLVED"}

    try:
        result = score_mlb_1ip_empirical(
            artifact_record=artifact,
            starter_status=lineup_evidence.get("starter_status", ""),
            official_lineup_status=lineup_evidence.get("official_lineup_status", ""),
            projected_top_four=lineup_evidence.get("projected_top_four"),
            line_value=row.line,
            side=row.direction,
            failure_path_prior=lineup_evidence.get("failure_path_prior"),
            market_evidence_present=market_evidence_present,
        )
    except Exception as exc:
        # The controlling specialist was invoked but raised (timeout, exception,
        # or otherwise failed to produce a scored package) — this is an
        # invocation-level failure, distinct from MODEL_OUTPUT_INVALID which is
        # reserved for a specialist that returns but with a malformed value.
        return terminal(
            row_key,
            "HELD",
            "MODEL_SCORER_FAILED",
            detail={
                "terminal_label": "MODEL_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "specialist_invoked": True,
                "stage": "MLB_1IP_EMPIRICAL_SPECIALIST",
            },
            acquisition=acquisition,
        )
    result["scout_research_barrier"] = barrier_detail

    if not result["model_evaluated"]:
        return terminal(
            row_key,
            "REJECTED",
            result["code"],
            detail={
                "terminal_label": result["terminal_label"],
                "blockers": result["blockers"],
                "lineup_evidence_state": result.get("lineup_evidence_state"),
                "scout_research_barrier": barrier_detail,
                "specialist_invoked": False,
            },
            acquisition=acquisition,
        )

    refresh_queue = {"status": "NOT_REQUIRED", "can_execute": False}
    if result.get("final_refresh_required") is True:
        refresh_queue = _queue_provisional_refresh(
            row=row,
            row_key=row_key,
            request_id=request_id,
            lineup_evidence=lineup_evidence,
            market_api=market_api,
        )
        if refresh_queue["status"] != "QUEUED":
            result["blockers"] = list(result.get("blockers") or []) + [
                "FINAL_REFRESH_QUEUE_PERSISTENCE_UNAVAILABLE"
            ]

    decision = reduce_terminal(
        proposed_label=result["terminal_label"],
        blockers=result["blockers"],
        model_evaluated=True,
    )
    return {
        "row_key": row_key,
        "terminal_status": "REJECTED" if decision.pick_rejected else "COMPLETED",
        "code": decision.terminal_label,
        "terminal_label": decision.terminal_label,
        "lineup_evidence_state": result["lineup_evidence_state"],
        "final_refresh_required": result["final_refresh_required"],
        "refresh_queue": refresh_queue,
        "model_evaluated": True,
        "pick_rejected": decision.pick_rejected,
        "verdict_class": decision.verdict_class,
        "infrastructure_blocked": decision.infrastructure_blocked,
        "acquisition": acquisition,
        "result": result,
        "probability_publishable": False,
        "can_execute": False,
    }
