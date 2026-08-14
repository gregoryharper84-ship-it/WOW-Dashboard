"""
gate_engine/universal_agent/audit_store.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Shared Audit / Cost Structures — Postgres-backed durable state for all
Universal Agent Core lanes.

Design decisions (from Weather shadow pilot lessons):
- DURABLE POSTGRES: all state lives in Postgres, not an in-memory ledger.
  In-memory state fails across gunicorn workers and deploy restarts.
- AVAILABLE/UNAVAILABLE: real token counts and cost are stored ONLY when
  status=AVAILABLE. UNAVAILABLE rows carry NULL for numeric fields — never a
  silent zero. This is the Weather Step 12.5B2 pattern.
- RESUMABILITY: is_work_completed() mirrors is_pair_completed() from the pilot
  runner (Weather Step 12.5B1). A second identical run skips completed work
  without re-executing it.
- compute_budget_guard() is a pure function with configurable per-token pricing
  (not hardcoded model constants), so tests can exercise it without env vars.

Tables created by ensure_tables():
  uac_evidence_packets     Evidence packet snapshots (immutable on insert)
  uac_agent_results        Agent execution results (ON CONFLICT DO UPDATE)
  uac_budget_events        Per-call budget accounting entries
  uac_run_resumability     Work-unit completion records (skip on retry)

This module has NO imports from app.py, gate_engine routing, or scoring logic.
It is pure infrastructure — callable from tests, from B1 agent code, and from
the eventual production orchestrator without any scoring side effects.
"""
from __future__ import annotations

import json
from typing import Any, Optional


# ── Usage status constants ────────────────────────────────────────────────────

class UsageStatus:
    """
    Accounting status for an agent SDK call.

    AVAILABLE   — Real SDK call completed; input_tokens/output_tokens/cost are real.
    UNAVAILABLE — SDK unavailable (flag off, timeout, auth fail); numeric fields NULL.

    Never coerce UNAVAILABLE numeric fields to zero — zero implies a real call
    with zero usage, which is misleading. Always use NULL to signal unavailability.
    """
    AVAILABLE   = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED     = "BLOCKED"     # Call attempted but blocked by capability boundary
    ERROR       = "ERROR"       # Call attempted but raised an exception


# ── Table DDL ────────────────────────────────────────────────────────────────

_DDL_EVIDENCE_PACKETS = """
CREATE TABLE IF NOT EXISTS uac_evidence_packets (
    id                 SERIAL PRIMARY KEY,
    snapshot_id        TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    canonical_event_id TEXT NOT NULL,
    lane               TEXT NOT NULL,
    packet_json        JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uac_evidence_packets_snapshot_unique UNIQUE (snapshot_id)
);
"""

_DDL_AGENT_RESULTS = """
CREATE TABLE IF NOT EXISTS uac_agent_results (
    id                 SERIAL PRIMARY KEY,
    run_id             TEXT NOT NULL,
    snapshot_id        TEXT NOT NULL,
    agent_id           TEXT NOT NULL,
    status             TEXT NOT NULL,
    output_json        JSONB,
    violation_code     TEXT,
    violation_message  TEXT,
    model              TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    estimated_cost_usd NUMERIC(12, 8),
    latency_ms         INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uac_agent_results_unique UNIQUE (run_id, snapshot_id, agent_id)
);
"""

_DDL_BUDGET_EVENTS = """
CREATE TABLE IF NOT EXISTS uac_budget_events (
    id                 SERIAL PRIMARY KEY,
    run_id             TEXT NOT NULL,
    agent_id           TEXT,
    event_type         TEXT NOT NULL,
    usage_status       TEXT NOT NULL,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    estimated_cost_usd NUMERIC(12, 8),
    model              TEXT,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_DDL_RUN_RESUMABILITY = """
