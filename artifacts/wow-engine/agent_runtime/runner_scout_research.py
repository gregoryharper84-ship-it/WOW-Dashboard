"""Execution handlers for the mandatory WOW v16 Scout + Research barrier.

Existing workers delegate unchanged to agent_runtime.runner. New workers are
strictly evidence/discovery workers and are checked for predictive authority
leakage before their output can be persisted.
"""
from __future__ import annotations

from typing import Any

from agent_runtime.runner import (
    TRANSIENT_CODES,
    _blocked,
    _succeeded,
    _terminal_output,
    execute_envelope as execute_base_envelope,
)
from agent_runtime.schemas import WorkerJobEnvelope as WorkerEnvelope, WorkerOutputEnvelope as WorkerOutput
from agent_runtime.registry import worker_spec
from agent_runtime.scout_research import (
    ALL_RESEARCH_WORKERS,
    RESEARCH_RECONCILER,
    RESEARCH_WORKERS,
    SCOUT_WORKERS,
    evidence_summary,
    scout_lane,
    scrub_authority,
    validate_non_predictive_output,
)

ROLE_BY_WORKER = {
    "wow.source-provenance-researcher": "SOURCE_PROVENANCE",
    "wow.participant-status-researcher": "PARTICIPANT_STATUS",
    "wow.history-comparables-researcher": "HISTORY_COMPARABLES",
    "wow.matchup-context-researcher": "MATCHUP_CONTEXT",
    "wow.market-settlement-researcher": "MARKET_SETTLEMENT",
}

NEW_WORKERS = SCOUT_WORKERS | ALL_RESEARCH_WORKERS


def _data_completeness(candidate: dict[str, Any]) -> int:
    fields = ("sport", "official_event_id", "participant", "market_family", "period")
    present = sum(1 for field in fields if candidate.get(field) not in (None, ""))
    return int(round(100 * present / len(fields)))


def _run_global_scout(env: WorkerEnvelope) -> WorkerOutput:
    candidate = env.payload.get("candidate")
    if not isinstance(candidate, dict):
        return _blocked(env, "SCOUT_CANDIDATE_MISSING")
    mode = str(env.payload.get("scout_mode") or "FOCUSED").upper()
    lane = scout_lane(candidate)
    output = {
        "scout_status": "SCOUT_CANDIDATE",
        "scout_mode": mode,
        "scout_lane": lane,
        "scout_priority_score": _data_completeness(candidate),
        "scout_priority_semantics": "DATA_COMPLETENESS_NOT_PROBABILITY",
        "candidate_identity": scrub_authority({
            key: candidate.get(key)
            for key in ("sport", "league", "official_event_id", "participant", "opponent", "market_family", "stat_family", "period", "exact_line", "side")
            if candidate.get(key) is not None
        }),
        "prediction_authority": False,
        "requires_controlling_specialist": True,
        "can_execute": False,
    }
    return _succeeded(env, "RESEARCH_INTEREST", output)


def _run_specialized_scout(env: WorkerEnvelope, expected_lane: str) -> WorkerOutput:
    candidate = env.payload.get("candidate")
    if not isinstance(candidate, dict):
        return _blocked(env, "SCOUT_CANDIDATE_MISSING")
    actual_lane = scout_lane(candidate)
    if actual_lane != expected_lane:
        return _blocked(env, "SCOUT_LANE_MISMATCH", output={"expected_lane": expected_lane, "actual_lane": actual_lane})
    output = {
        "scout_status": "SCOUT_ESCALATED",
        "scout_lane": actual_lane,
        "sport": str(candidate.get("sport") or "").upper(),
        "market_family": str(candidate.get("market_family") or "").upper(),
        "discovery_reason": candidate.get("discovery_reason") or "CALLER_OR_PROVIDER_CANDIDATE",
        "requires_research_team": True,
        "requires_controlling_specialist": True,
        "prediction_authority": False,
        "can_execute": False,
    }
    return _succeeded(env, "RESEARCH_INTEREST", output)


def _run_researcher(env: WorkerEnvelope) -> WorkerOutput:
    role = ROLE_BY_WORKER[env.worker_id]
    summary = evidence_summary(env.payload.get("evidence"), role)
    return _succeeded(env, "RESEARCH_INTEREST", summary)


def _run_reconciler(env: WorkerEnvelope) -> WorkerOutput:
    reports = env.payload.get("research_reports")
    if not isinstance(reports, list) or len(reports) < len(RESEARCH_WORKERS):
        return _blocked(env, "RESEARCH_TEAM_INCOMPLETE")
    if env.payload.get("team_jobs_ok") is not True:
        return _blocked(env, "RESEARCH_TEAM_INCOMPLETE", output={
            "research_status": "DATA_UNOBTAINABLE",
            "research_roles_completed": len(reports),
            "prediction_authority": False,
            "can_execute": False,
        })
    evidence_present = env.payload.get("evidence_present") is True
    event_start_present = env.payload.get("event_start_present") is True
    statuses = [str((report or {}).get("research_status") or "DATA_UNOBTAINABLE") for report in reports]
    if not evidence_present or not event_start_present:
        missing = []
        if not evidence_present:
            missing.append("evidence")
        if not event_start_present:
            missing.append("event_start_utc")
        return _blocked(env, "EVIDENCE_SNAPSHOT_MISSING", output={
            "research_status": "DATA_UNOBTAINABLE",
            "missing_fields": missing,
            "research_roles_completed": len(reports),
            "prediction_authority": False,
            "can_execute": False,
        })
    overall = "READY" if all(status == "READY" for status in statuses) else "PARTIAL"
    return _succeeded(env, "RESEARCH_INTEREST", {
        "research_status": overall,
        "research_roles_completed": len(reports),
        "research_role_statuses": statuses,
        "evidence_identifiability_deferred_to_existing_hydrator": True,
        "prediction_authority": False,
        "can_execute": False,
    })


def _execute_new(env: WorkerEnvelope) -> WorkerOutput:
    if env.worker_id == "wow.global-scout-coordinator":
        out = _run_global_scout(env)
    elif env.worker_id == "wow.prop-scout-router":
        out = _run_specialized_scout(env, "PROP")
    elif env.worker_id == "wow.ml-event-scout-router":
        out = _run_specialized_scout(env, "ML_EVENT")
    elif env.worker_id in ROLE_BY_WORKER:
        out = _run_researcher(env)
    elif env.worker_id == RESEARCH_RECONCILER:
        out = _run_reconciler(env)
    else:
        return _blocked(env, "WORKER_HANDLER_NOT_WIRED")

    violations = validate_non_predictive_output(out.output)
    if violations:
        return _blocked(env, "SCOUT_RESEARCH_AUTHORITY_VIOLATION", output={"violating_paths": violations})
    return out


def execute_envelope(env: WorkerEnvelope) -> WorkerOutput:
    spec = worker_spec(env.worker_id)
    if env.worker_version != spec.worker_version:
        return _blocked(env, "WORKER_VERSION_MISMATCH")
    if env.payload.get("_test_handler") == "SUCCEED":
        return _succeeded(env, spec.authority_ceiling, {"ok": True, "prediction_authority": False, "can_execute": False})
    if env.worker_id in NEW_WORKERS:
        return _execute_new(env)
    return execute_base_envelope(env)


__all__ = ["TRANSIENT_CODES", "_terminal_output", "execute_envelope"]
