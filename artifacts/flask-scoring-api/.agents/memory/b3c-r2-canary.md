---
name: B3C-R2 canary run
description: Second live B3C canary run (R2). First run to achieve 6/6 CANARY_CALL_SUCCESS + 6/6 schema_status=ACCEPTED. Covers the pipeline_status classifier bug (wrong attribute name) and the R1-fix validation.
---

# B3C-R2 Canary Run

**Run ID:** `b3c-live-92faf125-45ae-44cf-be77-95dc12f92e29`
**Previous (R1) run ID:** `b3c-live-6769751f-ec22-4e31-8f20-bedcf5c5b021`
**Snapshot:** NYY vs BOS, 2026-08-10, moneyline, preflight PASS (same as R1)

## Outcome

All 6 roles: CANARY_CALL_SUCCESS, schema_status=ACCEPTED, no violation_codes, no aborts.

R1 had SPORT_SPECIALIST + FINAL_REFRESH OUTPUT_REJECTED (terminal_label nested in response).
R2: both now ACCEPTED — prompt hardening (FIX 3) eliminated terminal_label injection.

Total spend: $0.022012 (6 calls, all costs non-null in DB — R1 FIX 2 confirmed).

## pipeline_status=FAILED Bug — FIXED

`_pipeline_status_from_orchestrator` in canary_pipeline.py accessed `orch.bundle.status` but
`EvidenceBundle` dataclass uses `bundle_status` (not `status`). This threw AttributeError,
caught by the outer except block, setting pipeline_status=FAILED despite 6/6 success.

Fix: `orch.bundle.status` → `orch.bundle.bundle_status` in canary_pipeline.py.

**Why:** EvidenceBundle is a frozen dataclass; its field is `bundle_status`. The accessor in
canary_pipeline assumed a `.status` shorthand that doesn't exist.

**How to apply:** Any future code reading bundle state from OrchestratorResult must use
`orch.bundle.bundle_status`, not `orch.bundle.status`.

## Remaining Script Introspection Error (non-critical)

`run_b3c_canary.py` logging code also accesses `adp.status` (`MlbMoneylineAdapterResult`),
which fails similarly. This only affects the script's console reporting, not the pipeline.
Check MlbMoneylineAdapterResult's actual field name before using it in the script.

## Safety Invariants — All Green

- CAN_EXECUTE: never set, not set
- PRODUCTION_AUTHORITY: never set, not set
- TERMINAL_LABEL_ESCAPED: 0 (no OUTPUT_REJECTED in R2)
- EXTERNAL_CALLS_AFTER_ABORT: N/A (no abort)
- null_cost rows (API-backed): 0 (R1 FIX 2 confirmed end-to-end)
- Flag after run: "false" (confirmed in script finally block and fresh process)

## Full Regression After Fix

9 pre-existing failures, 5441 passed, 0 new failures.
