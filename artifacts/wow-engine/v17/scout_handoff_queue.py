"""Durable asynchronous Scout -> specialist handoff primitives.

Scout remains evidence-only. This module maps an already-governed discovery
handoff into compact, immutable queue envelopes and provides a DB-leased worker
that calls the existing canonical prop/team-event scorers in-process.

No probability is created, blended, inferred from a sportsbook, or modified
here. V17 terminal authority and can_execute=false are preserved.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

import pick_request_runtime_core as pick_runtime
import team_event_request_runtime as team_runtime
from v17.multiscout_auto_advance import build_dispatch

CAN_EXECUTE = False
WORKER_VERSION = "V17_SCOUT_DURABLE_HANDOFF_V1"
LEASE_SECONDS = 900
POLL_SECONDS = 2.0

TargetLane = Literal["WOW_PROP_LANE", "LLP_TEAM_BETTING_ENGINE"]
RedTeamStatus = Literal["RED_TEAM_PASSED", "HANDOFF_BLOCKED"]


class ScoutCandidateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_id: str
    research_run_id: str
    candidate_id: str
    target_lane: TargetLane
    target_route: Literal["/score-pick-request", "/score-team-event-request"]
    request_id: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    research_priority: Literal["HIGH", "MEDIUM", "LOW", "UNRANKED"] = "UNRANKED"
    red_team_status: RedTeamStatus
    red_team_rule_version: str = "V17_DETERMINISTIC_HANDOFF_HYGIENE_V1"
    blocked_code: str | None = None
    blocked_detail: dict[str, Any] | None = None
    can_execute: Literal[False] = False


class ScoutHandoffPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["wow.v17.scout-handoff-plan.v1"] = "wow.v17.scout-handoff-plan.v1"
    source_run_id: str
    research_run_id: str
    candidates: list[ScoutCandidateEnvelope]
    mapping: dict[str, Any]
    governance: dict[str, Any]
    can_execute: Literal[False] = False


def _stable_id(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "cand_" + hashlib.sha256(raw).hexdigest()[:24]


def _priority(value: Any) -> str:
    label = str(value or "").strip().upper()
    return label if label in {"HIGH", "MEDIUM", "LOW"} else "UNRANKED"


def _forbidden_probability_payload(payload: dict[str, Any]) -> list[str]:
    forbidden = {
        "model_probability",
        "specialist_probability",
        "calibrated_probability",
        "calibrated_lower_bound",
        "calibrated_upper_bound",
        "probability_publishable",
    }
    return sorted(key for key in forbidden if key in payload)


def _red_team_hygiene(
    *,
    target_lane: str,
    target_route: str,
    request_payload: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Deterministic handoff hygiene only; never a narrative/model judgment."""
    expected = {
        "WOW_PROP_LANE": "/score-pick-request",
        "LLP_TEAM_BETTING_ENGINE": "/score-team-event-request",
    }.get(target_lane)
    if expected != target_route:
        return False, "SCOUT_HANDOFF_ROUTE_LANE_MISMATCH", {
            "target_lane": target_lane,
            "target_route": target_route,
        }
    forbidden = _forbidden_probability_payload(request_payload)
    if forbidden:
        return False, "SCOUT_PROBABILITY_AUTHORITY_VIOLATION", {
            "forbidden_fields": forbidden,
        }
    if not request_payload:
        return False, "SCOUT_HANDOFF_PAYLOAD_EMPTY", None
    return True, None, None


def _raw_candidate_priority(
    handoff: dict[str, Any], lane: str, source_index: int | None
) -> str:
    if source_index is None or source_index < 1:
        return "UNRANKED"
    model_handoff = handoff.get("model_handoff")
    if not isinstance(model_handoff, dict):
        return "UNRANKED"
    key = "prop_candidates" if lane == "WOW_PROP_LANE" else "team_event_candidates"
    rows = model_handoff.get(key)
    if not isinstance(rows, list) or source_index > len(rows):
        return "UNRANKED"
    candidate = rows[source_index - 1]
    if not isinstance(candidate, dict):
        return "UNRANKED"
    return _priority(
        candidate.get("research_priority")
        or candidate.get("priority")
        or candidate.get("research_interest")
    )


