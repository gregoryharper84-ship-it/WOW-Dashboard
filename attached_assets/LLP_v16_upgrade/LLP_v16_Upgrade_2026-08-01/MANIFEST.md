# LLP v16 Upgrade Pack — Manifest

## Framework

```text
WOW v16 Clean Core
Build date: 2026-08-01
Status: READY_FOR_legacy platform_INTEGRATION_AND_CUSTOM_GPT_UPLOAD
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

## Files

1. `WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH.md`
   - Critical governing patch and mandatory call order.

2. `wow-llp-moneyline-probability-expert-SKILL.md`
   - Existing LLP moneyline expert with 2026-08-01 integration appended.

3. `wow-llp-slate-integrity-expert-SKILL.md`
   - Official event identity, date, year, status, timezone, and duplicate-event gate.

4. `wow-llp-market-normalization-expert-SKILL.md`
   - Exact two-way/three-way odds conversion, hold, no-vig, and normalization.

5. `wow-llp-dynamic-calibration-expert-SKILL.md`
   - Candidate-specific calibration and uncertainty bounds.

6. `wow-llp-failure-path-expert-SKILL.md`
   - Exact-market, regime-based unconditional probability model.

7. `wow-llp-final-refresh-governor-SKILL.md`
   - Mandatory last-second event, lineup, price, and status recheck.

8. `LLP-ACTIVATION-PROMPT-2026-08-01.md`
   - Ready-to-paste activation prompt.

9. `LLP-REGRESSION-TESTS-2026-08-01.md`
   - 24 regression tests based on observed Linemaker Lite/Pro failure modes.

## Recommended legacy platform Hooks

```text
pipeline stage 0: full_slate_discovery
pipeline stage 1: slate_integrity_lock
pipeline stage 2: exact_market_lock
pipeline stage 3: participant_status_lock
pipeline stage 4: probability_model
pipeline stage 5: market_normalization
pipeline stage 6: dynamic_calibration
pipeline stage 7: failure_path_model
pipeline stage 8: leaderboard_split
pipeline stage 9: final_refresh_governor
pipeline stage 10: output_reconciliation
```

## Required Runtime Audit Fields

```text
patch_id
governance_hash
manifest_hash
skills_required
skills_invoked
slate_rows_in
slate_rows_removed
market_normalization_pass
calibration_pass
failure_path_pass
final_refresh_pass
row_reconciliation
lowest_ceiling
can_execute=false
```
