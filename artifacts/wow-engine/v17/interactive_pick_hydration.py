"""Bounded pre-hydration wrapper for interactive V17 prop batches.

The existing /score-pick-request handler remains sole owner of validation,
immutable persistence, fitted scoring, calibration, reconciliation, portfolio
governance, and terminal reduction. This wrapper moves only successful external
raw-evidence acquisition ahead of that handler and runs independent acquisitions
with a small bounded thread pool.

Rows that share one immutable evidence identity (for example MORE and LESS for
the same player/stat/event) share one external hydration result. Direction and
line are deliberately not part of the hydration identity because evidence is
upstream of settlement direction/threshold. The canonical scorer still receives
and scores every row independently.

If pre-hydration cannot prove route eligibility or any acquisition fails, the
row is passed to the captured canonical handler unchanged. Therefore failure
codes and fail-closed semantics remain owned by the canonical handler.

For the mandatory Scout -> Research barrier, only the five independent Research
workers are parallelized. Scout routing remains ordered before Research and the
existing reconciler still runs after all five workers complete. This changes no
worker roster, evidence contract, probability authority, calibration, terminal
reduction, persistence, or execution semantics; it only removes unnecessary
serial I/O from the interactive critical path.

One narrow exception is a COMPACT all-row EVENT_ALREADY_STARTED batch after the
same specialist/capability/certified-artifact preflight has already passed.
Those rows are terminalized immediately through the canonical terminal reducer
and Top-10 reconciler instead of repeating expensive downstream work that cannot
change a pregame-only event invalidation. FULL internal callers remain on the
captured canonical path unchanged. This preserves blocker precedence, row
identity, reconciliation, and can_execute=false while keeping historical
interactive reproduction traffic from exhausting the transport path.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Optional

from fastapi import Header

from mlb_1ip_specialist import CANONICAL_STAT_TYPE as MLB_1IP_STAT_TYPE
import pick_request_runtime_core as pick_runtime
from pick_request_runtime_core import PickRequestBatch, PickRequestRow, RawPropEvidence, _canonical_stat
from prop_auto_hydration_router import auto_hydrate_prop_evidence
from v17.top10_model_reconciliation import enforce_top10_completion

LOGGER = logging.getLogger("wow.v17.interactive_latency")
_STATE_KEY = "wow_interactive_pick_hydration_installed"
_RESEARCH_PATCH_KEY = "wow_interactive_parallel_research_installed"
DEFAULT_WORKERS = 4
MAX_WORKERS = 8
DEFAULT_RESEARCH_WORKERS = 5
MAX_RESEARCH_WORKERS = 5
_DEFAULT_AUTO_HYDRATE_PROP_EVIDENCE = auto_hydrate_prop_evidence


def _worker_count() -> int:
    try:
        value = int(os.getenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", str(DEFAULT_WORKERS)))
    except (TypeError, ValueError):
        value = DEFAULT_WORKERS
    return max(1, min(MAX_WORKERS, value))


def _research_worker_count() -> int:
    try:
        value = int(os.getenv("WOW_INTERACTIVE_PROP_RESEARCH_WORKERS", str(DEFAULT_RESEARCH_WORKERS)))
    except (TypeError, ValueError):
        value = DEFAULT_RESEARCH_WORKERS
    return max(1, min(MAX_RESEARCH_WORKERS, value))


def _install_parallel_research_barrier() -> bool:
    """Parallelize only independent Research workers inside the canonical barrier.

    Scout coordinator and lane router remain strictly ordered. The same worker
    handlers and envelopes are used. Research reports are reassembled in the
    canonical RESEARCH_WORKERS order before the unchanged reconciler executes,
    so ordering, blocker semantics, and audit shape stay deterministic.
    """
    if getattr(pick_runtime, _RESEARCH_PATCH_KEY, False):
        return True

    def _parallel_barrier(*, row_key: str, run_id: str, candidate: dict[str, Any]):
        stages_by_worker: dict[str, dict[str, Any]] = {}

        def _run(worker_id: str, payload: dict[str, Any]):
            env = pick_runtime._scout_research_envelope(run_id, row_key, worker_id, payload)
            out = pick_runtime.execute_envelope(env)
            stages_by_worker[worker_id] = {
                "worker_id": worker_id,
                "status": out.status,
                "blockers": list(out.blockers),
            }
            return out

        scout_worker = "wow.global-scout-coordinator"
        scout_out = _run(scout_worker, {"candidate": candidate, "scout_mode": "FOCUSED"})
        if scout_out.status != "SUCCEEDED":
            return False, {
                "stage": scout_worker,
                "blockers": scout_out.blockers,
                "stages": [stages_by_worker[scout_worker]],
            }

        lane = pick_runtime.scout_lane(candidate)
        lane_worker = "wow.prop-scout-router" if lane == "PROP" else "wow.ml-event-scout-router"
        lane_out = _run(lane_worker, {"candidate": candidate})
        if lane_out.status != "SUCCEEDED":
            return False, {
                "stage": lane_worker,
                "blockers": lane_out.blockers,
                "stages": [stages_by_worker[scout_worker], stages_by_worker[lane_worker]],
            }

        research_outputs: dict[str, Any] = {}
        worker_n = min(_research_worker_count(), len(pick_runtime.RESEARCH_WORKERS))
        started = perf_counter()
        if worker_n <= 1:
            for worker_id in pick_runtime.RESEARCH_WORKERS:
                research_outputs[worker_id] = _run(
                    worker_id,
                    {"candidate": candidate, "evidence": candidate.get("evidence")},
                )
        else:
            with ThreadPoolExecutor(max_workers=worker_n, thread_name_prefix="wow-prop-research") as pool:
                future_to_worker = {
                    pool.submit(
                        _run,
                        worker_id,
                        {"candidate": candidate, "evidence": candidate.get("evidence")},
                    ): worker_id
                    for worker_id in pick_runtime.RESEARCH_WORKERS
                }
                for future in as_completed(future_to_worker):
                    worker_id = future_to_worker[future]
                    research_outputs[worker_id] = future.result()

        reports: list[dict[str, Any]] = []
        team_jobs_ok = True
        for worker_id in pick_runtime.RESEARCH_WORKERS:
            out = research_outputs[worker_id]
            team_jobs_ok = team_jobs_ok and out.status == "SUCCEEDED"
            reports.append(
                out.output
                if out.status == "SUCCEEDED"
                else {"research_status": "DATA_UNOBTAINABLE", "worker_id": worker_id}
            )

        reconciler_out = _run(
            pick_runtime.RESEARCH_RECONCILER,
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
            *pick_runtime.RESEARCH_WORKERS,
            pick_runtime.RESEARCH_RECONCILER,
        ]
        stages = [stages_by_worker[worker_id] for worker_id in ordered_stage_ids if worker_id in stages_by_worker]
        LOGGER.warning(
            "WOW_V17_INTERACTIVE_STAGE route=/score-pick-request stage=research-barrier row_key=%s research_workers=%s stage_ms=%.3f can_execute=false",
            row_key,
            worker_n,
            (perf_counter() - started) * 1000.0,
        )
        if reconciler_out.status != "SUCCEEDED":
            return False, {
                "stage": pick_runtime.RESEARCH_RECONCILER,
                "blockers": reconciler_out.blockers,
                "stages": stages,
            }
        return True, {"stages": stages}

    pick_runtime._run_mandatory_scout_research = _parallel_barrier
    setattr(pick_runtime, _RESEARCH_PATCH_KEY, True)
    return True


def _route_is_prehydration_eligible(row: PickRequestRow, market_api: Any) -> bool:
    """Mirror canonical route preflight without creating new capability."""
    if row.evidence is not None:
        return False
    sport = str(row.sport or "").strip().upper()
    canonical_stat = _canonical_stat(sport, row.stat_type)
    if canonical_stat == MLB_1IP_STAT_TYPE:
        return False
    try:
        specialist = market_api.prod.base_api._controlling_specialist_provider(sport, canonical_stat)
        if not isinstance(specialist, dict) or specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
            return False
        lane = market_api.prod._runtime_capability(market_api.prod.PROP_CAPABILITY_KEY)
        if not isinstance(lane, dict) or lane.get("capability_status") != "AVAILABLE":
            return False
        route = market_api._prop_route_artifact(sport, canonical_stat)
        return bool(isinstance(route, dict) and route.get("ok") is True and route.get("code") == "PROP_CERTIFIED_MODEL_ARTIFACT_READY")
    except Exception:
        return False


def _event_started(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.utcoffset() is None:
        return False
    return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def _all_rows_started_and_preflight_ready(batch: PickRequestBatch, *, market_api: Any) -> bool:
    specialist_cache: dict[tuple[str, str], Any] = {}
    route_cache: dict[tuple[str, str], Any] = {}
    lane: Any = None
    lane_loaded = False
    for row in batch.rows:
        if not _event_started(row.event_start_time):
            return False
        sport = str(row.sport or "").strip().upper()
        canonical_stat = _canonical_stat(sport, row.stat_type)
        key = (sport, canonical_stat)
        try:
            if key not in specialist_cache:
                specialist_cache[key] = market_api.prod.base_api._controlling_specialist_provider(sport, canonical_stat)
            specialist = specialist_cache[key]
            if not isinstance(specialist, dict) or specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
                return False
            if not lane_loaded:
                lane = market_api.prod._runtime_capability(market_api.prod.PROP_CAPABILITY_KEY)
                lane_loaded = True
            if not isinstance(lane, dict) or lane.get("capability_status") != "AVAILABLE":
                return False
            if key not in route_cache:
                route_cache[key] = market_api._prop_route_artifact(sport, canonical_stat)
            route = route_cache[key]
            if not isinstance(route, dict) or route.get("ok") is not True or route.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
                return False
        except Exception:
            return False
    return True


def _event_started_response(batch: PickRequestBatch) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    for index, row in enumerate(batch.rows):
        row_key = row.row_key or f"row-{index + 1}"
        outcomes.append(pick_runtime._terminal(row_key, "REJECTED", "EVENT_ALREADY_STARTED", detail={"terminal_label":"NO_PLAY","specialist_invoked":False,"fast_path":"PREGAME_EVENT_INVALIDATED"}, acquisition={"mode":"AUTO_HYDRATION","status":"FAILED","provider":"PREGAME_EVENT_GUARD","source_type":row.source_type,"platform":row.platform,"can_execute":False}))
    rows_in = len(batch.rows)
    response = {"ok":False,"request_id":batch.request_id,"run_controller_status":"BLOCKED","rows_in":rows_in,"rows_completed":0,"rows_held":0,"rows_rejected":rows_in,"pick_rejected_count":sum(1 for row in outcomes if row.get("pick_rejected") is True),"infrastructure_blocked_count":sum(1 for row in outcomes if row.get("infrastructure_blocked") is True),"reconciliation_pass":True,"telemetry":pick_runtime._telemetry(outcomes),"specialist_utilization_summary":pick_runtime._specialist_utilization_summary(outcomes),"response_mode":batch.response_mode,"rows":[pick_runtime._compact_pick_outcome(outcome) for outcome in outcomes] if batch.response_mode == "COMPACT" else outcomes,"detail_retrieval":{"mode":"IMMUTABLE_RECEIPT_LOOKUP","operation_id":"lookupWowV17PredictionReceipts"} if batch.response_mode == "COMPACT" else None,"probability_objective":"GOVERNED_MODEL_ONLY","can_execute":False}
    return enforce_top10_completion(response, list(batch.rows))


def _hydration_key(row: PickRequestRow) -> tuple[str, ...]:
    return (str(row.sport or "").strip().upper(),_canonical_stat(row.sport,row.stat_type)," ".join(str(row.player or "").strip().split()),str(row.event_id or "").strip(),str(row.event_start_time or "").strip(),str(row.source_capture_timestamp or "").strip(),str(row.source_type or "").strip().upper(),str(row.platform or "").strip().upper(),str(row.opponent or "").strip().upper())


def _hydrate(row: PickRequestRow) -> RawPropEvidence:
    hydrator = auto_hydrate_prop_evidence
    if hydrator is _DEFAULT_AUTO_HYDRATE_PROP_EVIDENCE:
        runtime_hydrator = getattr(pick_runtime, "auto_hydrate_prop_evidence", None)
        if callable(runtime_hydrator):
            hydrator = runtime_hydrator
    raw = hydrator(
        sport=str(row.sport or "").strip().upper(),
        player=row.player,
        stat_type=_canonical_stat(row.sport,row.stat_type),
        event_start_time=row.event_start_time,
        source_capture_timestamp=row.source_capture_timestamp,
        source_label=f"{row.source_type}:{row.platform or 'UNKNOWN'}",
        opponent=row.opponent,
        canonical_event_id=row.event_id,
    )
    return RawPropEvidence.model_validate(raw)


def prehydrate_batch(batch: PickRequestBatch, *, market_api: Any) -> PickRequestBatch:
    workers=_worker_count(); eligible=[(index,row) for index,row in enumerate(batch.rows) if _route_is_prehydration_eligible(row,market_api)]
    if workers<=1 or not eligible: return batch
    groups={}
    for index,row in eligible:
        key=_hydration_key(row)
        if key not in groups: groups[key]=(row,[])
        groups[key][1].append(index)
    started=perf_counter(); evidence_by_index={}; failure_codes=set(); successful_fetches=0; pool_workers=min(workers,len(groups))
    with ThreadPoolExecutor(max_workers=pool_workers,thread_name_prefix="wow-prop-hydrate") as pool:
        future_to_key={pool.submit(_hydrate,representative):key for key,(representative,_indices) in groups.items()}
        for future in as_completed(future_to_key):
            key=future_to_key[future]; _representative,indices=groups[key]
            try: evidence=future.result()
            except Exception as exc:
                failure_codes.add(str(getattr(exc,"code",None) or type(exc).__name__)); continue
            successful_fetches+=1
            for index in indices: evidence_by_index[index]=evidence
    hydrated=batch.model_copy(update={"rows":[row.model_copy(update={"evidence":evidence_by_index[index]}) if index in evidence_by_index else row for index,row in enumerate(batch.rows)]}) if evidence_by_index else batch
    LOGGER.warning("WOW_V17_INTERACTIVE_STAGE route=/score-pick-request stage=prehydrate rows_in=%s eligible=%s unique_fetches=%s successful_fetches=%s prefetched=%s reused=%s failed_fetches=%s failure_codes=%s workers=%s stage_ms=%.3f can_execute=false",len(batch.rows),len(eligible),len(groups),successful_fetches,len(evidence_by_index),max(0,len(evidence_by_index)-successful_fetches),len(groups)-successful_fetches,",".join(sorted(failure_codes)) if failure_codes else "NONE",pool_workers,(perf_counter()-started)*1000.0)
    return hydrated


def install_interactive_pick_hydration_wrapper(app: Any, *, market_api: Any) -> bool:
    if getattr(app.state,_STATE_KEY,False): return True
    _install_parallel_research_barrier()
    captured_route=next((route for route in app.router.routes if getattr(route,"path",None)=="/score-pick-request" and "POST" in (getattr(route,"methods",set()) or set())),None)
    if captured_route is None or not callable(getattr(captured_route,"endpoint",None)): return False
    captured_endpoint=captured_route.endpoint; dependencies=list(getattr(captured_route,"dependencies",None) or []); operation_id=str(getattr(captured_route,"operation_id",None) or "scoreWowPickRequest")
    app.router.routes[:]=[route for route in app.router.routes if route is not captured_route]
    def score_pick_request_prehydrated(batch: PickRequestBatch,x_wow_model_identity: Optional[str]=Header(default=None,alias="X-WOW-Model-Identity")):
        if batch.response_mode=="COMPACT" and _all_rows_started_and_preflight_ready(batch,market_api=market_api):
            LOGGER.warning("WOW_V17_INTERACTIVE_STAGE route=/score-pick-request stage=event-invalidated-fast-path rows_in=%s response_mode=%s can_execute=false",len(batch.rows),batch.response_mode)
            return _event_started_response(batch)
        prepared=prehydrate_batch(batch,market_api=market_api); return captured_endpoint(prepared,x_wow_model_identity)
    app.post("/score-pick-request",dependencies=dependencies,operation_id=operation_id)(score_pick_request_prehydrated); setattr(app.state,_STATE_KEY,True); return True


def schedule_interactive_pick_hydration_install(app: Any, *, market_api: Any) -> None:
    if getattr(app.state,f"{_STATE_KEY}_scheduled",False): return
    @app.on_event("startup")
    async def _install_interactive_pick_hydration() -> None:
        installed=install_interactive_pick_hydration_wrapper(app,market_api=market_api); LOGGER.warning("WOW_V17_INTERACTIVE_HYDRATION status=%s workers=%s research_workers=%s can_execute=false","INSTALLED" if installed else "NOT_INSTALLED_ROUTE_UNAVAILABLE",_worker_count(),_research_worker_count())
    setattr(app.state,f"{_STATE_KEY}_scheduled",True)

__all__=["DEFAULT_WORKERS","MAX_WORKERS","DEFAULT_RESEARCH_WORKERS","MAX_RESEARCH_WORKERS","install_interactive_pick_hydration_wrapper","prehydrate_batch","schedule_interactive_pick_hydration_install"]
