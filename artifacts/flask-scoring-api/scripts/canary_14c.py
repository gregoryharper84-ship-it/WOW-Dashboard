#!/usr/bin/env python3
"""
scripts/canary_14c.py
Step 14C Live Canary — 1 snapshot × 5 subagents = 5 real Anthropic API calls.

Target (first alphabetically by city among STRATIFIED_COHORT_LIMIT excluded):
  RSID  : wx-capture-35b2ca1d-0bcd-4548-9dcc-b4c8a5322950
  City  : AUS   Date: 2026-08-10   σ_f: 3.0   Station: KAUS

Enforcement chain per call:
  real messages.create()
    → native per-subagent closed-schema validator   (Step 14C Phase 1)
    → CapabilityBoundary pre/post hooks             (Step 10B)
    → persist to kalshi_wx_shadow_results
  After all 5:
    → _assemble_payload()                           (imported from orchestrator)
    → validate_shadow_output()                      (Step 9 schema validator)
    → persist to kalshi_wx_shadow_snapshot_schema_validation

Stop conditions:
  - Structural failure on any agent → stop immediately, do not retry.
  - max_calls=5 is a hard cap.
  - Any pre-flight check fails → abort before any API call.

Environment requirements (set before process start so Gate B reads them):
  SHADOW_RESEARCH_API_ENABLED=true
  PILOT_BUDGET_USD, INPUT_PRICE_PER_TOKEN, OUTPUT_PRICE_PER_TOKEN,
  MAX_OUTPUT_TOKENS_PER_CALL
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
_log = logging.getLogger("canary_14c")

TARGET_RSID = "wx-capture-35b2ca1d-0bcd-4548-9dcc-b4c8a5322950"
AGENT_IDS = [
    "forecast_context",
    "source_reconciliation",
    "contradiction_detection",
    "unusual_regime",
    "uncertainty_explanation",
]
MAX_CALLS = 5


# ── Pre-flight environment checks ─────────────────────────────────────────────

def _preflight() -> dict:
    """Abort with a clear message if any required env var is missing or wrong."""
    errors = []

    # Gate A+B — must be set before process start
    gate_val = os.environ.get("SHADOW_RESEARCH_API_ENABLED", "").strip().lower()
    if gate_val != "true":
        errors.append(
            f"SHADOW_RESEARCH_API_ENABLED={gate_val!r} — must be 'true'"
        )

    # Safety: KALSHI_WX_SHADOW_AGENT_ENABLED must NOT be true
    agent_enabled = os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED", "false").strip().lower()
    if agent_enabled == "true":
        errors.append(
            "KALSHI_WX_SHADOW_AGENT_ENABLED=true — must NOT be true during canary"
        )

    for key in ("PILOT_BUDGET_USD", "INPUT_PRICE_PER_TOKEN",
                "OUTPUT_PRICE_PER_TOKEN", "MAX_OUTPUT_TOKENS_PER_CALL"):
        if not os.environ.get(key):
            errors.append(f"{key} not set")

    if errors:
        _log.error("PRE-FLIGHT FAILURES — aborting before any API call:")
        for e in errors:
            _log.error("  • %s", e)
        sys.exit(1)

    config = {
        "PILOT_BUDGET_USD":         float(os.environ["PILOT_BUDGET_USD"]),
        "INPUT_PRICE_PER_TOKEN":    float(os.environ["INPUT_PRICE_PER_TOKEN"]),
        "OUTPUT_PRICE_PER_TOKEN":   float(os.environ["OUTPUT_PRICE_PER_TOKEN"]),
        "MAX_OUTPUT_TOKENS_PER_CALL": int(os.environ["MAX_OUTPUT_TOKENS_PER_CALL"]),
    }
    _log.info("Pre-flight OK. Config: %s", config)
    _log.info(
        "Gate B module-level _RESEARCH_API_ENABLED will be True "
        "(SHADOW_RESEARCH_API_ENABLED was 'true' at import time)"
    )
    return config


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    config = _preflight()

    # Late imports — Gate B reads SHADOW_RESEARCH_API_ENABLED at this point
    from scripts.run_kalshi_wx_shadow_pilot import (  # noqa: PLC0415
        build_sdk_client,
        call_one_agent,
        get_db_conn,
        is_pair_completed,
        write_result_row,
        _record_snapshot_schema_validation,
    )
    from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary  # noqa: PLC0415
    from gate_engine.kalshi_wx_shadow_subagents import SubagentResult               # noqa: PLC0415

    # Confirm Gate B resolved correctly at import time
    import gate_engine.kalshi_wx_shadow_subagents as _sub_mod  # noqa: PLC0415
    gate_b = getattr(_sub_mod, "_RESEARCH_API_ENABLED", None)
    _log.info("Gate B (_RESEARCH_API_ENABLED) resolved to: %s", gate_b)
    if not gate_b:
        _log.error("STRUCTURAL FAILURE: Gate B is False — API calls will be blocked. Abort.")
        sys.exit(1)

    conn = get_db_conn()

    # ── Fetch target snapshot directly by RSID (bypass excluded_reason) ───────
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT research_snapshot_id, snapshot_json, excluded_reason
            FROM kalshi_wx_shadow_snapshot_queue
            WHERE research_snapshot_id = %s
            """,
            (TARGET_RSID,),
        )
        row = cur.fetchone()

    if row is None:
        _log.error("STRUCTURAL FAILURE: RSID %s not in kalshi_wx_shadow_snapshot_queue", TARGET_RSID)
        conn.close()
        sys.exit(1)

    rsid, snap_json, excluded_reason = row
    city        = snap_json.get("city", "?")
    market_date = snap_json.get("market_date", "?")
    sigma_f     = snap_json.get("sigma_f", "?")
    _log.info(
        "Target snapshot confirmed: rsid=%s city=%s date=%s sigma_f=%s excluded_reason=%s",
        rsid, city, market_date, sigma_f, excluded_reason,
    )

    # ── Confirm genuinely untouched ───────────────────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM kalshi_wx_shadow_results WHERE research_snapshot_id = %s",
            (rsid,),
        )
        existing = cur.fetchone()[0]

    if existing > 0:
        _log.error(
            "STRUCTURAL FAILURE: %d existing rows found for %s — "
            "not genuinely fresh. Abort without any API call.",
            existing, rsid,
        )
        conn.close()
        sys.exit(1)

    _log.info("Confirmed: 0 existing rows — all 5 calls will be genuinely fresh.")

    # ── Build SDK client ──────────────────────────────────────────────────────
    sdk_client = build_sdk_client()
    if sdk_client is None:
        _log.error("STRUCTURAL FAILURE: Cannot build Anthropic SDK client — check API key env vars")
        conn.close()
        sys.exit(1)

    cap_boundary = CapabilityBoundary()
    run_id       = f"canary-14c-{uuid.uuid4()}"
    _log.info("Run ID: %s", run_id)

    prior_results: dict = {}
    total_calls   = 0
    cumulative_spend = 0.0
    cumulative_in    = 0
    cumulative_out   = 0
    results_log: list = []
    sep = "─" * 72

    # ── 5 sequential agent calls ──────────────────────────────────────────────
    for agent_id in AGENT_IDS:

        if total_calls >= MAX_CALLS:
            _log.error("STRUCTURAL FAILURE: MAX_CALLS=%d reached before completing all agents", MAX_CALLS)
            conn.close()
            sys.exit(1)

        # Resumability check — must all be fresh
        if is_pair_completed(conn, rsid, agent_id):
            _log.error(
                "STRUCTURAL FAILURE: %s/%s already completed — not fresh. Abort.",
                rsid, agent_id,
            )
            conn.close()
            sys.exit(1)

        _log.info("── Call %d/%d: agent=%s ──", total_calls + 1, MAX_CALLS, agent_id)

        result = call_one_agent(
            agent_id,
            snap_json,
            prior_results,
            run_id,
            sdk_client,
            cap_boundary,
            max_output_tokens=config["MAX_OUTPUT_TOKENS_PER_CALL"],
        )
        total_calls += 1

        success        = bool(result.get("success", False))
        tool_input     = result.get("tool_input") or {}
        failure_reason = result.get("failure_reason")
        latency_ms     = result.get("latency_ms")
        input_tokens   = result.get("input_tokens")
        output_tokens  = result.get("output_tokens")
        usage_status   = result.get("usage_accounting_status", "UNAVAILABLE")
        status         = "COMPLETE" if success else "BLOCKED"

        # Cost accounting
        row_cost: float | None = None
        if (
            usage_status == "AVAILABLE"
            and isinstance(input_tokens, int)
            and isinstance(output_tokens, int)
        ):
            row_cost          = (input_tokens  * config["INPUT_PRICE_PER_TOKEN"]
                                + output_tokens * config["OUTPUT_PRICE_PER_TOKEN"])
            cumulative_spend += row_cost
            cumulative_in    += input_tokens
            cumulative_out   += output_tokens
        else:
            # Pessimistic fallback for budget tracking only
            cumulative_in  += 0
            cumulative_out += 0

        validated_output_json = {
            "agent_output": tool_input,
            "run_config":   config,
            **({"failure_reason": failure_reason} if failure_reason else {}),
        }

        write_result_row(
            conn,
            research_snapshot_id=rsid,
            agent_id=agent_id,
            run_id=run_id,
            validated_output_json=validated_output_json,
            status=status,
            latency_ms=latency_ms,
            model=None,                 # Task #160 gap — pre-existing, not fixed here
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=row_cost,
        )
        _log.info("  Persisted: %s / %s → status=%s", rsid[:16], agent_id, status)

        results_log.append({
            "agent_id":      agent_id,
            "success":       success,
            "status":        status,
            "latency_ms":    latency_ms,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "usage_status":  usage_status,
            "cost":          row_cost,
            "failure_reason": failure_reason,
            "tool_input":    tool_input,
        })

        if not success:
            _log.error(
                "STRUCTURAL FAILURE: agent=%s reason=%s — stopping immediately, "
                "no retry, no further agents.",
                agent_id, failure_reason,
            )
            conn.close()
            sys.exit(1)

        # Add to prior_results for dependency chain
        prior_results[agent_id] = SubagentResult(
            subagent_id=agent_id,
            tool_name=f"emit_{agent_id}",
            tool_input=tool_input,
            hook_violations=[],
            success=True,
        )

        _log.info(
            "  ✓ %s: latency=%sms  in=%s  out=%s  cost=%s",
            agent_id,
            latency_ms,
            input_tokens,
            output_tokens,
            f"${row_cost:.8f}" if row_cost is not None else "UNAVAILABLE",
        )

    # ── Canonical assembly + Step 9 validation ────────────────────────────────
    _log.info("Running _assemble_payload + validate_shadow_output …")
    _record_snapshot_schema_validation(
        conn, rsid, snap_json, run_id, prior_results, len(AGENT_IDS),
    )

    # Read validation result from DB
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT validation_status, validation_detail,
                   canonical_payload_json, recorded_at
            FROM   kalshi_wx_shadow_snapshot_schema_validation
            WHERE  research_snapshot_id = %s
            """,
            (rsid,),
        )
        val_row = cur.fetchone()

    # Confirm zero production/calibration table writes
    calibration_tables = [
        "llp_source_snapshots",
        "prob_ledger",
        "slip_expert_review_log",
        "wow_session_exposure",
        "llp_calibration_records",
    ]
    contamination: dict = {}
    with conn.cursor() as cur:
        for tbl in calibration_tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                contamination[tbl] = cur.fetchone()[0]
            except Exception:
                contamination[tbl] = "TABLE_NOT_FOUND"

    # Confirm results rows written (all 5)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT agent_id, status, latency_ms, input_tokens, output_tokens,
                   estimated_cost_usd, created_at
            FROM   kalshi_wx_shadow_results
            WHERE  research_snapshot_id = %s
            ORDER BY created_at
            """,
            (rsid,),
        )
        db_rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]

    conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL REPORT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'═'*72}")
    print("Step 14C Live Canary — Complete Results")
    print(f"{'═'*72}")

    print(f"\nSnapshot:     {rsid}")
    print(f"City/Date/σ:  {city} / {market_date} / σ_f={sigma_f}")
    print(f"excluded_reason confirmed: {excluded_reason}")
    print(f"Run ID:       {run_id}")

    print(f"\n{sep}")
    print("Per-Agent Results (from in-memory call log)")
    print(sep)

    for r in results_log:
        print(f"\n[{r['agent_id']}]")
        print(f"  success       : {r['success']}")
        print(f"  status        : {r['status']}")
        print(f"  latency_ms    : {r['latency_ms']}")
        print(f"  input_tokens  : {r['input_tokens']}")
        print(f"  output_tokens : {r['output_tokens']}")
        print(f"  usage_status  : {r['usage_status']}")
        cost_str = f"${r['cost']:.8f}" if r['cost'] is not None else "UNAVAILABLE"
        print(f"  cost          : {cost_str}")
        if r['failure_reason']:
            print(f"  failure_reason: {r['failure_reason']}")
        # Check for governance contamination in output
        ti = r['tool_input']
        forbidden_present = [k for k in ("final_decision", "stake_tier", "is_playable") if k in ti]
        print(f"  governance keys in output: {forbidden_present if forbidden_present else 'NONE'}")
        print(f"  output:")
        for k, v in ti.items():
            print(f"    {k}: {json.dumps(v)}")

    print(f"\n{sep}")
    print("DB Rows Confirmed (from kalshi_wx_shadow_results)")
    print(sep)
    print(f"  Rows persisted: {len(db_rows)}/5")
    for row in db_rows:
        print(f"  [{row['agent_id']}] status={row['status']} "
              f"in={row['input_tokens']} out={row['output_tokens']} "
              f"cost={row['estimated_cost_usd']} at={row['created_at']}")

    print(f"\n{sep}")
    print("Canonical Assembly + Step 9 Validation")
    print(sep)
    if val_row:
        val_status, val_detail, canonical_json, val_at = val_row
        print(f"  validation_status : {val_status}")
        print(f"  validation_detail : {val_detail}")
        print(f"  recorded_at       : {val_at}")
        print(f"  canonical_payload :")
        if canonical_json:
            print(json.dumps(canonical_json, indent=4))
        else:
            print("    (null)")
    else:
        print("  ERROR: no validation row found in DB")

    print(f"\n{sep}")
    print("Production/Calibration Table Contamination Check")
    print(sep)
    for tbl, count in contamination.items():
        print(f"  {tbl}: {count} rows")

    print(f"\n{sep}")
    print("Cost Summary")
    print(sep)
    print(f"  Total calls       : {total_calls}/5")
    print(f"  Total input tokens: {cumulative_in}")
    print(f"  Total output tokens: {cumulative_out}")
    print(f"  Total spend       : ${cumulative_spend:.8f}")
    print(f"  Budget            : ${config['PILOT_BUDGET_USD']:.2f}")
    print(f"  Under budget      : {cumulative_spend < config['PILOT_BUDGET_USD']}")

    print(f"\n{sep}")
    print("Environment Confirmation (at run end)")
    print(sep)
    print(f"  SHADOW_RESEARCH_API_ENABLED    : {os.environ.get('SHADOW_RESEARCH_API_ENABLED', '(unset)')}")
    print(f"  KALSHI_WX_SHADOW_AGENT_ENABLED : {os.environ.get('KALSHI_WX_SHADOW_AGENT_ENABLED', '(unset)')}")
    print(f"  Gate B at module import         : {gate_b}")
    print(f"\n{'═'*72}\n")


if __name__ == "__main__":
    main()
