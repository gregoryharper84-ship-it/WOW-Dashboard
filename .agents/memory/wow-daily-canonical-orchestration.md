---
name: WOW Daily Canonical Orchestration
description: >
  Task #277 implementation — canonical /wow/daily/run route, orchestrator module,
  immutable manifest, five handoff fixes, exact reconciliation.
---

# WOW Daily Canonical Orchestration — Task #277

**Commit:** 6a63d9b  **Merged:** 2026-08-19

## What was built

### New modules
- `gate_engine/daily_orchestrator.py` — canonical orchestration entry point
- `storage/daily_manifest.py` — immutable run manifest (wow_daily_runs + wow_daily_run_rows)
- `gate_engine/tests/test_daily_orchestrator.py` — 47 regression tests

### Modified
- `jobs/wow_daily_scan.py` — `_props_by_sport`, `_persist_results`, `limit_per_sport=None`, source union (always both)
- `app.py` — POST /wow/daily/run, GET /wow/daily/manifest/<run_id>, GET /wow/daily/runs
- `gpt-action-schema-gate-engine.yaml` — runWowDailyCanonical / getWowDailyManifest / listWowDailyRuns

## Five verified handoff fixes

1. **Source union** — `_union_props_for_sport` calls BOTH primary (Odds API) AND backup (TheRundown), never replaces. `run_scan` source-fetch block also unioned.
2. **No pre-score truncation** — `limit_per_sport=None` disables the slice; orchestrator always passes None.
3. **Fail-closed side resolution** — `resolve_participant_side(row)` returns `SIDE_UNKNOWN` when no marker found (never defaults to home). Existing `_is_home_side` unchanged to avoid pipeline regression.
4. **WNBA specialist readiness** — `wnba_ml_specialist_ready(enr)` requires BOTH members of at least one pair: (home_win_pct, away_win_pct) | (home_power, away_power) | (home_elo, away_elo). Partial hydration → NOT_READY.
5. **Tennis specialist readiness** — `tennis_ml_specialist_ready(enr)` requires a complete input pair: (surface_adjusted_form, surface) | (home_elo, away_elo) | (hold_rate, break_rate) | (h2h_win_rate, surface).

Bonus: **Soccer 1X2 normalisation** — `normalise_soccer_outcome` maps 1/X/2 and word forms → canonical HOME/DRAW/AWAY server-side before evaluation.

## Architecture

```
POST /wow/daily/run
  └─ run_daily_orchestration()
       ├─ _union_props_for_sport(sport)     # calls both sources, dedup
       ├─ resolve_participant_side(row)     # fail-closed
       ├─ wnba_ml_specialist_ready / tennis_ml_specialist_ready
       ├─ normalise_soccer_props()
       ├─ _canonical_selection_id()        # stable SHA-256 hash
       ├─ run_scan(_props_by_sport=..., _persist_results=False, limit_per_sport=None)
       ├─ _build_reconciliation(discovered_ids, scan_result)
       │    # reconciled = True iff discovered == sum(all terminal buckets)
       ├─ storage/daily_manifest.finalize_run()
       └─ compact GPT output (no _buckets key)
```

## Key invariants
- `can_execute = False` (unconditional)
- Probability formulas, calibration, thresholds, labels — unchanged
- Provenance cap (DEGRADED / RECONCILIATION_WARNING) preserved

## Remaining gaps (not in scope of #277)
- `_is_home_side` in sport_model.py still defaults to home — follow-up task proposed
- Manifest tables not yet read by /scan-results/summary — follow-up task proposed
- End-to-end live session test against real Odds API — follow-up task proposed
