#!/usr/bin/env python3
"""
scripts/run_b3c_canary.py
B3C Bounded Real-Claude Canary — Live Execution Runner

WHAT THIS IS
  Standalone script that executes the B3C bounded real-Claude canary against
  exactly ONE frozen MLB Moneyline evidence snapshot.

  Authorized path (no shortcuts):
    frozen snapshot
    → B3A MlbMoneylineAdapter (real, not mocked)
    → ClaudeRoleRunner (real Anthropic API, budget-guarded)
    → Real B0 _scan_forbidden_keys at every response
    → Real B2 run_orchestrator (B1 validators + ContradictionDetector + BundleAssembler)
    → b3c_canary_runs table (isolated persistence)

  NOT a Flask route. NOT imported by app.py. NOT registered with any scheduler.

SNAPSHOT
  NYY vs BOS, 2026-08-10, moneyline, preflight PASS.
  Exact construction: _full_row() from tests/test_universal_agent_b3a.py.
  No fabricated or synthetic data — this is the canonical B3A full-pass fixture.

AUTHORIZED CALL PARAMETERS
  model          = "claude-haiku-4-5-20251001"  (exact literal in canary_config.py)
  max_tokens     = 1024                          (uniform, not configurable)
  budget_ceiling = $0.10                         (MAX_TOTAL_SPEND_USD in canary_config.py)
  retries        = 0                             (AUTOMATIC_RETRIES = 0)
  timeout        = 30s                           (PER_CALL_TIMEOUT_SECONDS = 30.0)

FLAG DISCIPLINE
  UAC_MLB_ML_CLAUDE_SHADOW_ENABLED is set to "true" in os.environ BEFORE any
  gate_engine imports (flag is read at canary_config module load time).
  It is set back to "false" in the finally block unconditionally.
  CAN_EXECUTE, PRODUCTION_AUTHORITY, and all Weather flags are never touched.

STOP CONDITIONS (any one aborts the run immediately, no retry):
  - Wrong or missing response model
  - Missing usage metadata
  - Budget guard failure
  - Timeout, network failure, API error
  - Malformed response
  - Schema violation or forbidden governance key
  - CapabilityBoundary failure
  - Unexpected persistence failure (logged, does not stop run; noted in output)
"""
from __future__ import annotations

# ── FLAG MUST BE SET BEFORE ANY gate_engine IMPORTS ──────────────────────────
# UAC_MLB_ML_CLAUDE_SHADOW_ENABLED is read at module load time in
# gate_engine.universal_agent.canary.canary_config via _read_bool_flag().
# Setting it here (before any import of that module) ensures the flag is True
# when canary_config is first imported.
import os
os.environ["UAC_MLB_ML_CLAUDE_SHADOW_ENABLED"] = "true"

import json
import sys
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
_log = logging.getLogger("b3c_canary_runner")

# ── Project root on sys.path ──────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── gate_engine imports (AFTER env var set) ───────────────────────────────────
from gate_engine.universal_agent.canary.canary_config import (
    UAC_MLB_ML_CLAUDE_SHADOW_ENABLED,
    PINNED_MODEL,
    MAX_CALLS,
    MAX_TOTAL_SPEND_USD,
    MAX_TOKENS,
    AUTOMATIC_RETRIES,
    PER_CALL_TIMEOUT_SECONDS,
)
from gate_engine.universal_agent.canary.canary_pipeline import run_canary_pipeline


# ── Frozen snapshot — NYY vs BOS, 2026-08-10, preflight PASS ─────────────────
# Exact construction from tests/test_universal_agent_b3a.py::_full_row()
# All eight source coverage fields present.
SNAPSHOT_ROW: dict = {
    # Identity
    "event_id":    "mlb-2026-08-10-nyy-bos",
    "sport":       "MLB",
    "market":      "moneyline",
    # Team / event
    "team":        "New York Yankees",
    "opponent":    "Boston Red Sox",
    "team_id":     "nyy",
    "opponent_id": "bos",
    "slate_date":  "2026-08-10",
    # Source metadata
    "pulled_at":       "2026-08-10T10:00:00+00:00",
    "starter_source":  "mlb-stats-api",
    "lineup_source":   "mlb-stats-api",
    "weather_source":  "nws-api",
    "odds_source":     "odds-api",
    # Gate 1 — starter / lineup
    "starter_status": "CONFIRMED",
    "lineup_status":  "CONFIRMED",
    # Gate 2 — event / weather
    "event_status":   "SCHEDULED",
    "weather_status": "CLEAR",
    # Gate 3 — no-vig / model
    "kalshi_multiplier":              1.80,
    "sportsbook_no_vig_probability":  0.58,
    "kalshi_breakeven_probability":   0.5556,
    "breakeven_gap":                  0.0244,
    # Moneyline model outputs
    "model_probability":                  0.60,
    "calibrated_probability_lower_bound": 0.575,
    # Odds
    "candidate_odds": -138,
    "opponent_odds":  +118,
    # Preflight outcome
    "preflight_checked":  True,
    "preflight_status":   "PASS",
    "preflight_blockers": [],
    "upgrade_allowed":    True,
    "terminal_label":     "MODEL_QUALIFIED_HOLD",
    # Gate record
    "gates": {
        "mlb_winner_preflight": {
            "hard_blockers":    [],
            "watch_blockers":   [],
            "preflight_status": "PASS",
        }
    },
}
SNAPSHOT_NAME = "NYY vs BOS — 2026-08-10 — moneyline — preflight PASS"


