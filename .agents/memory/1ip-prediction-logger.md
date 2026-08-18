---
name: 1IP Prediction Logger
description: Observational fail-open prediction logger for 1IP props; data collection for blind benchmark; commit c8a6bfe.
---

## What it is
Observational data collection only — no scoring logic changed.
Persist one immutable PredictionRecord per 1IP row that reaches MODEL_QUALIFIED_HOLD.

## Observation point
gate_engine/pipeline.py — after the 1IP TEST_ONLY blanket ceiling block (WOW-PATCH-2026-08-16-AUDIT fix e), inside the per-row loop. Wrapped in unconditional try/except (fail-open). Stamps row['gates']['prediction_logger'] with status dict.

## Idempotency key
`log_dedup_key` = SHA-256[:16] of (pitcher_mlbam_id, game_date, line, direction).
**NOT frozen_at** — must be stable across worker retries.
DB: `ON CONFLICT (log_dedup_key) DO NOTHING`.

## DB tables
- `wow_validation_prediction_log` — frozen predictions
- `wow_validation_outcome_log` — post-game outcomes (FK to prediction table)
Migration in `validation/migration.sql`; auto-applied on first log call via `_ensure_tables()`.

## Fail-open design
- Any exception in `log_1ip_prediction()` is caught and returned as `{"action": "WRITE_FAILURE", ...}`.
- `terminal_label` is NEVER mutated by the logger (verified by T14/T29 tests).
- Scoring availability is never gated on logger health.

## Skip reasons (typed)
NOT_MLB, NOT_1IP_STAT, CEILING_NOT_HOLD, MISSING_PROBABILITY, MISSING_PITCHER_ID,
MISSING_LINE_OR_DIRECTION, MISSING_GAME_DATE, GAME_ALREADY_STARTED, SYNTHETIC_ROW, TEST_ROW_MARKER

## Endpoints
- GET  /wow/validation/1ip/status  — readiness counters, threshold (default 20, env-configurable via VALIDATION_BENCHMARK_THRESHOLD)
- GET  /wow/validation/1ip/export  — paginated frozen predictions (limit 500)
- POST /wow/validation/1ip/outcome — attach post-game outcome (fail-closed; raises OutcomeLogError)

## Benchmark readiness
Threshold = 20 settled eligible games (configurable via VALIDATION_BENCHMARK_THRESHOLD env var).
Does NOT evaluate holdout early; count-only queries.

**Why:** Blind benchmark requires data collected before any model tuning.
Dedup key must exclude frozen_at or multi-worker retries create phantom duplicates.
Fail-open is unconditional — no scoring route can be blocked by logger failure.

## Tests
32 tests in validation/tests/test_prediction_logger.py
Commit: c8a6bfe — 7,542 regression tests pass.
