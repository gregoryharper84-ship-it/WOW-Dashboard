"""
gate_engine/command_center/orchestrator.py
WOW Sports Intelligence Command Center — Phase 1

Main orchestration pipeline.

Protocol (two-phase):
  Phase A — Intake + Routing (POST /wow/cc/intake):
    1. Intake validation      → canonical envelopes
    2. Market routing         → each candidate → exactly one family
    Returns: routing manifest (no engine calls yet)

  Phase B — Full Run (POST /wow/cc/run):
    Accepts routing manifest + engine_results dict
    3. Engine result attachment → engine_label + engine_blockers stamped
    4. Kalshi Recovery Mode isolation
    5. All shared services (slate, exposure, calibration, fp, exact-line, refresh)
    6. Monotonic ceiling enforcement
    7. Row reconciliation
    8. Build unified output envelope

Alternatively, Phase B accepts raw candidates + pre-populated engine_results
so callers can skip Phase A.

INVARIANTS (enforced at every return point):
  can_execute = False              — unconditional
  dry_run_only = True              — unconditional
  KALSHI_RECOVERY_MODE = "ACTIVE"  — Kalshi isolation always active
  No downstream pass erases an upstream blocker
  Monotonic ceiling: final_label >= cc_ceiling in restrictiveness
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .cc_labels import (
    CAN_EXECUTE, KALSHI_RECOVERY_MODE, DRY_RUN_ONLY,
    FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
    CC_ENGINE_RESULT_MISSING, CC_ENGINE_LABEL_INVALID,
)
from .candidate_intake import validate_batch, make_envelope, extract_engine_label, check_engine_result_keys
from .market_router import route_batch
from .ceiling_resolver import enforce_batch_ceilings, check_no_upstream_erasure
from .shared_services import run_all as run_shared_services
from .kalshi_isolation import apply_recovery_mode_caps, check_cross_contamination
from .reconciliation import reconcile_batch, build_run_summary

_KALSHI_FAMILIES = frozenset({FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER})

# Module-level governance constants
can_execute: bool = CAN_EXECUTE


# ---------------------------------------------------------------------------
# Phase A — Intake + Routing
# ---------------------------------------------------------------------------

def run_intake(
    raw_candidates: list[dict[str, Any]],
    session_id: str = "",
    run_id:     str = "",
    target_date: str = "",
) -> dict[str, Any]:
    """
    Phase A: validate and route candidates without calling any engine.
    Returns a routing manifest the caller uses to invoke the right engines.
    """
    run_id = run_id or str(uuid.uuid4())[:16]
    ts     = datetime.now(timezone.utc).isoformat()

    # Step 1: Intake
    valid_envs, invalid_envs = validate_batch(raw_candidates)
    all_envs = valid_envs + invalid_envs

    # Step 2: Route valid candidates
    routing = route_batch(valid_envs)

    # Build routing manifest per family
    manifest: dict[str, Any] = {
        "run_id":       run_id,
        "session_id":   session_id,
        "target_date":  target_date,
        "timestamp":    ts,
        "phase":        "A_INTAKE_ROUTING",
        "intake": {
            "total_received":   len(raw_candidates),
            "valid":            len(valid_envs),
            "invalid":          len(invalid_envs),
            "invalid_ids":      [e.get("candidate_id") for e in invalid_envs],
        },
        "routing":        routing,
        "envelopes":      all_envs,   # caller can inspect + pass to engines
        "can_execute":    CAN_EXECUTE,
        "dry_run_only":   DRY_RUN_ONLY,
        "kalshi_recovery_mode": KALSHI_RECOVERY_MODE,
    }
    return manifest


# ---------------------------------------------------------------------------
# Phase B — Full Run
# ---------------------------------------------------------------------------

def run_command_center(
    raw_candidates:  list[dict[str, Any]],
    engine_results:  dict[str, Any] | None = None,
    session_id:      str = "",
    run_id:          str = "",
    target_date:     str = "",
    freshness_window_minutes: int = 30,
) -> dict[str, Any]:
    """
    Full orchestration pipeline (Phase A + B in one call).

    Parameters
    ----------
    raw_candidates      — list of raw candidate dicts
    engine_results      — optional dict keyed by candidate_id → engine result dict
                          If None, engine_result stays None on all envelopes and
                          CC:ENGINE_RESULT_MISSING is stamped.
    session_id, run_id  — identifiers for this run
    target_date         — YYYY-MM-DD slate date
    freshness_window_minutes — freshness threshold for final refresh check

    Returns
    -------
    Unified CC output envelope (see cc_output_schema below).
    """
    run_id = run_id or str(uuid.uuid4())[:16]
    ts     = datetime.now(timezone.utc).isoformat()

    # ── Step 1: Intake ─────────────────────────────────────────────────────
    valid_envs, invalid_envs = validate_batch(raw_candidates)
    all_envs: list[dict[str, Any]] = valid_envs + invalid_envs

    # ── Step 2: Route ──────────────────────────────────────────────────────
    routing_report = route_batch(valid_envs)

    # Collect ALL envelopes: routed + unrouted valid + invalid
    routed_envs: list[dict[str, Any]] = []
    for family_envs in routing_report["by_family"].values():
        routed_envs.extend(family_envs)
    unrouted_envs = routing_report["conflicts"] + routing_report["unresolvable"]

    # ── Step 3: Attach engine results ─────────────────────────────────────
    er_map = engine_results or {}
    for env in all_envs:
        cid    = env.get("candidate_id", "")
        family = env.get("assigned_family") or env.get("market_family")
        er     = er_map.get(cid)

        if er is None:
            if family:   # routed but no result
                env.setdefault("cc_blockers", []).append(
                    f"{CC_ENGINE_RESULT_MISSING}:candidate_id={cid}"
                )
            env["engine_result"] = None
            env["engine_label"]  = None
            continue

        # Validate engine result shape
        missing_keys = check_engine_result_keys(family or "", er)
        if missing_keys:
            env["cc_blockers"].append(
                f"{CC_ENGINE_LABEL_INVALID}:missing_keys={','.join(missing_keys)}"
            )

        env["engine_result"]  = er
        env["engine_blockers"] = list(er.get("blockers") or er.get("failed_modules") or [])

        # Extract primary engine label
        raw_label = extract_engine_label(family or "", er)
        env["engine_label"] = raw_label

        # Carry engine's can_execute check — must always be False
        if er.get("can_execute") is True:
            env["cc_blockers"].append(
                "CC:ENGINE_CAN_EXECUTE_VIOLATION:engine returned can_execute=True"
            )

    # ── Step 4: Kalshi isolation ───────────────────────────────────────────
    kalshi_envs = [e for e in routed_envs if
                   (e.get("assigned_family") or e.get("market_family")) in _KALSHI_FAMILIES]
    kalshi_report: dict[str, Any] = {"status": "SKIPPED", "total": 0}
    if kalshi_envs:
        kalshi_report = apply_recovery_mode_caps(kalshi_envs)

    contamination_violations = check_cross_contamination(all_envs)

    # ── Step 5: Shared services ────────────────────────────────────────────
    # Run against all envelopes (including invalids and unrouted — they still
    # need slate-integrity and row-completeness checks)
    service_report = run_shared_services(all_envs, target_date, freshness_window_minutes)

    # ── Step 6: Monotonic ceiling enforcement ─────────────────────────────
    ceiling_report = enforce_batch_ceilings(all_envs)
    erasure_violations = check_no_upstream_erasure(all_envs)

    # ── Step 7: Reconciliation ────────────────────────────────────────────
    recon_report = reconcile_batch(all_envs)

    # ── Step 8: Build output ──────────────────────────────────────────────
    summary = build_run_summary(all_envs, routing_report, service_report, recon_report)

    return {
        # Identifiers
        "run_id":       run_id,
        "session_id":   session_id,
        "target_date":  target_date,
        "timestamp":    ts,
        "phase":        "B_FULL_RUN",

        # Governance invariants — always present, always these values
        "can_execute":           CAN_EXECUTE,
        "dry_run_only":          DRY_RUN_ONLY,
        "kalshi_recovery_mode":  KALSHI_RECOVERY_MODE,

        # Candidate results
        "candidates": all_envs,

        # Per-phase reports
        "intake_report": {
            "total_received": len(raw_candidates),
            "valid":          len(valid_envs),
            "invalid":        len(invalid_envs),
            "invalid_ids":    [e.get("candidate_id") for e in invalid_envs],
        },
        "routing_report":     routing_report,
        "kalshi_report":      kalshi_report,
        "contamination_violations": contamination_violations,
        "service_report":     service_report,
        "ceiling_report":     ceiling_report,
        "erasure_violations": erasure_violations,
        "reconciliation_report": recon_report,
        "summary":            summary,
    }