# ── Database connection ───────────────────────────────────────────────────────

def get_db_conn():
    import psycopg2
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL env var not set")
    return psycopg2.connect(db_url, connect_timeout=10)


# ── Anthropic client ──────────────────────────────────────────────────────────

def build_client():
    import anthropic as sdk
    api_key = (
        os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        raise RuntimeError("No Anthropic API key available in environment")
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    if base_url:
        return sdk.Anthropic(api_key=api_key, base_url=base_url)
    return sdk.Anthropic(api_key=api_key)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    canary_run_id = f"b3c-live-{uuid.uuid4()}"
    run_start = datetime.now(timezone.utc).isoformat()

    _log.info("=" * 72)
    _log.info("B3C LIVE CANARY RUN STARTING")
    _log.info("canary_run_id       : %s", canary_run_id)
    _log.info("snapshot            : %s", SNAPSHOT_NAME)
    _log.info("event_id            : %s", SNAPSHOT_ROW["event_id"])
    _log.info("run_start           : %s", run_start)
    _log.info("-" * 72)
    _log.info("AUTHORIZED PARAMETERS")
    _log.info("  PINNED_MODEL             = %s", PINNED_MODEL)
    _log.info("  MAX_TOKENS               = %s", MAX_TOKENS)
    _log.info("  MAX_CALLS                = %s", MAX_CALLS)
    _log.info("  MAX_TOTAL_SPEND_USD      = $%.2f", MAX_TOTAL_SPEND_USD)
    _log.info("  AUTOMATIC_RETRIES        = %s", AUTOMATIC_RETRIES)
    _log.info("  PER_CALL_TIMEOUT_SECONDS = %.1f", PER_CALL_TIMEOUT_SECONDS)
    _log.info("-" * 72)
    _log.info("FLAG STATE AT MODULE LOAD")
    _log.info("  UAC_MLB_ML_CLAUDE_SHADOW_ENABLED = %s", UAC_MLB_ML_CLAUDE_SHADOW_ENABLED)
    _log.info("  CAN_EXECUTE              = %s",
              repr(os.environ.get("CAN_EXECUTE", "<not set>")))
    _log.info("  PRODUCTION_AUTHORITY     = %s",
              repr(os.environ.get("PRODUCTION_AUTHORITY", "<not set>")))
    _log.info("  SHADOW_RESEARCH_API_ENABLED = %s",
              repr(os.environ.get("SHADOW_RESEARCH_API_ENABLED", "<not set>")))
    _log.info("=" * 72)

    if not UAC_MLB_ML_CLAUDE_SHADOW_ENABLED:
        _log.error("ABORT: UAC_MLB_ML_CLAUDE_SHADOW_ENABLED is False at module load. "
                   "env var was not set before the import chain executed.")
        sys.exit(1)

    # Build client and DB connection before starting
    try:
        client = build_client()
        _log.info("Anthropic client constructed (real)")
    except Exception as exc:
        _log.error("ABORT: Failed to build Anthropic client: %s", exc)
        sys.exit(1)

    conn = None
    try:
        conn = get_db_conn()
        _log.info("Database connection established")
    except Exception as exc:
        _log.warning("DB connection failed — run will proceed but NOT persist: %s", exc)

    # ── Execute ───────────────────────────────────────────────────────────────
    result = None
    try:
        _log.info("Invoking run_canary_pipeline() …")
        result = run_canary_pipeline(
            SNAPSHOT_ROW,
            canary_run_id,
            db_conn=conn,
            _client=client,
            # _force_enabled NOT used — real flag governs
        )
    finally:
        # ── FLAG RESET — unconditional ────────────────────────────────────────
        os.environ["UAC_MLB_ML_CLAUDE_SHADOW_ENABLED"] = "false"
        final_flag = os.environ.get("UAC_MLB_ML_CLAUDE_SHADOW_ENABLED", "<not set>")
        _log.info("=" * 72)
        _log.info("FLAG RESET: UAC_MLB_ML_CLAUDE_SHADOW_ENABLED = %s", final_flag)
        _log.info("=" * 72)

    if conn:
        try:
            conn.close()
        except Exception:
            pass

    # ── Report ────────────────────────────────────────────────────────────────
    if result is None:
        _log.error("run_canary_pipeline() returned None — unexpected")
        sys.exit(1)

    _log.info("")
    _log.info("=" * 72)
    _log.info("CANARY RUN COMPLETE")
    _log.info("  canary_run_id     : %s", result.canary_run_id)
    _log.info("  pipeline_status   : %s", result.pipeline_status)
    _log.info("  calls_attempted   : %d", result.calls_attempted)
    _log.info("  calls_successful  : %d", result.calls_successful)
    _log.info("  total_spend_usd   : $%.6f", result.total_spend_usd)
    _log.info("  persisted         : %s", result.persisted)
    if result.error_message:
        _log.info("  error_message     : %s", result.error_message)
    if result.disabled_reason:
        _log.info("  disabled_reason   : %s", result.disabled_reason)
    _log.info("-" * 72)

    # Per-call log
    if result.call_log:
        _log.info("CALL LOG (%d entries):", len(result.call_log))
        for i, rec in enumerate(result.call_log, 1):
            _log.info(
                "  [%d] role=%-24s status=%-30s "
                "req_model=%s resp_model=%s "
                "in=%s out=%s latency=%sms cost=$%s",
                i, rec.role_id, rec.status,
                rec.requested_model, rec.response_model,
                rec.input_tokens, rec.output_tokens,
                rec.latency_ms,
                f"{rec.calculated_cost_usd:.6f}" if rec.calculated_cost_usd is not None else "n/a",
            )
            if rec.violation_codes:
                _log.info("    violation_codes : %s", rec.violation_codes)
            if rec.error_classification:
                _log.info("    error           : %s", rec.error_classification)
            if rec.raw_output_hash:
                _log.info("    raw_hash        : %s", rec.raw_output_hash)
    else:
        _log.info("CALL LOG: empty (0 entries)")

    # Orchestrator result
    if result.orchestrator_result is not None:
        orch = result.orchestrator_result
        _log.info("-" * 72)
        _log.info("ORCHESTRATOR RESULT")
        try:
            _log.info("  bundle_status       : %s", orch.bundle.status)
            _log.info("  roles_completed     : %d", len(orch.role_results))
            contradictions = getattr(orch, "contradictions", []) or []
            _log.info("  contradictions      : %d", len(contradictions))
            for role_id, rr in orch.role_results.items():
                _log.info("    %-24s status=%s canonical_hash=%s",
                          role_id,
                          getattr(rr, "status", "?"),
                          _sha256_json(getattr(rr, "advisory_findings", None)))
        except Exception as exc:
            _log.info("  (orchestrator introspection error: %s)", exc)
    else:
        _log.info("ORCHESTRATOR RESULT: None (pipeline aborted before orchestrator)")

    # Adapter result
    if result.adapter_result is not None:
        adp = result.adapter_result
        _log.info("-" * 72)
        _log.info("ADAPTER RESULT")
        try:
            _log.info("  adapter_status  : %s", adp.status)
            pkt = adp.packet
            _log.info("  snapshot_id     : %s", pkt.snapshot_id)
            _log.info("  lane            : %s", pkt.lane)
            _log.info("  event_id        : %s", pkt.canonical_event_id)
        except Exception as exc:
            _log.info("  (adapter introspection error: %s)", exc)

    _log.info("=" * 72)

    # Emit machine-readable summary for DB query step
    summary = {
        "canary_run_id":   result.canary_run_id,
        "pipeline_status": result.pipeline_status,
        "calls_attempted": result.calls_attempted,
        "calls_successful": result.calls_successful,
        "total_spend_usd": result.total_spend_usd,
        "persisted":       result.persisted,
        "snapshot":        SNAPSHOT_NAME,
        "event_id":        SNAPSHOT_ROW["event_id"],
        "flag_after_run":  os.environ.get("UAC_MLB_ML_CLAUDE_SHADOW_ENABLED", "<not set>"),
    }
    print("\nCANARY_RUN_SUMMARY_JSON:", json.dumps(summary))


def _sha256_json(obj: Any) -> str:
    import hashlib
    try:
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True, default=str).encode()
        ).hexdigest()[:16] + "…"
    except Exception:
        return "n/a"


if __name__ == "__main__":
    main()
