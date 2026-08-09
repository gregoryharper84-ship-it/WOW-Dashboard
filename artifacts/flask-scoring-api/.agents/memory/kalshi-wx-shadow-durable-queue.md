---
name: Kalshi WX shadow durable queue (Step 12.5 Increment A)
description: Architecture of the durable Postgres-backed shadow capture path that replaced the rejected daemon-thread/orchestrator design.
---

## Rule
The live weather route does exactly ONE thing when the flag is on: construct the frozen WeatherResearchSnapshot and INSERT it into `kalshi_wx_shadow_snapshot_queue` (status='PENDING'). No threads. No Claude calls. No orchestrator.

## Tables

```sql
-- Created in app.py _CM_SCHEMA_DDL (executed by _cm_ensure_schema at startup)
-- AND idempotently by kalshi_wx_shadow_db.ensure_shadow_tables on first insert.

kalshi_wx_shadow_snapshot_queue (
    id SERIAL PRIMARY KEY,
    research_snapshot_id TEXT NOT NULL UNIQUE,
    snapshot_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)

kalshi_wx_shadow_results (
    id SERIAL PRIMARY KEY,
    research_snapshot_id TEXT,
    agent_id TEXT, run_id TEXT,
    validated_output_json JSONB,
    status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- Nothing writes to kalshi_wx_shadow_results yet — that's the pilot runner increment.
```

## Module layout

- `gate_engine/kalshi_wx_shadow_db.py` — SHADOW_SCHEMA_DDL + ensure_shadow_tables + snapshot_to_json_dict + insert_shadow_snapshot + _get_shadow_conn
- `gate_engine/kalshi_wx_shadow_capture.py` — REWRITTEN: no threading, no semaphore, no orchestrator, no SDK client; lazy-imports WeatherResearchSnapshot + insert_shadow_snapshot; single synchronous INSERT on the request thread

## Key constraints

- `insert_shadow_snapshot` opens its own connection, runs DDL if `_TABLES_ENSURED=False`, inserts, commits, closes. No connection state leaks.
- `snapshot_to_json_dict` uses `dataclasses.asdict()` + recursive tuple→list conversion for JSONB compatibility.
- `ON CONFLICT (research_snapshot_id) DO NOTHING` prevents duplicate rows.
- `_TABLES_ENSURED` module-level flag: DDL runs once per worker process. Race condition harmless (IF NOT EXISTS).
- Exception safety: all DB errors caught by outer try/except in `maybe_fire_shadow_snapshot`, logged as SHADOW_CAPTURE_FAILURE, never propagated to caller.

## Patch targets for tests
- `gate_engine.kalshi_wx_shadow_capture._SHADOW_ENABLED` — flag bool
- `gate_engine.kalshi_wx_shadow_db.insert_shadow_snapshot` — the single DB write (patch this to capture snapshot args or inject errors)
- `gate_engine.kalshi_wx_shadow_db._get_shadow_conn` — patch to assert DB not accessed when flag off

## Structural invariants enforced by tests
- `threading` is not imported anywhere in the capture module (AST check)
- "Thread", "Semaphore", "run_shadow_orchestrator", "_build_shadow_sdk_client", "Anthropic(" are absent from capture module source (string check)
- Both shadow tables appear in `_CM_SCHEMA_DDL` in app.py

## What comes next (NOT YET BUILT)
The pilot runner (Increment B): a separate process/script that reads PENDING rows from `kalshi_wx_shadow_snapshot_queue`, deserializes the snapshot, calls `run_shadow_orchestrator` with a real SDK client, writes results to `kalshi_wx_shadow_results`, and marks rows CONSUMED.
