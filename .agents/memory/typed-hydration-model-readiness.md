---
name: Typed Hydration & Model Readiness patch
description: WOW-PATCH-2026-08-17; new gate_engine/typed_hydration.py module; four data-presence gates; typed lifecycle state machine; RunController hard-abort; reconcile_run invariant.
---

## Rule

Data acquisition failure must never be presented as model judgment.
`lifecycle_state` / `data_status` / `model_status` / `failure_class` are separate typed
dimensions; `terminal_label` remains a native WOW label.
`INCOMPLETE_INPUT`, `DATA_PROVIDER_OUTAGE`, `STALE_DATA` are `DataStatus` values — **not** terminal labels.

**Why:** The patch spec explicitly corrected this architectural boundary. Earlier pipeline
code already used a separate `labels.DataStatus` enum (RETRIEVED/RECONSTRUCTED/etc.) for
different purposes — the new `typed_hydration.DataStatus` is a separate, purpose-specific
enum in that module only; they do not conflict.

## Key design points

- Lifecycle states: BOARD_EXTRACTED → DATA_HYDRATING → CONTRACT_COMPLETE → FOUR_GATES_CLEARED → MODEL_READY → SCORING_ATOMIC → SCORED | BLOCKED (terminal)
- State machine is forward-only; `_validate_transition()` raises ValueError on any invalid or backward move
- Four gates check data PRESENCE and TTL FRESHNESS only — no probability/threshold logic
- TTL check in market gate: `(now - market_checked_at).total_seconds() > market_ttl`; expired TTL blocks even if value present
- SOURCE_CONFLICT overrides missing-field classification in market gate
- DATA_PROVIDER_OUTAGE on `row["data_status"]` or `enrichment["data_status"]` blocks gate 1
- RunController hard-aborts: (1) `contract_complete_count == 0`, (2) all rows provider outage, (3) systemic threshold exceeded
- `reconcile_run()`: five equations + no-duplicate check; any failure → `RUN_INVALID_HYDRATION_RECONCILIATION`
- New PropLabels: `RUN_INVALID_HYDRATION_RECONCILIATION`, `HYDRATION_ABORT`
- Governance patch #26, precedence 105; hash changed from ff2a9ce5... to new value

## How to apply

Call `run_hydration_check(row, enrichment)` before lane selection to get a `HydrationResult`.
Call `run_controller(results)` to get `model_ready_row_ids` — only those rows may be ranked, slipped, or entered into exposure ledgers.
`blocked_row_ids` from the controller must never enter rankings, slips, or exposure.
`reconcile_run(results, scored_row_ids, model_failed_row_ids)` verifies the equation after scoring.