CREATE TABLE IF NOT EXISTS uac_run_resumability (
    id            SERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL,
    work_unit_id  TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    completed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uac_run_resumability_unique UNIQUE (run_id, work_unit_id)
);
"""

_ALL_DDL = (
    _DDL_EVIDENCE_PACKETS,
    _DDL_AGENT_RESULTS,
    _DDL_BUDGET_EVENTS,
    _DDL_RUN_RESUMABILITY,
)


# ── Table management ──────────────────────────────────────────────────────────

def ensure_tables(conn: Any) -> None:
    """
    Create all UAC B0 Postgres tables if they do not exist.
    Safe to call multiple times (IF NOT EXISTS).
    Caller provides an open psycopg2 connection.
    Does not close the connection.
    """
    with conn.cursor() as cur:
        for ddl in _ALL_DDL:
            cur.execute(ddl)
    conn.commit()


def get_table_names() -> list[str]:
    """Return the list of table names created by ensure_tables()."""
    return [
        "uac_evidence_packets",
        "uac_agent_results",
        "uac_budget_events",
        "uac_run_resumability",
    ]


# ── Evidence packet persistence ───────────────────────────────────────────────

def record_evidence_packet(
    conn: Any,
    *,
    snapshot_id: str,
    run_id: str,
    canonical_event_id: str,
    lane: str,
    packet_dict: dict[str, Any],
) -> None:
    """
    Insert an evidence packet. ON CONFLICT DO NOTHING — evidence packets are
    immutable once recorded; a duplicate snapshot_id is silently skipped.
    """
    sql = """
        INSERT INTO uac_evidence_packets
            (snapshot_id, run_id, canonical_event_id, lane, packet_json)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_id) DO NOTHING
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            snapshot_id, run_id, canonical_event_id, lane,
            json.dumps(packet_dict),
        ))
    conn.commit()
    # ── Provenance audit (best-effort, never blocks evidence persistence) ─────
    # Call auditSourceProvenance at the UAC ingestion point: when a fact is
    # first attached to a candidate via the evidence packet.
    # checkpoint = "uac_evidence_intake"
    #
    # SAVEPOINT isolation: the provenance UPDATE runs on the caller's shared
    # connection.  If it fails (e.g. schema migration not yet applied), psycopg2
    # puts the connection into InFailedSqlTransaction state, poisoning every
    # subsequent operation for the caller.  A blind conn.rollback() would
    # discard legitimate prior work in the same transaction; ROLLBACK TO
    # SAVEPOINT undoes ONLY the failed provenance operation and restores the
    # connection to a clean, usable state.  ROLLBACK TO SAVEPOINT is permitted
    # by PostgreSQL even when the transaction is in aborted state.
    #
    # Success path: the hook commits internally (see _audit_uac_evidence_provenance);
    # that commit auto-releases the savepoint — no explicit RELEASE needed.
    # Failure path: ROLLBACK TO SAVEPOINT + RELEASE returns the connection to
    # exactly the state it was in after the INSERT commit above.
    _PROV_SP = "_uac_prov_audit"
    try:
        with conn.cursor() as _sp_cur:
            _sp_cur.execute(f"SAVEPOINT {_PROV_SP}")
        _audit_uac_evidence_provenance(conn, snapshot_id, lane, packet_dict)
        # Hook succeeded and committed; savepoint auto-released by that commit.
    except Exception:
        try:
            with conn.cursor() as _sp_cur:
                _sp_cur.execute(f"ROLLBACK TO SAVEPOINT {_PROV_SP}")
                _sp_cur.execute(f"RELEASE SAVEPOINT {_PROV_SP}")
        except Exception:
            pass  # Savepoint may not exist (creation failed or hook already committed)
        # Best-effort — provenance audit failed; evidence packet committed above.


def _audit_uac_evidence_provenance(
    conn: Any,
    snapshot_id: str,
    lane: str,
    packet_dict: dict[str, Any],
) -> None:
    """
    Build a StructuredEvidence from a UAC evidence packet and run
    auditSourceProvenance, then write the audit columns back to the
    uac_evidence_packets row.

    UAC ingestion call site:
        checkpoint = "uac_evidence_intake"
        fact_type  derived from packet_dict["market"] or lane fallback

    Best-effort: caller suppresses all exceptions.
    """
    from gate_engine.source_provenance import (
        auditSourceProvenance,
        build_evidence_from_dict,
    )

    fact_type = (
        packet_dict.get("market")
        or packet_dict.get("fact_type")
        or f"uac_evidence:{lane}"
    )
    evidence_data = {
        "evidence_id":     snapshot_id,
        "snapshot_id":     snapshot_id,
        "fact_type":       fact_type,
        "source_name":     packet_dict.get("source_name") or packet_dict.get("book") or lane,
        "source_type":     packet_dict.get("source_type") or "",
        "fetch_timestamp": packet_dict.get("retrieved_at") or packet_dict.get("source_timestamp"),
        "sport":           packet_dict.get("sport"),
        "market":          packet_dict.get("market"),
        "fact_value":      packet_dict.get("line") or packet_dict.get("odds"),
        "published_at":    packet_dict.get("published_at"),
        "observed_at":     packet_dict.get("observed_at") or packet_dict.get("source_timestamp"),
        "effective_at":    packet_dict.get("effective_at"),
        "materiality":     packet_dict.get("materiality") or "MEDIUM",
    }
    evidence = build_evidence_from_dict(evidence_data)
    result   = auditSourceProvenance(evidence, "uac_evidence_intake")

    # Write audit results back to uac_evidence_packets row
    update_sql = """
        UPDATE uac_evidence_packets SET
            fact_type               = %s,
            fact_value_hash         = %s,
            source_grade            = %s,
            freshness_policy_id     = %s,
            freshness_basis         = %s,
            freshness_status        = %s,
            materiality             = %s,
            conflict_status         = %s,
            reconstruction_status   = %s,
            max_supportable_ceiling = %s
        WHERE snapshot_id = %s
    """
    with conn.cursor() as cur:
        cur.execute(update_sql, (
            evidence.fact_type,
            evidence.fact_value_hash,
            evidence.source_grade,
            evidence.freshness_policy_id,
            evidence.freshness_basis.value if evidence.freshness_basis else None,
            evidence.freshness_status.value,
            evidence.materiality.value,
            evidence.conflict_status.value,
            evidence.reconstruction_status.value,
            evidence.max_supportable_ceiling,
            snapshot_id,
        ))
    conn.commit()


