"""
gate_engine/universal_agent/canary/canary_store.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

Isolated Postgres persistence for B3C canary runs.

TABLE: b3c_canary_runs
  - One row per (canary_run_id, role_id) attempt
  - Raw model output text is NEVER persisted — only its SHA-256 hash
  - canonical_output_hash: hash of advisory_findings (only when accepted)
  - Unique constraint on (canary_run_id, role_id)

No app.py import. No Flask routes. No Weather/Kalshi imports.
No cross-reference to CAN_EXECUTE or production routing flags.
"""
from __future__ import annotations

from typing import Any, Optional

can_execute    = False
advisory_only  = True

# ── Table DDL ─────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS b3c_canary_runs (
    id                          BIGSERIAL PRIMARY KEY,
    canary_run_id               TEXT        NOT NULL,
    snapshot_id                 TEXT        NOT NULL,
    role_id                     TEXT        NOT NULL,
    requested_model             TEXT        NOT NULL,
    response_model              TEXT,
    request_timestamp           TIMESTAMPTZ,
    completion_timestamp        TIMESTAMPTZ,
    latency_ms                  INTEGER,
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    cache_read_input_tokens     INTEGER,
    cache_creation_input_tokens INTEGER,
    calculated_cost_usd         REAL,
    cumulative_run_cost_usd     REAL,
    runner_status               TEXT        NOT NULL,
    schema_status               TEXT,
    violation_codes             TEXT,
    error_classification        TEXT,
    raw_output_hash             TEXT,
    canonical_output_hash       TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT b3c_canary_unique_role UNIQUE (canary_run_id, role_id)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS b3c_canary_runs_run_idx
    ON b3c_canary_runs (canary_run_id);
"""


def ensure_canary_tables(conn: Any) -> None:
    """
    Create b3c_canary_runs table and index if they do not exist.
    Idempotent — safe to call on every canary run.

    Parameters
    ----------
    conn : psycopg2 connection
    """
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE_SQL)
        cur.execute(_CREATE_INDEX_SQL)
    conn.commit()


def persist_canary_result(
    conn: Any,
    *,
    canary_run_id:               str,
    snapshot_id:                 str,
    role_id:                     str,
    requested_model:             str,
    response_model:              Optional[str],
    request_timestamp:           Any,    # datetime or None
    completion_timestamp:        Any,    # datetime or None
    latency_ms:                  Optional[int],
    input_tokens:                Optional[int],
    output_tokens:               Optional[int],
    cache_read_input_tokens:     Optional[int],
    cache_creation_input_tokens: Optional[int],
    calculated_cost_usd:         Optional[float],
    cumulative_run_cost_usd:     Optional[float],
    runner_status:               str,
    schema_status:               Optional[str],
    violation_codes:             Optional[list],
    error_classification:        Optional[str],
    raw_output_hash:             Optional[str],
    canonical_output_hash:       Optional[str],
) -> None:
    """
    Insert one B3C canary result row into b3c_canary_runs.
    ON CONFLICT (canary_run_id, role_id) → UPDATE with latest values.

    Raw model output text is NEVER persisted — only the hash fields.

    Parameters
    ----------
    conn : psycopg2 connection (caller owns transaction lifecycle)
    """
    violation_codes_str: Optional[str] = (
        ",".join(violation_codes)
        if violation_codes
        else None
    )

    _UPSERT_SQL = """
        INSERT INTO b3c_canary_runs (
            canary_run_id, snapshot_id, role_id,
            requested_model, response_model,
            request_timestamp, completion_timestamp, latency_ms,
            input_tokens, output_tokens,
            cache_read_input_tokens, cache_creation_input_tokens,
            calculated_cost_usd, cumulative_run_cost_usd,
            runner_status, schema_status,
            violation_codes, error_classification,
            raw_output_hash, canonical_output_hash
        )
        VALUES (
            %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s
        )
        ON CONFLICT (canary_run_id, role_id)
        DO UPDATE SET
            response_model              = EXCLUDED.response_model,
            request_timestamp           = EXCLUDED.request_timestamp,
            completion_timestamp        = EXCLUDED.completion_timestamp,
            latency_ms                  = EXCLUDED.latency_ms,
            input_tokens                = EXCLUDED.input_tokens,
            output_tokens               = EXCLUDED.output_tokens,
            cache_read_input_tokens     = EXCLUDED.cache_read_input_tokens,
            cache_creation_input_tokens = EXCLUDED.cache_creation_input_tokens,
            calculated_cost_usd         = EXCLUDED.calculated_cost_usd,
            cumulative_run_cost_usd     = EXCLUDED.cumulative_run_cost_usd,
            runner_status               = EXCLUDED.runner_status,
            schema_status               = EXCLUDED.schema_status,
            violation_codes             = EXCLUDED.violation_codes,
            error_classification        = EXCLUDED.error_classification,
            raw_output_hash             = EXCLUDED.raw_output_hash,
            canonical_output_hash       = EXCLUDED.canonical_output_hash;
    """
    with conn.cursor() as cur:
        cur.execute(_UPSERT_SQL, (
            canary_run_id,       snapshot_id,     role_id,
            requested_model,     response_model,
            request_timestamp,   completion_timestamp, latency_ms,
            input_tokens,        output_tokens,
            cache_read_input_tokens, cache_creation_input_tokens,
            calculated_cost_usd, cumulative_run_cost_usd,
            runner_status,       schema_status,
            violation_codes_str, error_classification,
            raw_output_hash,     canonical_output_hash,
        ))
    conn.commit()
