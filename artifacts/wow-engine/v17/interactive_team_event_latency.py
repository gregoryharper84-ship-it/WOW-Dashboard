"""Bound interactive V17 team/event research latency without changing scoring.

The governed team/event scorer requires Scout -> lane routing -> five independent
Research workers -> reconciler before the sport-specific fitted specialist runs.
Scout and lane routing are order-dependent and remain serial. The five Research
workers are independent evidence gatherers, so this module runs only that group
concurrently, then reassembles their reports in the canonical RESEARCH_WORKERS
order before invoking the unchanged reconciler.

This is a latency/orchestration optimization only. It does not create model
capability, change evidence semantics, change fitted model math/calibration,
change terminal reduction, or authorize execution. Any worker/reconciler failure
preserves the same SCOUT_RESEARCH_BARRIER_BLOCKED contract as the canonical
runtime. ``can_execute`` remains false.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
from time import perf_counter
from typing import Any

import v17.team_event_request_runtime as team_runtime

LOGGER = logging.getLogger("wow.v17.interactive_team_event_latency")
_STATE_KEY = "wow_interactive_team_event_research_installed"
DEFAULT_RESEARCH_WORKERS = 5
MAX_RESEARCH_WORKERS = 5


def _worker_count() -> int:
    try:
        value = int(
            os.getenv(
                "WOW_INTERACTIVE_TEAM_EVENT_RESEARCH_WORKERS",
                str(DEFAULT_RESEARCH_WORKERS),
            )
        )
    except (TypeError, ValueError):
        value = DEFAULT_RESEARCH_WORKERS
    return max(1, min(MAX_RESEARCH_WORKERS, value))


def install_interactive_team_event_latency() -> bool:
    """Parallelize only independent team/event Research workers.

    The patch is process-local and idempotent. Callers continue to invoke the
    canonical ``_run_mandatory_scout_research`` symbol, including MLB, NFL and
    registered multisport bridges, so there is still exactly one research
    barrier implementation/authority at runtime.
    """
    if getattr(team_runtime, _STATE_KEY, False):
        return True

    canonical_workers = tuple(team_runtime.RESEARCH_WORKERS)

    def _parallel_barrier(req: Any) -> dict[str, Any]:
        run_id = f"v17-sync-{req.research_run_id}"
        candidate_id = req.event_key
        candidate: dict[str, Any] = {
            "sport": req.sport.strip().upper(),
            "league": req.league,
            "official_event_id": req.official_event_id,
            "market_family": req.market_family,
            "event_start_utc": req.event_start_time_utc,
            "evidence": dict(req.sport_specific_evidence or {}),
        }
        stages_by_worker: dict[str, dict[str, Any]] = {}

        def _run(worker_id: str, payload: dict[str, Any]):
            env = team_runtime._scout_research_envelope(
                run_id, candidate_id, worker_id, payload
            )
            out = team_runtime.execute_envelope(env)
            stages_by_worker[worker_id] = {
                "worker_id": worker_id,
                "status": out.status,
                "blockers": list(out.blockers),
            }
            return out

        scout_worker = "wow.global-scout-coordinator"
        scout_out = _run(
            scout_worker,
            {"candidate": candidate, "scout_mode": "FOCUSED"},
        )
        if scout_out.status != "SUCCEEDED":
            raise team_runtime._scout_research_barrier_blocked(
                req, scout_worker, scout_out.blockers
            )

        lane = team_runtime.scout_lane(candidate)
        lane_worker = (
            "wow.prop-scout-router" if lane == "PROP" else "wow.ml-event-scout-router"
        )
        lane_out = _run(lane_worker, {"candidate": candidate})
        if lane_out.status != "SUCCEEDED":
            raise team_runtime._scout_research_barrier_blocked(
                req, lane_worker, lane_out.blockers
            )

        research_outputs: dict[str, Any] = {}
        workers = min(_worker_count(), len(canonical_workers))
        research_started = perf_counter()
        if workers <= 1:
            for worker_id in canonical_workers:
                research_outputs[worker_id] = _run(
                    worker_id,
                    {"candidate": candidate, "evidence": candidate.get("evidence")},
                )
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="wow-team-event-research",
            ) as pool:
                future_to_worker = {
                    pool.submit(
                        _run,
                        worker_id,
                        {
                            "candidate": candidate,
                            "evidence": candidate.get("evidence"),
                        },
                    ): worker_id
                    for worker_id in canonical_workers
                }
                for future in as_completed(future_to_worker):
                    worker_id = future_to_worker[future]
                    # Preserve canonical failure behavior: an unexpected worker
                    # exception is not converted into a model-capability status.
                    research_outputs[worker_id] = future.result()

        reports: list[dict[str, Any]] = []
        team_jobs_ok = True
        for worker_id in canonical_workers:
            out = research_outputs[worker_id]
            team_jobs_ok = team_jobs_ok and out.status == "SUCCEEDED"
            reports.append(
                out.output
                if out.status == "SUCCEEDED"
                else {
                    "research_status": "DATA_UNOBTAINABLE",
                    "worker_id": worker_id,
                }
            )

        reconciler_out = _run(
            team_runtime.RESEARCH_RECONCILER,
            {
                "research_reports": reports,
                "team_jobs_ok": team_jobs_ok,
                "evidence_present": isinstance(candidate.get("evidence"), dict),
                "event_start_present": bool(candidate.get("event_start_utc")),
            },
        )
        ordered_stage_ids = [
            scout_worker,
            lane_worker,
            *canonical_workers,
            team_runtime.RESEARCH_RECONCILER,
        ]
        stages = [
            stages_by_worker[worker_id]
            for worker_id in ordered_stage_ids
            if worker_id in stages_by_worker
        ]
        LOGGER.warning(
            "WOW_V17_INTERACTIVE_STAGE route=/score-team-event "
            "stage=research-barrier event_key=%s research_workers=%s "
            "stage_ms=%.3f can_execute=false",
            req.event_key,
            workers,
            (perf_counter() - research_started) * 1000.0,
        )
        if reconciler_out.status != "SUCCEEDED":
            raise team_runtime._scout_research_barrier_blocked(
                req, team_runtime.RESEARCH_RECONCILER, reconciler_out.blockers
            )
        return {"status": "SUCCEEDED", "stages": stages}

    team_runtime._run_mandatory_scout_research = _parallel_barrier
    setattr(team_runtime, _STATE_KEY, True)
    return True


__all__ = ["install_interactive_team_event_latency"]