def get_evidence_packet(
    conn: Any,
    snapshot_id: str,
) -> Optional[dict[str, Any]]:
    """
    Retrieve a stored evidence packet by snapshot_id.
    Returns the packet dict, or None if not found.
    """
    sql = "SELECT packet_json FROM uac_evidence_packets WHERE snapshot_id = %s"
    with conn.cursor() as cur:
        cur.execute(sql, (snapshot_id,))
        row = cur.fetchone()
    if row is None:
        return None
    val = row[0]
    return val if isinstance(val, dict) else json.loads(val)


# ── Agent result persistence ──────────────────────────────────────────────────

def record_agent_result(
    conn: Any,
    *,
    run_id: str,
    snapshot_id: str,
    agent_id: str,
    status: str,
    output: Optional[dict[str, Any]] = None,
    violation_code: Optional[str] = None,
    violation_message: Optional[str] = None,
    model: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    estimated_cost_usd: Optional[float] = None,
    latency_ms: Optional[int] = None,
) -> None:
    """
    Persist one agent execution result.

    AVAILABLE/UNAVAILABLE accounting:
      When status=UNAVAILABLE, input_tokens/output_tokens/estimated_cost_usd
      are forced to NULL regardless of what the caller passes — never a silent zero.
      When status=AVAILABLE, numeric fields should be real values.

    ON CONFLICT (run_id, snapshot_id, agent_id): update all fields.
    A retry may overwrite an earlier BLOCKED or ERROR result.
    """
    if status == UsageStatus.UNAVAILABLE:
        input_tokens       = None
        output_tokens      = None
        estimated_cost_usd = None

    sql = """
        INSERT INTO uac_agent_results
            (run_id, snapshot_id, agent_id, status, output_json,
             violation_code, violation_message, model,
             input_tokens, output_tokens, estimated_cost_usd, latency_ms)
        VALUES (%s, %s, %s, %s, %s,  %s, %s, %s,  %s, %s, %s, %s)
        ON CONFLICT (run_id, snapshot_id, agent_id) DO UPDATE SET
            status             = EXCLUDED.status,
            output_json        = EXCLUDED.output_json,
            violation_code     = EXCLUDED.violation_code,
            violation_message  = EXCLUDED.violation_message,
            model              = EXCLUDED.model,
            input_tokens       = EXCLUDED.input_tokens,
            output_tokens      = EXCLUDED.output_tokens,
            estimated_cost_usd = EXCLUDED.estimated_cost_usd,
            latency_ms         = EXCLUDED.latency_ms,
            created_at         = NOW()
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            run_id, snapshot_id, agent_id, status,
            json.dumps(output) if output is not None else None,
            violation_code, violation_message, model,
            input_tokens, output_tokens, estimated_cost_usd, latency_ms,
        ))
    conn.commit()


def get_agent_result(
    conn: Any,
    *,
    run_id: str,
    snapshot_id: str,
    agent_id: str,
) -> Optional[dict[str, Any]]:
    """Return the stored result for (run_id, snapshot_id, agent_id), or None."""
    sql = """
        SELECT status, output_json, violation_code, violation_message,
               model, input_tokens, output_tokens, estimated_cost_usd, latency_ms
        FROM uac_agent_results
        WHERE run_id = %s AND snapshot_id = %s AND agent_id = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (run_id, snapshot_id, agent_id))
        row = cur.fetchone()
    if row is None:
        return None
    output_val = row[1]
    return {
        "status":             row[0],
        "output":             output_val if isinstance(output_val, dict) else (
                                  json.loads(output_val) if output_val else None
                              ),
        "violation_code":     row[2],
        "violation_message":  row[3],
        "model":              row[4],
        "input_tokens":       row[5],
        "output_tokens":      row[6],
        "estimated_cost_usd": float(row[7]) if row[7] is not None else None,
        "latency_ms":         row[8],
    }


