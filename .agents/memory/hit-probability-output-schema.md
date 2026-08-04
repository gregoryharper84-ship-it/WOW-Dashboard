---
name: hit-probability output schema
description: HitProbResult extended fields; FS calibration contract; formula registry reload; Tennis verified_formula split
---

## Rule
`HitProbResult` now carries 6 additional fields (all default `None`):

| Field | Non-FS models | FS PROVISIONAL models |
|---|---|---|
| `raw_model_probability` | = `hit_probability` (no sep. calibration) | raw Gaussian before buffers |
| `calibrated_probability` | = `hit_probability` | = `hit_probability` |
| `calibrated_lower_bound` | `None` | `calibrated − 0.05` |
| `opposite_raw_probability` | `1 − hit_probability` | `1 − raw_model_probability` |
| `formula_registry_version` | `None` | from `FormulaRegistry.file_version` |
| `formula_registry_hash` | `None` | sha256[:16] of JSON content |

**`hit_probability` is always the calibrated value.** `raw_model_probability + opposite_raw_probability ≈ 1.0`. `calibrated_lower_bound` does NOT sum to anything with its opposite side.

All non-FS results go through `_finalize()` in `hit_probability.py`. Tier 1d builds fields manually.

## Formula registry — verified_formula / verified_settlement split
`FormulaDefinition` now has three verification fields:
- `verified_formula` — scoring components confirmed from authoritative source → gates `validate()` (required to run Gaussian)
- `verified_settlement` — edge cases (retirement, walkover, tiebreak) confirmed against settled results
- `verified` — `verified_formula AND verified_settlement` (full confidence)

Tennis: `verified_formula=true`, `verified_settlement=false`. SETTLEMENT_EDGE_CASES_UNVERIFIED appears in `calibration_note` when `verified_settlement=false`.

NBA, MLB_HITTER: both flags true. WNBA, MLB_PITCHER, NFL: both flags false.

**Why:** a self-consistent formula can still score edge cases wrong. Separating the gate prevents shipping `verified=true` based only on reading a playbook article, before any back-test against settled slips.

## Formula registry hot-reload
`_get_fs_registry()` checks `os.path.getmtime(file_path)` on every call. If mtime changed since last load, the singleton is cleared and reloaded automatically — no worker restart needed. File-hash, file-version, and loaded_at are stored on the `FormulaRegistry` instance and stamped on every FS prediction record.

**Why:** formula edits on disk could otherwise be served by a long-running worker; also required for calibration back-tests to identify which formula version produced each historical prediction.

## compute_batch output
`compute_batch` now returns all 6 new fields in every row dict.

## Sum-to-1 test pattern
Tests that verify MORE+LESS symmetry must use `raw_model_probability`, NOT `hit_probability`. `hit_probability` is calibrated and will be ~`2×cal_buf` below 1.0 for the FS path.