def build_handoff_plan(handoff: dict[str, Any]) -> ScoutHandoffPlan:
    """Build compact row-isolated queue envelopes from the existing mapper."""
    dispatch = build_dispatch(handoff)
    source_run_id = str(dispatch["source_run_id"])
    research_run_id = str(dispatch["research_run_id"])
    candidates: list[ScoutCandidateEnvelope] = []

    for batch in dispatch["prop_batches"]:
        for row in batch["rows"]:
            payload = dict(row)
            candidate_id = _stable_id(source_run_id, "PROP", payload)
            target_lane = "WOW_PROP_LANE"
            route = "/score-pick-request"
            passed, code, detail = _red_team_hygiene(
                target_lane=target_lane, target_route=route, request_payload=payload
            )
            candidates.append(
                ScoutCandidateEnvelope(
                    source_run_id=source_run_id,
                    research_run_id=research_run_id,
                    candidate_id=candidate_id,
                    target_lane=target_lane,
                    target_route=route,
                    request_id=f"{research_run_id}:scout:{candidate_id}",
                    request_payload=payload,
                    red_team_status="RED_TEAM_PASSED" if passed else "HANDOFF_BLOCKED",
                    blocked_code=code,
                    blocked_detail=detail,
                )
            )

    for batch in dispatch["team_event_batches"]:
        for row in batch["rows"]:
            payload = dict(row)
            candidate_id = _stable_id(
                source_run_id,
                "TEAM_EVENT",
                payload.get("event_key"),
                payload.get("objective_lane"),
            )
            target_lane = "LLP_TEAM_BETTING_ENGINE"
            route = "/score-team-event-request"
            passed, code, detail = _red_team_hygiene(
                target_lane=target_lane, target_route=route, request_payload=payload
            )
            candidates.append(
                ScoutCandidateEnvelope(
                    source_run_id=source_run_id,
                    research_run_id=research_run_id,
                    candidate_id=candidate_id,
                    target_lane=target_lane,
                    target_route=route,
                    request_id=f"{research_run_id}:scout:{candidate_id}",
                    request_payload=payload,
                    red_team_status="RED_TEAM_PASSED" if passed else "HANDOFF_BLOCKED",
                    blocked_code=code,
                    blocked_detail=detail,
                )
            )

    # Mapping rejects are not silently discarded. They enter the ledger directly
    # as HANDOFF_BLOCKED and do not consume specialist capacity.
    for lane, route, rejected_key in (
        ("WOW_PROP_LANE", "/score-pick-request", "rejected_prop_rows"),
        ("LLP_TEAM_BETTING_ENGINE", "/score-team-event-request", "rejected_team_event_rows"),
    ):
        for rejected in dispatch["mapping"].get(rejected_key) or []:
            source_index = int(rejected.get("source_index") or 0)
            code = str(rejected.get("code") or "SCOUT_MAPPING_FAILED")
            candidate_id = _stable_id(source_run_id, lane, source_index, code)
            candidates.append(
                ScoutCandidateEnvelope(
                    source_run_id=source_run_id,
                    research_run_id=research_run_id,
                    candidate_id=candidate_id,
                    target_lane=lane,
                    target_route=route,
                    request_id=f"{research_run_id}:scout:{candidate_id}",
                    request_payload={},
                    research_priority=_raw_candidate_priority(handoff, lane, source_index),
                    red_team_status="HANDOFF_BLOCKED",
                    blocked_code=code,
                    blocked_detail={"source_index": source_index},
                )
            )

    # The mapping functions already reconcile duplicate props separately. A
    # duplicate observation is intentionally not a second candidate/job.
    governance = {
        **dict(dispatch["governance"]),
        "red_team_gate": "DETERMINISTIC_HANDOFF_HYGIENE_V1",
        "queue_probability_authority": False,
        "v17_terminal_reducer_is_terminal_authority": True,
        "can_execute": False,
    }
    return ScoutHandoffPlan(
        source_run_id=source_run_id,
        research_run_id=research_run_id,
        candidates=candidates,
        mapping=dict(dispatch["mapping"]),
        governance=governance,
        can_execute=False,
    )


def enqueue_plan(db: Any, plan: ScoutHandoffPlan) -> dict[str, Any]:
    payload = [candidate.model_dump(mode="json") for candidate in plan.candidates]
    result = db.rpc(
        "wow_enqueue_scout_handoff_batch",
        {"p_jobs": payload},
    ).execute()
    body = getattr(result, "data", None)
    if not isinstance(body, dict):
        raise RuntimeError("SCOUT_HANDOFF_ENQUEUE_RECEIPT_INVALID")
    return {
        "schema_version": "wow.v17.scout-handoff-enqueue.v1",
        "source_run_id": plan.source_run_id,
        "research_run_id": plan.research_run_id,
        "queue": body,
        "mapping": plan.mapping,
        "can_execute": False,
    }


