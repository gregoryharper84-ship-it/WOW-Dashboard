"""Per-worker execution handlers and the Celery task that runs them (packet
sections 6-16). Ported from PR #33 (feature/wow-agent-runtime-v1) during the
convergence pass, adapted to this module's reducer signature (which takes
the controlling job's status explicitly rather than deriving it by scanning
required_jobs for a matching worker_id) and registry (agent_runtime.registry
instead of a private one).
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.schemas import WorkerJobEnvelope as WorkerEnvelope, WorkerOutputEnvelope as WorkerOutput
from agent_runtime.idempotency import compute_request_hash as canonical_hash
from agent_runtime.registry import worker_spec
from agent_runtime.queue import celery_app
from agent_runtime.discovery import merge_discovery, canonical_candidate_key
from agent_runtime.evidence import seal_evidence
from agent_runtime.gates import mix_failure_regimes, validate_calibrated, final_refresh
from agent_runtime.reducer import RequiredJobResult, reduce_candidate
from agent_runtime.model_bridge import score_mlb_event_bridge, HELD_CODE, PUBLISHED_CODE

TRANSIENT_CODES = {"TRANSPORT_429", "TRANSPORT_5XX", "DATABASE_TEMPORARY", "QUEUE_TEMPORARY"}


def _terminal_output(env: WorkerEnvelope, status: str, ceiling: str, blockers: list[str], output: dict) -> WorkerOutput:
    now = datetime.now(timezone.utc)
    payload = {"status": status, "ceiling": ceiling, "blockers": blockers, "output": output}
    return WorkerOutput(
        run_id=env.run_id, job_id=env.job_id, candidate_id=env.candidate_id, worker_id=env.worker_id,
        worker_version=env.worker_version, status=status, ceiling=ceiling, blockers=blockers,
        evidence_snapshot_id=env.evidence_snapshot_id, output=output, output_hash=canonical_hash(payload),
        started_at=now.isoformat(), completed_at=now.isoformat(), can_execute=False,
    )


def _blocked(env: WorkerEnvelope, *blockers: str, output: dict | None = None) -> WorkerOutput:
    return _terminal_output(env, "BLOCKED", "RESEARCH_INTEREST", list(blockers), output or {})


def _succeeded(env: WorkerEnvelope, ceiling: str, output: dict, blockers: list[str] | None = None) -> WorkerOutput:
    return _terminal_output(env, "SUCCEEDED", ceiling, blockers or [], output)


def _aware(value) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return dt


def _run_discovery(env: WorkerEnvelope) -> WorkerOutput:
    rows = env.payload.get("rows")
    if not isinstance(rows, list):
        return _blocked(env, "DISCOVERY_INPUT_MISSING")
    if not rows and env.payload.get("discovery_enabled") is True:
        return _blocked(env, "DISCOVERY_PROVIDER_UNAVAILABLE", output={"candidate_count": 0})
    merged = merge_discovery(rows)
    return _succeeded(env, "RESEARCH_INTEREST", {"candidates": merged, "candidate_count": len(merged), "source_family_dedupe": True})


def _run_identity(env: WorkerEnvelope) -> WorkerOutput:
    row = env.payload.get("candidate")
    if not isinstance(row, dict):
        return _blocked(env, "CANDIDATE_IDENTITY_MISSING")
    required = ["sport", "official_event_id", "participant", "market_family", "period"]
    if str(row.get("market_family") or "").upper() == "PLAYER_PROP":
        required += ["stat_family", "exact_line", "side"]
    missing = [key for key in required if row.get(key) is None or str(row.get(key)).strip() == ""]
    if missing:
        return _blocked(env, "SLATE_IDENTITY_INCOMPLETE", output={"missing_fields": missing})
    return _succeeded(env, "IDENTITY_VERIFIED", {"canonical_key": canonical_candidate_key(row), "candidate": row})


def _run_evidence(env: WorkerEnvelope) -> WorkerOutput:
    payload = env.payload.get("evidence")
    if not isinstance(payload, dict):
        return _blocked(env, "EVIDENCE_SNAPSHOT_MISSING")
    # No separate evidence_snapshots table — evidence lives in
    # wow_prop_evidence_snapshots/wow_event_evidence (existing, per Phase 0's
    # overlap audit) once a real lane wires acquisition; until then the
    # coordinator folds the sealed result into candidate_payload.
    sealed = seal_evidence(str(env.evidence_snapshot_id or ""), payload)
    blockers = []
    if sealed.missing_required_fields:
        blockers.append("EVIDENCE_REQUIRED_FIELDS_MISSING")
    if sealed.source_conflicts:
        blockers.append("EVIDENCE_SOURCE_CONFLICT")
    output = {
        "payload_hash": sealed.payload_hash,
        "missing_required_fields": list(sealed.missing_required_fields),
        "source_conflicts": list(sealed.source_conflicts),
        "sealed_evidence": payload,
    }
    if blockers:
        return _blocked(env, *blockers, output=output)
    return _succeeded(env, "EVIDENCE_VERIFIED", output)


def _run_controlling_model(env: WorkerEnvelope) -> WorkerOutput:
    sport = str(env.payload.get("sport") or "").upper()
    market_family = str(env.payload.get("market_family") or "").upper()
    period = str(env.payload.get("period") or "").upper()

    # MLB event scoring is already governed by the server-owned G11 bridge.
    # The bridge is allowed to prove a fitted-model path in HELD mode even
    # while the separate publication capability remains unavailable. It will
    # not leak a numeric probability until calibration health + ratification
    # say publish.
    if sport == "MLB" and market_family == "OUTRIGHT_WINNER" and period in {"FULL_GAME", "FULL_GAME_INCLUDING_EXTRA_INNINGS"}:
        try:
            bridged = score_mlb_event_bridge(env.payload)
        except Exception as exc:
            code = getattr(exc, "code", None) or "EVENT_MODEL_BRIDGE_UNAVAILABLE"
            return _blocked(env, str(code), output={"probability_publishable": False, "error": type(exc).__name__})
        code = bridged.get("code")
        if code == HELD_CODE:
            return _succeeded(env, "MODEL_QUALIFIED_HOLD", bridged, ["PROBABILITY_PUBLICATION_HELD"])
        if code == PUBLISHED_CODE and bridged.get("probability_publishable") is True:
            return _succeeded(env, "MODEL_QUALIFIED_HOLD", bridged)
        blockers = list(bridged.get("bridge_blockers") or ["MODEL_UNAVAILABLE"])
        return _blocked(env, *blockers, output=bridged)

    cap = env.payload.get("capability")
    if not isinstance(cap, dict) or cap.get("status") != "AVAILABLE":
        return _blocked(env, "MODEL_UNAVAILABLE", output={"probability_publishable": False})
    if not cap.get("artifact_id") or not cap.get("calibrator_id"):
        # A capability is registered/available, but the specific inputs required
        # to invoke it (artifact_id/calibrator_id) are missing — distinct from a
        # genuinely unavailable capability, and from an invocation exception.
        return _blocked(env, "MODEL_INPUTS_INSUFFICIENT", output={"probability_publishable": False})
    # No qualitative, market, L5/L10, or caller-provided probability fallback.
    return _blocked(env, "CONTROLLING_MODEL_PROVIDER_NOT_WIRED", output={
        "probability_publishable": False, "artifact_id": cap.get("artifact_id"), "calibrator_id": cap.get("calibrator_id"),
    })


def _run_failure_paths(env: WorkerEnvelope) -> WorkerOutput:
    raw = env.payload.get("components")
    if not isinstance(raw, list) or not raw:
        return _blocked(env, "FAILURE_PATH_COMPONENTS_MISSING")
    try:
        components = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("FAILURE_PATH_COMPONENT_INVALID")
            components.append((float(item["weight"]), {int(k): float(v) for k, v in dict(item["pmf"]).items()}))
        pmf = mix_failure_regimes(components)
    except Exception as exc:
        return _blocked(env, (getattr(exc, "args", ["FAILURE_PATH_INVALID"]) or ["FAILURE_PATH_INVALID"])[0])
    return _succeeded(env, "MODEL_QUALIFIED_HOLD", {"unconditional_pmf": pmf, "failure_path_applied": True})


def _run_calibration(env: WorkerEnvelope) -> WorkerOutput:
    try:
        calibrator_id = env.payload.get("calibrator_id")
        point = env.payload.get("point")
        lower = env.payload.get("lower")
        upper = env.payload.get("upper")
        validate_calibrated(point, lower, upper, calibrator_id)
    except Exception as exc:
        return _blocked(env, (getattr(exc, "args", ["CALIBRATION_INVALID"]) or ["CALIBRATION_INVALID"])[0])
    return _succeeded(env, "MODEL_QUALIFIED_HOLD", {
        "calibrator_id": calibrator_id, "calibrated_probability": float(point),
        "calibrated_probability_lower_bound": float(lower), "calibrated_probability_upper_bound": float(upper),
        "probability_publishable": True,
    })


def _run_market(env: WorkerEnvelope) -> WorkerOutput:
    checks = {
        "MARKET_IDENTITY_MISMATCH": env.payload.get("exact_identity_match") is True,
        "SETTLEMENT_IDENTITY_MISMATCH": env.payload.get("settlement_match") is True,
        "TWO_WAY_NO_VIG_UNRESOLVED": env.payload.get("two_way_no_vig_resolved") is True,
        "MARKET_PRICE_STALE": env.payload.get("price_fresh") is True,
    }
    blockers = [code for code, ok in checks.items() if not ok]
    if blockers:
        return _blocked(env, *blockers, output={"market_gate": "HOLD", "blocks_model_probability": False})
    return _succeeded(env, "MARKET_VERIFIED_HOLD", {"market_gate": "PASS", "blocks_model_probability": False})


def _run_structure(env: WorkerEnvelope) -> WorkerOutput:
    checks = {
        "DEPENDENCY_CHECK_INCOMPLETE": env.payload.get("dependency_checked") is True,
        "CORRELATION_CHECK_INCOMPLETE": env.payload.get("correlation_checked") is True,
        "DIRECTIONAL_EXPOSURE_CHECK_INCOMPLETE": env.payload.get("directional_exposure_checked") is True,
        "DUPLICATE_THESIS_CHECK_INCOMPLETE": env.payload.get("duplicate_thesis_checked") is True,
        "PORTFOLIO_CHECK_INCOMPLETE": env.payload.get("portfolio_checked") is True,
    }
    blockers = [code for code, ok in checks.items() if not ok]
    if blockers:
        return _blocked(env, *blockers)
    return _succeeded(env, "STRUCTURE_VERIFIED_HOLD", {"structure_gate": "PASS", "capital_allocation": False})


def _run_final_refresh(env: WorkerEnvelope) -> WorkerOutput:
    try:
        status, blockers = final_refresh(
            now=_aware(env.payload.get("now") or datetime.now(timezone.utc)),
            event_start=_aware(env.payload["event_start"]),
            event_status=str(env.payload.get("event_status") or "UNKNOWN"),
            market_fresh=env.payload.get("market_fresh") is True,
            critical_status_fresh=env.payload.get("critical_status_fresh") is True,
        )
    except Exception as exc:
        return _blocked(env, "FINAL_REFRESH_INPUT_INVALID", output={"error": type(exc).__name__})
    if status != "PASS":
        return _terminal_output(env, "REJECTED", "RESEARCH_INTEREST", blockers, {"final_refresh": "REJECT"})
    return _succeeded(env, "FINAL_REFRESH_HOLD", {"final_refresh": "PASS"})


def _run_reducer(env: WorkerEnvelope) -> WorkerOutput:
    jobs = env.payload.get("required_jobs")
    if not isinstance(jobs, list):
        return _blocked(env, "REDUCER_INPUT_MISSING")
    controlling_worker_id = env.payload.get("controlling_worker_id")
    controlling_job_status = None
    required_job_results = []
    for job in jobs:
        if not isinstance(job, dict):
            return _blocked(env, "REDUCER_INPUT_MISSING")
        required_job_results.append(RequiredJobResult(
            worker_id=str(job.get("worker_id")), status=str(job.get("status")),
            ceiling=job.get("ceiling"), blockers=tuple(job.get("blockers") or ()),
        ))
        if job.get("worker_id") == controlling_worker_id:
            controlling_job_status = job.get("status")
    try:
        decision = reduce_candidate(
            controlling_worker_id=controlling_worker_id,
            controlling_job_status=controlling_job_status,
            required_jobs=required_job_results,
        )
    except Exception:
        return _blocked(env, "REDUCER_FAILED")
    return _succeeded(
        env, decision.ceiling,
        {
            "terminal_label": decision.label, "final_terminal_ceiling": decision.ceiling,
            "probability_publishable": decision.probability_publishable, "blockers": list(decision.blockers),
        },
        list(decision.blockers),
    )


_HANDLERS = {
    "wow.parallel-discovery-router": _run_discovery,
    "wow.slate-integrity-expert": _run_identity,
    "wow.evidence-hydration": _run_evidence,
    "wow.controlling-model": _run_controlling_model,
    "wow.failure-path-framework": _run_failure_paths,
    "wow.dynamic-calibration-expert": _run_calibration,
    "wow.exact-line-market-auditor": _run_market,
    "wow.structure-exposure-governor": _run_structure,
    "wow.final-refresh-governor": _run_final_refresh,
    "wow.terminal-ceiling-reducer": _run_reducer,
}


def execute_envelope(env: WorkerEnvelope) -> WorkerOutput:
    spec = worker_spec(env.worker_id)
    if env.worker_version != spec.worker_version:
        return _blocked(env, "WORKER_VERSION_MISMATCH")
    if env.payload.get("_test_handler") == "SUCCEED":
        return _succeeded(env, spec.authority_ceiling, {"ok": True})
    handler = _HANDLERS.get(env.worker_id)
    if handler is None:
        return _blocked(env, "WORKER_HANDLER_NOT_WIRED")
    return handler(env)


@celery_app.task(bind=True, name="wow.agent_runtime.execute", acks_late=True)
def execute_job(self, envelope: dict):
    try:
        env = WorkerEnvelope.model_validate(envelope)
    except Exception as exc:
        return {"status": "BLOCKED", "error_code": "WORKER_CONTRACT_INVALID", "error": type(exc).__name__, "can_execute": False}
    spec = worker_spec(env.worker_id)
    try:
        out = execute_envelope(env)
        return out.model_dump(mode="json")
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in TRANSIENT_CODES and self.request.retries < spec.max_retries:
            raise self.retry(exc=exc, countdown=min(60, 2 ** self.request.retries))
        return _terminal_output(env, "DEAD_LETTERED", "RESEARCH_INTEREST", ["WORKER_DEAD_LETTERED"], {"error": type(exc).__name__}).model_dump(mode="json")
