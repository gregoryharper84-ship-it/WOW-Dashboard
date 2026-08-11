---
name: B4 hardening #193/#194/#195
description: Pipeline state separation, settlement worker reliability, full-pipeline integration fixtures, and place_bet/settlement governance treatment.
---

## What was built

### #193 — Pipeline State Separation (pipeline_state.py)
New module `gate_engine/universal_agent/pipeline_state.py`.

Three categories that used to share one code path:
- `FailureKind.TECHNICAL` — DB/network error; upstream adapter result **preserved** in `ScopedContractFailure.preserved_upstream_result`; row locked at ADVISORY ceiling
- `FailureKind.CONTRACT` — DATA_CONTRACT_FAIL/schema error; fail-closed at every level including ADVISORY
- `FailureKind.LEGITIMATE_OUTCOME` — NO_PLAY/REJECT/WATCH; not an error; upgrade guard unconditionally allows

Row isolation is a **hard error**: `RowPipelineState.record_failure()` raises `ValueError` when `failure.row_id != self.row_id`.

`UpgradeCeiling.BLOCKED_FOR_FAILURES` frozenset covers VERIFIED/MONEY/FINAL_APPROVED/EDGE/etc. — always blocked for TECHNICAL or CONTRACT regardless of upstream preservation.

`PipelineStateGuard.can_upgrade()` priority order:
1. None failure → allowed
2. LEGITIMATE_OUTCOME → allowed
3. reconstruction_attempted=True → blocked above ADVISORY
4. target in BLOCKED_FOR_FAILURES → denied
5. TECHNICAL + non-blocked → allowed, upstream echoed in result
6. CONTRACT → fail-closed

### #194 — Settlement Worker Reliability (settlement_worker.py)
Targeted edits only:
- Added `_BACKOFF_BASE_SEC` (default 5s) and `_BACKOFF_MAX_SEC` (default 120s) constants
- Added `consecutive_errors` and `last_heartbeat` to `_WORKER_STATS`
- `_settlement_worker_loop` now stamps `last_heartbeat` at START of every iteration (even before tick runs), uses exponential backoff `min(BASE * 2^n, MAX)` on error, resets counter on success

**Idempotency**: already handled structurally — the `AND settlement_status = 'OPEN'` guard in the UPDATE statement makes re-grading a no-op.

### place_bet/settlement governance treatment (output_contract.py)
Added to `FORBIDDEN_GOVERNANCE_KEYS`: `place_bet`, `bet`, `wager`, `settlement`, `settle`, `settle_result`, `market_order`, `order_placement`.
Applied at any nesting depth via `_scan_forbidden_keys()`.

### #195 — Full-Pipeline Integration Fixtures
Three new test files:
- `gate_engine/tests/test_pipeline_state.py` — 65 tests
- `gate_engine/tests/test_settlement_reliability.py` — 44 tests  
- `gate_engine/tests/test_b4_full_pipeline_integration.py` — 60 tests

**Why:** The no-production-coupling test must use `inspect.getsource()` (AST inspection), not `sys.modules` — the session accumulates imports from other test files so sys.modules check produces false positives.

## Test count
5,764 passed / 17 skipped / 0 failed (was 5,607 before this session).