def _claim(db: Any, worker_id: str) -> dict[str, Any] | None:
    result = db.rpc(
        "wow_claim_scout_handoff_job",
        {"p_worker_id": worker_id, "p_lease_seconds": LEASE_SECONDS},
    ).execute()
    rows = list(getattr(result, "data", None) or [])
    return dict(rows[0]) if rows else None


def _rpc_row(db: Any, function: str, params: dict[str, Any]) -> dict[str, Any]:
    result = db.rpc(function, params).execute()
    data = getattr(result, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise RuntimeError(f"{function.upper()}_RECEIPT_INVALID")
    return dict(data)


def _receipt_row(result: dict[str, Any]) -> dict[str, Any] | None:
    rows = result.get("outcomes") or result.get("rows") or []
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    return dict(rows[0])


def _v17_qualified(row: dict[str, Any]) -> bool:
    return bool(
        str(row.get("terminal_status") or row.get("status") or "").upper() == "COMPLETED"
        and row.get("probability_publishable") is True
        and row.get("rank_eligible") is True
        and row.get("card_admission_eligible", True) is True
    )


def _retry_safe(job: dict[str, Any]) -> bool:
    if str(job.get("target_lane")) == "WOW_PROP_LANE":
        return True
    payload = job.get("request_payload")
    if not isinstance(payload, dict):
        return False
    return (
        str(payload.get("sport") or "").upper() == "MLB"
        and str(payload.get("league") or "").upper() == "MLB"
        and bool(str(payload.get("research_run_id") or "").strip())
        and bool(str(payload.get("event_key") or "").strip())
    )


def _error_code(exc: Exception) -> tuple[str, int | None, dict[str, Any]]:
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail)}
        return str(detail.get("code") or f"HTTP_{exc.status_code}"), exc.status_code, detail
    return "SPECIALIST_RUNTIME_EXCEPTION", None, {"error_type": type(exc).__name__}


def _retry_budget(code: str, http_status: int | None) -> int:
    if http_status in {429, 502, 503, 504}:
        return 3
    if code in {
        "PROVIDER_UNAVAILABLE",
        "TRANSPORT_FAILURE",
        "MODEL_SERVICE_UNREACHABLE",
        "RECEIPT_SERVICE_UNREACHABLE",
    }:
        return 3
    return 2  # initial attempt + one retry for a repeat-safe runtime failure


def _retry_delay(attempt_count: int) -> int:
    return (15, 30, 60)[min(max(attempt_count - 1, 0), 2)]


