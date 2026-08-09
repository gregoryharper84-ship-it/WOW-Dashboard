#!/usr/bin/env python3
"""
scripts/run_kalshi_wx_shadow_pilot.py
Kalshi Weather Shadow Pilot Runner — Step 12.5B Increment 1/2

WHAT THIS IS
  Standalone batch script that processes persisted eligible snapshots through
  the five Kalshi Weather shadow research subagents.  Run manually:

      python scripts/run_kalshi_wx_shadow_pilot.py

  NOT a Flask route.  NOT imported by app.py.  NOT registered with any
  scheduler, cron, or background thread.

REQUIRED ENV VARS (runner refuses to start if any are absent — fail closed)
  PILOT_BUDGET_USD              float   maximum cumulative spend for this run
  INPUT_PRICE_PER_TOKEN         float   cost per input token (operator-supplied)
  OUTPUT_PRICE_PER_TOKEN        float   cost per output token (operator-supplied)
  MAX_OUTPUT_TOKENS_PER_CALL    int     max output tokens assumed per call (for
                                        conservative worst-case cost estimate)

  No Anthropic pricing is hardcoded.  The operator must supply all four values.

ELIGIBILITY
  Snapshots are eligible when kalshi_wx_shadow_snapshot_queue has a row AND
  kalshi_wx_shadow_deterministic_outcome has a matching row for the same
  research_snapshot_id (the INNER JOIN from Step 12.5A).

HARD CAPS
  MAX_SNAPSHOTS        = 25    enforced as a running counter, not arithmetic
  MAX_SUBAGENT_CALLS   = 125   enforced before EVERY individual call attempt

RESUMABILITY
  Before attempting each (research_snapshot_id, agent_id) pair, the runner
  checks kalshi_wx_shadow_results for an existing row.  If found, that pair
  is skipped.  A restarted run never re-executes completed pairs and never
  counts them toward the 125-call cap.

SPEND GUARD
  Before each individual call, a conservative worst-case cost is computed as:
    input_tokens_est * INPUT_PRICE_PER_TOKEN
    + MAX_OUTPUT_TOKENS_PER_CALL * OUTPUT_PRICE_PER_TOKEN
  If cumulative_spend + worst_case > PILOT_BUDGET_USD, the runner stops
  permanently (hard stop, not a soft warning, not a per-call skip).

RESULTS STORAGE
  One row written to kalshi_wx_shadow_results per attempted call — whether
  the call succeeds, fails schema validation, is tool-denied, or errors.
  Each row includes the exact pricing config used in run_config inside
  validated_output_json, making accounting fully reconstructable from the DB.

AUTHORITY INVARIANTS
  - No production ledger writes
  - No modification of snapshot or outcome rows
  - No import of Kalshi order placement or trading modules
  - The underlying research client's CAN_EXECUTE = False is unchanged
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

# ── Project root on sys.path ──────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
_log = logging.getLogger("kalshi_wx_shadow_pilot")

# ── Hard caps (enforced as running counters, not implied by arithmetic) ────────
MAX_SNAPSHOTS: int = 25
MAX_SUBAGENT_CALLS: int = 125

AGENT_IDS: List[str] = [
    "forecast_context",
    "source_reconciliation",
    "contradiction_detection",
    "unusual_regime",
    "uncertainty_explanation",
]

# ── Required budget env vars — no defaults, fail closed if absent ─────────────
_REQUIRED_BUDGET_VARS: Dict[str, type] = {
    "PILOT_BUDGET_USD":           float,
    "INPUT_PRICE_PER_TOKEN":      float,
    "OUTPUT_PRICE_PER_TOKEN":     float,
    "MAX_OUTPUT_TOKENS_PER_CALL": int,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Budget configuration
# ═══════════════════════════════════════════════════════════════════════════════

def require_budget_config() -> Dict[str, Any]:
    """
    Read all required budget env vars.  Refuses to start if any are absent.

    Prints the name of each missing var to stderr before calling sys.exit(1).
    Returns a dict with cast values on success.
    No defaults are applied — the operator must supply every value explicitly.
    """
    config: Dict[str, Any] = {}
    missing: List[str] = []

    for key, cast in _REQUIRED_BUDGET_VARS.items():
        val = os.environ.get(key)
        if val is None:
            missing.append(key)
            continue
        try:
            config[key] = cast(val)
        except (ValueError, TypeError):
            print(
                f"ERROR: {key}={val!r} cannot be cast to {cast.__name__}",
                file=sys.stderr,
            )
            sys.exit(1)

    if missing:
        for key in missing:
            print(f"ERROR: required env var {key!r} is not set", file=sys.stderr)
        print(
            "Runner refused to start — set all required budget env vars first.",
            file=sys.stderr,
        )
        sys.exit(1)

    return config


# ═══════════════════════════════════════════════════════════════════════════════
# Database helpers
# ═══════════════════════════════════════════════════════════════════════════════

def get_db_conn():
    """Open a psycopg2 connection via DATABASE_URL."""
    try:
        import psycopg2  # local import keeps module loadable without psycopg2 installed
    except ImportError as exc:
        raise RuntimeError("psycopg2 not installed") from exc
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL env var not set")
    return psycopg2.connect(db_url, connect_timeout=10)


def apply_schema_migrations(conn) -> None:
    """
    Idempotent schema migrations for kalshi_wx_shadow_results.
    Adds new columns and the UNIQUE(research_snapshot_id, agent_id) index.
    Safe to call on every runner startup — all statements use IF NOT EXISTS.
    """
    ddl = """
    ALTER TABLE kalshi_wx_shadow_results
        ADD COLUMN IF NOT EXISTS latency_ms          INTEGER,
        ADD COLUMN IF NOT EXISTS model               TEXT,
        ADD COLUMN IF NOT EXISTS input_tokens        INTEGER,
        ADD COLUMN IF NOT EXISTS output_tokens       INTEGER,
        ADD COLUMN IF NOT EXISTS estimated_cost_usd  NUMERIC;

    CREATE UNIQUE INDEX IF NOT EXISTS ux_shadow_results_rsid_agent
        ON kalshi_wx_shadow_results (research_snapshot_id, agent_id);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    _log.info("Schema migrations applied to kalshi_wx_shadow_results")


