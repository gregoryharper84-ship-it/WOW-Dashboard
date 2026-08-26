---
name: FMCG gate wiring audit
description: Which v1.1 FMCG gates were actually wired vs. silently skipping, and how the two broken ones were fixed.
---

# FMCG v1.1 Gate Wiring Audit

## Gate wiring status at v1.1 ship

| Gate | Key | Pipeline write site | Status |
|---|---|---|---|
| calibration_health (#15) | `gates["calibration_health"]` | L144-145 first per-row loop | ✅ Wired |
| bidirectional_sides (#16) | `gates["bidirectional_analysis"]` | No module computes this | SKIP correct — no pipeline module exists yet |
| source_timestamp_grading (#17) | `gates["source_grade"]` | L198 `source_grade.run()` | ❌ **Fixed** — see below |
| prob_ledger (#18) | `gates["prob_ledger"]` | L535 first per-row loop | ✅ Wired |
| session_directional_exposure (#19) | `gates["directional_exposure"]` | L604 `directional_exposure.run()` | ✅ Wired |
| pregame_snapshot (#20) | `gates["pp_final_refresh"]` | L1033 (after apply_gatekeeper) | ❌ **Fixed** — see below |
| terminal_label_native (#21) | `row["terminal_label"]` | L922 `classify()` | ✅ Wired |

## Fix 1 — source_timestamp_grading reads wrong fields

`_check_source_timestamp_grading` originally read `sg.get("grade_type")` and `sg.get("timestamp_grade")` — neither exists in the `source_grade.run()` result dict. The actual field is `worst_critical` (e.g. `"A"`, `"B"`, `"N/T"`). Per-source timestamp info is in `source_grades[*].has_timestamp` (filtered by `is_critical`).

Also: the row-level guard set was missing `"N/T"` — it had `{"N", "T", "NO_TIMESTAMP", "TIMESTAMP_MISSING"}` so `"N/T"` string fell through to SKIP.

**Why:** The gate was written against an assumed output schema, not the actual `source_grade.run()` return dict.

**How to apply:** Any time a FMCG gate reads a module's gate result dict, verify the field names against the module's actual `return` statement — not against assumed/documented schema.

## Fix 2 — pp_final_refresh wired after apply_gatekeeper

`_pp_baselines` was fetched at pipeline L1005 and `pp_final_refresh.run()` called at L1033, both AFTER `apply_gatekeeper(row)` at L930. The pregame_snapshot gate in FMCG always SKIPped because `row["gates"]["pp_final_refresh"]` was never set before FMCG read it.

Fix: move the `_pp_baselines` DB fetch block to before the second per-row loop (before L755), then call `pp_final_refresh.enforce_final_refresh(row, _pp_baselines.get(row.get("row_id") or ""))` per-row immediately before `apply_gatekeeper(row)`. The batch `pp_final_refresh.run()` call is kept at its original site for the batch report — it re-uses the already-fetched `_pp_baselines` dict (no second DB trip). `enforce_final_refresh` is idempotent on the same baseline so double-call is safe.

**Why:** Gate insertion order matters; any gate that writes `row["gates"]["X"]` must run before `apply_gatekeeper` or the corresponding FMCG gate will always SKIP.

**How to apply:** For any new FMCG gate, verify its data source runs in the first per-row loop (L~130–618) or before apply_gatekeeper in the second loop (L755–930). If not, move it or add a per-row call at the right insertion point.