def process_claimed_job(
    db: Any,
    job: dict[str, Any],
    *,
    worker_id: str,
    prop_score_fn: Callable[..., dict[str, Any]],
    team_score_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate one leased row; failures never affect sibling jobs."""
    payload = job.get("request_payload")
    if not isinstance(payload, dict) or not payload:
        return _rpc_row(
            db,
            "wow_block_scout_handoff_job",
            {
                "p_job_id": job["job_id"],
                "p_worker_id": worker_id,
                "p_error_code": "SCOUT_HANDOFF_PAYLOAD_EMPTY",
                "p_error_detail": {"candidate_id": job.get("candidate_id")},
            },
        )

    try:
        if job.get("target_lane") == "WOW_PROP_LANE":
            row = pick_runtime.PickRequestRow.model_validate(payload)
            batch = pick_runtime.PickRequestBatch(
                request_id=str(job["request_id"]),
                response_mode="COMPACT",
                rows=[row],
            )
            result = prop_score_fn(batch, x_wow_model_identity=None)
        elif job.get("target_lane") == "LLP_TEAM_BETTING_ENGINE":
            row = team_runtime.TeamEventRequestRow.model_validate(payload)
            batch = team_runtime.TeamEventRequestBatch(rows=[row])
            result = team_score_fn(batch, x_wow_model_identity=None)
        else:
            raise ValueError("SCOUT_HANDOFF_TARGET_LANE_INVALID")
        if not isinstance(result, dict):
            raise RuntimeError("SPECIALIST_OUTPUT_INVALID")
        outcome = _receipt_row(result)
        if outcome is None:
            raise RuntimeError("SPECIALIST_RECONCILIATION_INVALID")
        if result.get("can_execute") is not False or outcome.get("can_execute") is not False:
            raise RuntimeError("SCOUT_EXECUTION_GOVERNANCE_VIOLATION")
    except Exception as exc:
        code, http_status, detail = _error_code(exc)
        attempt = int(job.get("attempt_count") or 1)
        direct_block = http_status in {400, 409, 422} or code in {
            "SCOUT_HANDOFF_TARGET_LANE_INVALID",
            "SCOUT_EXECUTION_GOVERNANCE_VIOLATION",
            "SPECIALIST_OUTPUT_INVALID",
            "SPECIALIST_RECONCILIATION_INVALID",
        }
        budget = _retry_budget(code, http_status)
        if not direct_block and attempt < budget and _retry_safe(job):
            return _rpc_row(
                db,
                "wow_retry_scout_handoff_job",
                {
                    "p_job_id": job["job_id"],
                    "p_worker_id": worker_id,
                    "p_delay_seconds": _retry_delay(attempt),
                    "p_error_code": code,
                    "p_error_detail": {
                        **detail,
                        "http_status": http_status,
                        "retry_safe": True,
                        "retry_budget": budget,
                    },
                },
            )
        block_code = code
        if not direct_block and not _retry_safe(job):
            block_code = "SCOUT_HANDOFF_AMBIGUOUS_RETRY_PROHIBITED"
            detail = {
                **detail,
                "original_error_code": code,
                "http_status": http_status,
                "retry_safe": False,
            }
        return _rpc_row(
            db,
            "wow_block_scout_handoff_job",
            {
                "p_job_id": job["job_id"],
                "p_worker_id": worker_id,
                "p_error_code": block_code,
                "p_error_detail": detail,
            },
        )

    specialist_receipt = {
        "route": job["target_route"],
        "candidate_id": job["candidate_id"],
        "result": result,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "can_execute": False,
    }
    return _rpc_row(
        db,
        "wow_finish_scout_handoff_job",
        {
            "p_job_id": job["job_id"],
            "p_worker_id": worker_id,
            "p_specialist_receipt": specialist_receipt,
            "p_v17_qualified": _v17_qualified(outcome),
        },
    )


def read_run_summary(db: Any, source_run_id: str, *, include_receipts: bool = False) -> dict[str, Any]:
    result = (
        db.table("wow_scout_handoff_jobs")
        .select("*")
        .eq("source_run_id", source_run_id)
        .order("created_at")
        .execute()
    )
    rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    counts: dict[str, int] = {}
    lane_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        state = str(row.get("current_state") or "UNKNOWN")
        lane = str(row.get("target_lane") or "UNKNOWN")
        counts[state] = counts.get(state, 0) + 1
        lane_counts.setdefault(lane, {})
        lane_counts[lane][state] = lane_counts[lane].get(state, 0) + 1

    terminal = bool(rows) and all(bool(row.get("terminal")) for row in rows)
    evaluated = counts.get("MODEL_EVALUATED", 0) + counts.get("V17_QUALIFIED", 0)
    summary = {
        "schema_version": "wow.v17.scout-handoff-run.v1",
        "source_run_id": source_run_id,
        "research_run_id": rows[0].get("research_run_id") if rows else None,
        "status": "COMPLETE" if terminal else ("IN_PROGRESS" if rows else "NOT_FOUND"),
        "candidate_jobs": len(rows),
        "model_evaluated": evaluated,
        "v17_qualified": counts.get("V17_QUALIFIED", 0),
        "handoff_blocked": counts.get("HANDOFF_BLOCKED", 0),
        "state_counts": counts,
        "lane_state_counts": lane_counts,
        "reconciliation_pass": terminal and sum(counts.values()) == len(rows),
        "untracked_rows": 0 if rows else None,
        "can_execute": False,
    }
    if include_receipts:
        summary["jobs"] = rows
    return summary


async def worker_loop(
    *,
    db_client_fn: Callable[[], Any],
    prop_score_fn: Callable[..., dict[str, Any]],
    team_score_fn: Callable[..., dict[str, Any]],
    worker_id: str,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            db = await asyncio.to_thread(db_client_fn)
            job = await asyncio.to_thread(_claim, db, worker_id)
            if job is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
                continue
            await asyncio.to_thread(
                process_claimed_job,
                db,
                job,
                worker_id=worker_id,
                prop_score_fn=prop_score_fn,
                team_score_fn=team_score_fn,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The lease makes worker/process loss recoverable without allowing a
            # second worker to own the row concurrently.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass


__all__ = [
    "CAN_EXECUTE",
    "ScoutCandidateEnvelope",
    "ScoutHandoffPlan",
    "WORKER_VERSION",
    "build_handoff_plan",
    "enqueue_plan",
    "process_claimed_job",
    "read_run_summary",
    "worker_loop",
]