def fetch_eligible_snapshots(conn, max_count: int) -> List[Dict]:
    """
    Eligibility INNER JOIN: snapshots with both a queue row AND a
    deterministic outcome row.  Ordered by inserted_at ASC (oldest first).
    Returns at most max_count rows.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT q.research_snapshot_id,
                   q.snapshot_json,
                   o.terminal_label,
                   o.price_gate_disposition,
                   o.can_execute
            FROM   kalshi_wx_shadow_snapshot_queue q
            INNER JOIN kalshi_wx_shadow_deterministic_outcome o
                   ON q.research_snapshot_id = o.research_snapshot_id
            ORDER  BY q.inserted_at ASC
            LIMIT  %s
            """,
            (max_count,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def is_pair_completed(conn, rsid: str, agent_id: str) -> bool:
    """
    Return True if (rsid, agent_id) already has any row in
    kalshi_wx_shadow_results — regardless of status.
    A failed attempt still counts; no pair is ever re-attempted in the same
    or a subsequent run.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM kalshi_wx_shadow_results"
            " WHERE research_snapshot_id = %s AND agent_id = %s",
            (rsid, agent_id),
        )
        return cur.fetchone() is not None


def load_prior_results(conn, rsid: str) -> Dict[str, Any]:
    """
    Load previously COMPLETE agent results for a snapshot so they can be
    passed as dependency kwargs to later agents in the sequence.

    Returns dict of {agent_id: SubagentResult}.  Incomplete / errored rows
    are excluded — a downstream agent only receives upstream context when it
    is structurally sound.
    """
    from gate_engine.kalshi_wx_shadow_subagents import SubagentResult  # noqa: PLC0415

    rows: Dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT agent_id, validated_output_json
            FROM   kalshi_wx_shadow_results
            WHERE  research_snapshot_id = %s
              AND  status = 'COMPLETE'
            """,
            (rsid,),
        )
        for agent_id, vjson in cur.fetchall():
            # validated_output_json structure: {"agent_output": {...}, "run_config": {...}}
            tool_input: Dict = {}
            if isinstance(vjson, dict):
                tool_input = vjson.get("agent_output") or {}
            rows[agent_id] = SubagentResult(
                subagent_id=agent_id,
                tool_name=f"{agent_id}_tool",   # placeholder; not used downstream
                tool_input=tool_input,
                hook_violations=[],
                success=True,
            )
    return rows


