# V28: bounded schema bootstrap must survive commits and failed optional DDL

- date: 2026-11-07
- typed_reason: CI_STARTUP_LOCK_WAIT / OPTIONAL_MIGRATION_TRANSACTION_ABORT
- mechanism: Stage 2 provenance DDL had no lock/statement timeouts; its internal COMMIT invalidates SET LOCAL deadlines. A failed optional PostgreSQL statement aborts the transaction unless its savepoint is rolled back, discarding earlier successful columns and blocking later work.
- detection_point: First hosted CI startup lock-wait review and independent CodeRabbit transaction-semantics challenge, before merging the Class C NBA integration.
- fix: Candidate commits b2fc570 and 2ec39ce add session-scoped SQL timeouts on a dedicated Stage 2 connection plus per-statement SAVEPOINT rollback and cleanup. CM optional migrations separately retain savepoint and transaction-local deadlines, and now throttle repeated failed retries instead of serializing every request.
- prevention_artifact: `gate_engine/tests/test_stage2_schema_lock_timeouts.py`, `gate_engine/tests/test_provenance_statement_savepoint_recovery.py`, `services/tests/test_cm_schema_deadlines.py`.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2504
- evidence_scope: Exact-head hosted CI 8/8 green at 2ec39ce, preexisting 5,664→5,667 gate-engine pass count; new CM throttle change requires fresh exact-head CI and independent SIRT review. No deployment or probability-certification conclusion.
