---
name: Mandatory acquisition patch architecture
description: How WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0 is wired into the gate_engine pipeline.
---

## Rule
Missing data triggers acquisition — NOT immediate failure. The engine must document every source attempt before assigning DATA_UNOBTAINABLE or any terminal bucket.

## Key modules

**gate_engine/acquisition.py** (new)
- `AcquisitionTracker` — per-row tracker; `mark_missing_at_intake()`, `record_attempt()`, `build_row_report()`
- `SourceStatus` / `ReconstructionStatus` constants
- `build_run_acquisition_report()` — aggregates row reports into Section 29.2 run-level output
- `format_unobtainable_blocker()` — standard DATA_UNOBTAINABLE blocker string

**gate_engine/data_contract.py** additions
- `run_intake(row, enr)` — checks ROW_REQUIRED_FIELDS only; missing enrichment fields → FIELD_MISSING_AT_INTAKE, row NOT terminated
- `run_deferred(row, enr, tracker)` — checks ENRICHMENT_REQUIRED_FIELDS after acquisition; missing → DATA_CONTRACT_FAIL
- `run()` untouched for backward compat (all existing tests still call it directly)

**gate_engine/l5_l10_ledger.py** additions
- Season_log fallback when game_log is None: if season_log has ≥ MIN_GAMES_L5 rows → reconstructed; ≥10 → RECONSTRUCTED_A, else RECONSTRUCTED_B_UNCORROBORATED (caps row at MODEL_QUALIFIED_HOLD)
- New result fields: `l5_source_status`, `l10_source_status`, `reconstruction_method`, `source_rows_used`, `reconstruction_confidence`, `l5_line_used`, `source_attempts`, `approval_cap`

**gate_engine/pipeline.py** changes
- Imports `AcquisitionTracker`, `SourceStatus`, `build_run_acquisition_report` from acquisition
- Uses `data_contract.run_intake()` (not `run()`) in the Module B block; calls `run_deferred()` after all data gates
- l5_l10_ledger source_attempts are forwarded into the tracker for game_log / l5_values / l10_values fields
- market_gate result is tracked for market_no_vig_probability field
- Module exceptions recorded in tracker as FAILED (not just failed_modules list)
- `row["gates"]["acquisition"]` = per-row report; `acquisition_execution_report` added to output dict

## Why
Spec prohibits "preferred source failed → data unavailable" without executing the full ladder. The old data_contract immediately terminated enrichment-missing rows before l5_l10_ledger could even try. Now enrichment fields are deferred; row continues through acquisition, then re-checked.

## Reconstruction cap rule
RECONSTRUCTED_B_UNCORROBORATED → `MODEL_QUALIFIED_HOLD` approval cap + blocker appended. RECONSTRUCTED_A and RECONSTRUCTED_B_CORROBORATED → no automatic cap.