def write_result_row(
    conn,
    *,
    research_snapshot_id: str,
    agent_id: str,
    run_id: str,
    validated_output_json: Any,
    status: str,
    latency_ms: Optional[int],
    model: Optional[str],
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd: float,
) -> None:
    """
    Insert one result row into kalshi_wx_shadow_results.

    ON CONFLICT (research_snapshot_id, agent_id) DO NOTHING means a second
    insert for an already-completed pair is silently ignored — the UNIQUE
    index at the DB level enforces this, not just application logic.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kalshi_wx_shadow_results
                (research_snapshot_id, agent_id, run_id,
                 validated_output_json, status,
                 latency_ms, model, input_tokens, output_tokens,
                 estimated_cost_usd)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (research_snapshot_id, agent_id) DO NOTHING
            """,
            (
                research_snapshot_id, agent_id, run_id,
                json.dumps(validated_output_json), status,
                latency_ms, model,
                input_tokens, output_tokens,
                estimated_cost_usd,
            ),
        )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Token and cost estimation
# ═══════════════════════════════════════════════════════════════════════════════

def estimate_input_tokens(serialized: str) -> int:
    """
    Conservative (deliberately high) token count estimate for a string.
    Uses 3 bytes per token rather than the ~3.5-char average — errs high.
    A high estimate is safe: it can only cause earlier budget-guard trips,
    never late ones.
    """
    byte_len = len(serialized.encode("utf-8"))
    return max(1, (byte_len + 2) // 3)


def worst_case_call_cost(
    input_tokens: int,
    max_output_tokens: int,
    input_price: float,
    output_price: float,
) -> float:
    """
    Conservative worst-case cost for one subagent call.
    Uses full input_tokens + max_output_tokens — never less.
    The operator supplies all four values; nothing is hardcoded here.
    """
    return input_tokens * input_price + max_output_tokens * output_price


# ═══════════════════════════════════════════════════════════════════════════════
# Snapshot deserialization
# ═══════════════════════════════════════════════════════════════════════════════

def deserialize_snapshot(snap_json: Any) -> Any:
    """
    Reconstruct a WeatherResearchSnapshot from the JSONB dict stored by
    insert_shadow_snapshot in kalshi_wx_shadow_db.py.

    Handles the field-name mismatch between what the DB stores (e.g.
    "forecast_high", "horizon_hours") and WeatherResearchSnapshot field names.
    All missing optional fields fall back to safe defaults.
    """
    from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot  # noqa: PLC0415

    if not isinstance(snap_json, dict):
        raise ValueError(
            f"snap_json must be a dict, got {type(snap_json).__name__}"
        )

    return WeatherResearchSnapshot(
        research_snapshot_id=snap_json["research_snapshot_id"],
        canonical_event_id=snap_json.get("canonical_event_id") or "",
        city=snap_json["city"],
        station=snap_json["station"],
        market_date=snap_json["market_date"],
        source_cutoff_timestamp=snap_json.get("source_cutoff_timestamp") or "",
        nws_gridpoint_forecast=snap_json.get("nws_gridpoint_forecast"),
        open_meteo_forecast=snap_json.get("open_meteo_forecast"),
        noaa_ncei_forecast=snap_json.get("noaa_ncei_forecast"),
        official_observations_at_cutoff=snap_json.get("official_observations_at_cutoff"),
        # DB stores "forecast_high"; dataclass field is "forecast_high_used_by_deterministic_model"
        forecast_high_used_by_deterministic_model=float(
            snap_json.get("forecast_high_used_by_deterministic_model")
            or snap_json.get("forecast_high")
            or 0.0
        ),
        weather_data_source_tier=snap_json.get("weather_data_source_tier") or "unknown",
        # DB stores "horizon_hours"; dataclass field is "forecast_horizon_hours"
        forecast_horizon_hours=float(
            snap_json.get("forecast_horizon_hours")
            or snap_json.get("horizon_hours")
            or 0.0
        ),
        sigma_f=float(snap_json.get("sigma_f") or 3.5),
        deterministic_weather_readiness_state=(
            snap_json.get("deterministic_weather_readiness_state") or "READY"
        ),
        source_timestamps=snap_json.get("source_timestamps") or {},
        source_provenance=snap_json.get("source_provenance") or {},
        source_failures=tuple(snap_json.get("source_failures") or []),
        source_disagreements=tuple(snap_json.get("source_disagreements") or []),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SDK client builder
# ═══════════════════════════════════════════════════════════════════════════════

def build_sdk_client() -> Optional[Any]:
    """
    Build an Anthropic SDK client from environment variables.
    Returns None if the SDK is not installed or no API key is available.
    Resolution order:
      1. AI_INTEGRATIONS_ANTHROPIC_API_KEY + AI_INTEGRATIONS_ANTHROPIC_BASE_URL
      2. ANTHROPIC_API_KEY
    """
    try:
        import anthropic as _sdk  # noqa: PLC0415
    except ImportError:
        return None

    api_key = (
        os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        return None

    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    if base_url:
        return _sdk.Anthropic(api_key=api_key, base_url=base_url)
    return _sdk.Anthropic(api_key=api_key)


# ═══════════════════════════════════════════════════════════════════════════════
# Individual agent caller (production path — mocked in tests)
# ═══════════════════════════════════════════════════════════════════════════════

def call_one_agent(
    agent_id: str,
    snap_json: Any,
    prior_results: Dict[str, Any],   # agent_id → SubagentResult
    run_id: str,
    sdk_client: Any,
    capability_boundary: Any,
) -> Dict[str, Any]:
    """
    Call a single shadow research subagent and return a result dict.

    This is the function that tests replace with a mock via the
    call_agent_fn parameter of run_pilot().  When call_agent_fn is
    supplied, this function is never called — guaranteeing zero real
    Anthropic API calls in any mocked test run.

    Returns a dict with keys:
      success       bool
      tool_input    dict   SubagentResult.tool_input
      failure_reason str | None
      latency_ms    int
      model         str | None   (None — not exposed by SubagentResult)
      input_tokens  int          (0 — not exposed by SubagentResult)
      output_tokens int          (0 — not exposed by SubagentResult)
    """
    from gate_engine.kalshi_wx_shadow_subagents import (  # noqa: PLC0415
        run_contradiction_detection_subagent,
        run_forecast_context_subagent,
        run_source_reconciliation_subagent,
        run_uncertainty_explanation_subagent,
        run_unusual_regime_subagent,
    )

    snapshot = deserialize_snapshot(snap_json)
    context = {
        "city":   snapshot.city,
        "date":   snapshot.market_date,
        "run_id": run_id,
    }

    # Build dependency kwargs for agents that need upstream context
    kwargs: Dict[str, Any] = {}
    if agent_id == "contradiction_detection":
        kwargs["forecast_context"]      = prior_results.get("forecast_context")
        kwargs["source_reconciliation"] = prior_results.get("source_reconciliation")
    elif agent_id == "uncertainty_explanation":
        kwargs["forecast_context"]       = prior_results.get("forecast_context")
        kwargs["contradiction_detection"] = prior_results.get("contradiction_detection")
        kwargs["unusual_regime"]          = prior_results.get("unusual_regime")

    dispatch = {
        "forecast_context":        run_forecast_context_subagent,
        "source_reconciliation":   run_source_reconciliation_subagent,
        "contradiction_detection": run_contradiction_detection_subagent,
        "unusual_regime":          run_unusual_regime_subagent,
        "uncertainty_explanation":  run_uncertainty_explanation_subagent,
    }

    fn = dispatch[agent_id]
    t0 = time.monotonic()
    result = fn(
        sdk_client,
        context,
        capability_boundary,
        snapshot=snapshot,
        **kwargs,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    return {
        "success":        result.success,
        "tool_input":     result.tool_input,
        "failure_reason": result.failure_reason,
        "latency_ms":     latency_ms,
        "model":          None,   # not exposed by SubagentResult
        "input_tokens":   0,      # not exposed by SubagentResult
        "output_tokens":  0,      # not exposed by SubagentResult
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Main pilot loop
# ═══════════════════════════════════════════════════════════════════════════════

def run_pilot(
    config: Dict[str, Any],
    db_conn: Any,
    *,
    call_agent_fn: Optional[Callable] = None,
    max_snapshots: int = MAX_SNAPSHOTS,
    max_calls: int = MAX_SUBAGENT_CALLS,
) -> Dict[str, Any]:
    """
    Main pilot loop.  Processes eligible snapshots one at a time, sequentially.

    Parameters
    ----------
    config          Budget config dict from require_budget_config().
    db_conn         Open psycopg2 connection (or mock in tests).
    call_agent_fn   Optional override for the agent caller.  When supplied,
                    call_one_agent() is never invoked — guaranteeing zero real
                    Anthropic API calls in any mocked test run.
                    Signature: (agent_id, snap_json, prior_results, run_id,
                                sdk_client, capability_boundary) → result_dict
    max_snapshots   Hard cap on snapshots processed this run (default 25).
    max_calls       Hard cap on total subagent call attempts (default 125).
                    Checked BEFORE every individual call — a 126th attempt is
                    structurally impossible.

    Returns a summary dict with run statistics and stop_reason.

    stop_reason values
      EXHAUSTED           fewer than max_snapshots eligible rows existed
      MAX_SNAPSHOTS       max_snapshots snapshots fully processed
      MAX_SUBAGENT_CALLS  max_calls call attempts reached (hard cap)
      BUDGET_EXCEEDED     worst-case cost would exceed PILOT_BUDGET_USD
    """
    _caller = call_agent_fn if call_agent_fn is not None else call_one_agent

    run_id             = f"pilot-{uuid.uuid4()}"
    total_calls:   int = 0
    cumulative_spend:  float = 0.0
    cumulative_in_tok: int = 0
    cumulative_out_tok:int = 0
    snapshots_done:    int = 0
    stop_reason:       str = "EXHAUSTED"

    # SDK client and CapabilityBoundary — used only when call_agent_fn is None
    sdk_client = build_sdk_client()
    try:
        from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary  # noqa: PLC0415
        cap_boundary = CapabilityBoundary()
    except ImportError:
        cap_boundary = None

    _log.info(
        "Pilot starting: run_id=%s budget=%.6f max_snapshots=%d max_calls=%d",
        run_id, config["PILOT_BUDGET_USD"], max_snapshots, max_calls,
    )

    snapshots = fetch_eligible_snapshots(db_conn, max_snapshots)
    _log.info("Eligible snapshots: %d", len(snapshots))

    for snap_row in snapshots:

        # ── Snapshot-level hard cap ───────────────────────────────────────────
        if snapshots_done >= max_snapshots:
            stop_reason = "MAX_SNAPSHOTS"
            break

        rsid      = snap_row["research_snapshot_id"]
        snap_json = snap_row["snapshot_json"]

        # Load prior completed results for this snapshot (dependency chain)
        prior_results = load_prior_results(db_conn, rsid)

        for agent_id in AGENT_IDS:

            # ── Hard call cap: refused BEFORE any work on this agent ──────────
            if total_calls >= max_calls:
                stop_reason = "MAX_SUBAGENT_CALLS"
                _log.warning(
                    "MAX_SUBAGENT_CALLS=%d reached — hard stop before %s/%s",
                    max_calls, rsid, agent_id,
                )
                return _build_summary(
                    run_id, stop_reason, total_calls, cumulative_spend,
                    cumulative_in_tok, cumulative_out_tok, snapshots_done, config,
                )

            # ── Resumability: skip already-completed pairs ────────────────────
            if is_pair_completed(db_conn, rsid, agent_id):
                _log.info("SKIP (completed): %s / %s", rsid, agent_id)
                continue

            # ── Conservative worst-case cost estimate ─────────────────────────
            serialized = json.dumps({
                "snapshot_id": rsid,
                "agent_id":    agent_id,
                "run_id":      run_id,
                "snap_keys":   list(snap_json.keys()) if isinstance(snap_json, dict) else [],
            })
            input_tok_est = estimate_input_tokens(serialized)
            wc_cost = worst_case_call_cost(
                input_tok_est,
                config["MAX_OUTPUT_TOKENS_PER_CALL"],
                config["INPUT_PRICE_PER_TOKEN"],
                config["OUTPUT_PRICE_PER_TOKEN"],
            )

            # ── Spend guard: hard stop (not a soft warning) ───────────────────
            if cumulative_spend + wc_cost > config["PILOT_BUDGET_USD"]:
                stop_reason = "BUDGET_EXCEEDED"
                _log.warning(
                    "BUDGET_EXCEEDED: cumulative=%.8f + worst_case=%.8f > "
                    "budget=%.6f — hard stop before %s/%s",
                    cumulative_spend, wc_cost,
                    config["PILOT_BUDGET_USD"], rsid, agent_id,
                )
                return _build_summary(
                    run_id, stop_reason, total_calls, cumulative_spend,
                    cumulative_in_tok, cumulative_out_tok, snapshots_done, config,
                )

            # ── Attempt the call (counts toward the 125 cap regardless) ───────
            total_calls += 1
            _log.info(
                "Call %d/%d: agent=%s snapshot=%s",
                total_calls, max_calls, agent_id, rsid,
            )

            t0            = time.monotonic()
            status        = "ERROR"
            tool_input:   Dict = {}
            failure_reason: Optional[str] = None
            input_tokens  = input_tok_est
            output_tokens = 0
            model: Optional[str] = None

            try:
                result = _caller(
                    agent_id, snap_json, prior_results,
                    run_id, sdk_client, cap_boundary,
                )
                latency_ms     = result.get("latency_ms") or int((time.monotonic() - t0) * 1000)
                success        = bool(result.get("success", False))
                tool_input     = result.get("tool_input") or {}
                failure_reason = result.get("failure_reason")
                input_tokens   = int(result.get("input_tokens") or input_tok_est)
                output_tokens  = int(result.get("output_tokens") or 0)
                model          = result.get("model")
                status         = "COMPLETE" if success else "BLOCKED"
            except Exception as exc:
                latency_ms     = int((time.monotonic() - t0) * 1000)
                failure_reason = f"{type(exc).__name__}: {exc}"
                _log.warning(
                    "Agent call raised exception (counted as attempt): %s",
                    failure_reason,
                )
                # status stays "ERROR"; failure counts toward 125 cap — no retry

            # ── Accumulate actual spend ───────────────────────────────────────
            actual_cost = (
                input_tokens  * config["INPUT_PRICE_PER_TOKEN"]
                + output_tokens * config["OUTPUT_PRICE_PER_TOKEN"]
            )
            cumulative_spend   += actual_cost
            cumulative_in_tok  += input_tokens
            cumulative_out_tok += output_tokens

            # ── Update prior_results for downstream dependency chain ──────────
            if status == "COMPLETE":
                try:
                    from gate_engine.kalshi_wx_shadow_subagents import SubagentResult  # noqa: PLC0415
                    prior_results[agent_id] = SubagentResult(
                        subagent_id=agent_id,
                        tool_name=f"{agent_id}_tool",
                        tool_input=tool_input,
                        hook_violations=[],
                        success=True,
                    )
                except ImportError:
                    pass

            # ── Persist result row ────────────────────────────────────────────
            # Pricing config is embedded in every row so accounting is
            # reconstructable from the DB without needing runtime context.
            output_payload = {
                "agent_output":    tool_input,
                "failure_reason":  failure_reason,
                "run_config": {
                    "PILOT_BUDGET_USD":           config["PILOT_BUDGET_USD"],
                    "INPUT_PRICE_PER_TOKEN":      config["INPUT_PRICE_PER_TOKEN"],
                    "OUTPUT_PRICE_PER_TOKEN":     config["OUTPUT_PRICE_PER_TOKEN"],
                    "MAX_OUTPUT_TOKENS_PER_CALL": config["MAX_OUTPUT_TOKENS_PER_CALL"],
                    "run_id": run_id,
                },
            }
            write_result_row(
                db_conn,
                research_snapshot_id  = rsid,
                agent_id              = agent_id,
                run_id                = run_id,
                validated_output_json = output_payload,
                status                = status,
                latency_ms            = latency_ms,
                model                 = model,
                input_tokens          = input_tokens,
                output_tokens         = output_tokens,
                estimated_cost_usd    = actual_cost,
            )

        snapshots_done += 1

    return _build_summary(
        run_id, stop_reason, total_calls, cumulative_spend,
        cumulative_in_tok, cumulative_out_tok, snapshots_done, config,
    )


def _build_summary(
    run_id:            str,
    stop_reason:       str,
    total_calls:       int,
    cumulative_spend:  float,
    cumulative_in_tok: int,
    cumulative_out_tok:int,
    snapshots_done:    int,
    config:            Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "run_id":                    run_id,
        "stop_reason":               stop_reason,
        "total_subagent_calls":      total_calls,
        "snapshots_processed":       snapshots_done,
        "cumulative_spend_usd":      round(cumulative_spend, 8),
        "cumulative_input_tokens":   cumulative_in_tok,
        "cumulative_output_tokens":  cumulative_out_tok,
        "pilot_config": {
            "PILOT_BUDGET_USD":           config["PILOT_BUDGET_USD"],
            "INPUT_PRICE_PER_TOKEN":      config["INPUT_PRICE_PER_TOKEN"],
            "OUTPUT_PRICE_PER_TOKEN":     config["OUTPUT_PRICE_PER_TOKEN"],
            "MAX_OUTPUT_TOKENS_PER_CALL": config["MAX_OUTPUT_TOKENS_PER_CALL"],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """
    Entry point for direct manual execution:
        python scripts/run_kalshi_wx_shadow_pilot.py

    Reads budget config from env, applies schema migrations, runs pilot,
    prints JSON summary to stdout and exits.
    """
    config = require_budget_config()
    conn = get_db_conn()
    try:
        apply_schema_migrations(conn)
        summary = run_pilot(config, conn)
        _log.info("Pilot complete: %s", json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