# ── Budget event persistence ──────────────────────────────────────────────────

def record_budget_event(
    conn: Any,
    *,
    run_id: str,
    agent_id: Optional[str],
    event_type: str,
    usage_status: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    estimated_cost_usd: Optional[float] = None,
    model: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """
    Record a budget accounting event.
    When usage_status=UNAVAILABLE, numeric fields are forced to NULL.
    """
    if usage_status == UsageStatus.UNAVAILABLE:
        input_tokens       = None
        output_tokens      = None
        estimated_cost_usd = None

    sql = """
        INSERT INTO uac_budget_events
            (run_id, agent_id, event_type, usage_status,
             input_tokens, output_tokens, estimated_cost_usd, model, notes)
        VALUES (%s, %s, %s, %s,  %s, %s, %s,  %s, %s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            run_id, agent_id, event_type, usage_status,
            input_tokens, output_tokens, estimated_cost_usd, model, notes,
        ))
    conn.commit()


def get_run_budget_summary(
    conn: Any,
    run_id: str,
) -> dict[str, Any]:
    """
    Return aggregated budget data for a run, grouped by usage_status.
    NULL values are preserved — never coerced to 0 (UNAVAILABLE calls stay NULL).

    Returns: {usage_status: {"call_count": int, "total_input_tokens": int|None, ...}}
    """
    sql = """
        SELECT
            usage_status,
            COUNT(*)                  AS call_count,
            SUM(input_tokens)         AS total_input_tokens,
            SUM(output_tokens)        AS total_output_tokens,
            SUM(estimated_cost_usd)   AS total_cost_usd
        FROM uac_budget_events
        WHERE run_id = %s
        GROUP BY usage_status
    """
    with conn.cursor() as cur:
        cur.execute(sql, (run_id,))
        rows = cur.fetchall()
    return {
        row[0]: {
            "call_count":          row[1],
            "total_input_tokens":  row[2],
            "total_output_tokens": row[3],
            "total_cost_usd":      float(row[4]) if row[4] is not None else None,
        }
        for row in rows
    }


# ── Resumability ──────────────────────────────────────────────────────────────

def mark_work_completed(
    conn: Any,
    *,
    run_id: str,
    work_unit_id: str,
    outcome: str = "COMPLETED",
) -> None:
    """
    Mark a work unit as completed. Idempotent (ON CONFLICT DO NOTHING).
    work_unit_id convention: "{snapshot_id}:{agent_id}"
    """
    sql = """
        INSERT INTO uac_run_resumability (run_id, work_unit_id, outcome)
        VALUES (%s, %s, %s)
        ON CONFLICT (run_id, work_unit_id) DO NOTHING
    """
    with conn.cursor() as cur:
        cur.execute(sql, (run_id, work_unit_id, outcome))
    conn.commit()


def is_work_completed(
    conn: Any,
    *,
    run_id: str,
    work_unit_id: str,
) -> bool:
    """
    Return True if (run_id, work_unit_id) has already been completed.
    Callers should skip the work unit when this returns True.

    This mirrors is_pair_completed() from the Weather pilot runner
    (Weather Step 12.5B1 resumability pattern).
    """
    sql = """
        SELECT 1 FROM uac_run_resumability
        WHERE run_id = %s AND work_unit_id = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (run_id, work_unit_id))
        row = cur.fetchone()
    return row is not None


# ── Budget guard (pure function) ──────────────────────────────────────────────

def compute_budget_guard(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_per_1k: float,
    output_price_per_1k: float,
    max_cost_usd: float,
) -> dict[str, Any]:
    """
    Pure budget guard function with configurable per-token pricing.

    Pricing is NOT hardcoded to any model's constants — the caller supplies
    input_price_per_1k and output_price_per_1k. This allows tests to exercise
    the guard logic with arbitrary prices without env var dependencies.

    Returns:
      {
        "allowed":            bool,    True if estimated cost <= max_cost_usd
        "estimated_cost_usd": float,   Estimated cost at supplied pricing
        "remaining_usd":      float,   max_cost_usd - estimated_cost_usd
        "input_cost_usd":     float,   Input token cost component
        "output_cost_usd":    float,   Output token cost component
      }
    """
    input_cost  = input_tokens  / 1000.0 * input_price_per_1k
    output_cost = output_tokens / 1000.0 * output_price_per_1k
    total_cost  = input_cost + output_cost
    return {
        "allowed":            total_cost <= max_cost_usd,
        "estimated_cost_usd": total_cost,
        "remaining_usd":      max_cost_usd - total_cost,
        "input_cost_usd":     input_cost,
        "output_cost_usd":    output_cost,
    }
