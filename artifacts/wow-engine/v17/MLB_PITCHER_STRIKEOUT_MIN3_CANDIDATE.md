# MLB pitcher-strikeout minimum-three-start candidate

Status: **CLASS_C_APPROVED_FOR_PROMOTION__PENDING_MERGE_DEPLOY_VERIFY**

This document records the evidence and exact scope of the governed change that lowers the `PITCHER_STRIKEOUTS` hydration eligibility floor from 10 prior official MLB starts to 3 while preserving the incumbent up-to-L10 history window for established pitchers.

## Evidence

Chronological held-out replay is implemented in PR #785 using the certified `MLB_PITCHER_SO_FAILURE_PATH_NB_V1` artifact and its training baseline. The dedicated 3-9-start gate produced:

- rows: 199
- model mean NLL: 2.23651273465936
- baseline mean NLL: 2.279708171608196
- model mean Brier: 0.24127363322902812
- baseline mean Brier: 0.2596417225863381
- mean predicted More probability: 0.5417769918737902
- observed More rate: 0.542713567839196
- absolute calibration gap: 0.0009365759654057504
- 10-bin ECE: 0.01998974753378257
- result: `MIN3_CANDIDATE_EARNED_GOVERNED_REVIEW`

The 1-2-start holdout remains blocked: 89 rows, absolute calibration gap and ECE both 0.14545063281676723. This promotion does not expand coverage to 1-2 prior starts.

The boundary was also reviewed at finer resolution: the 3-5-start cohort independently beat the training baseline on NLL and Brier and showed a small aggregate calibration gap, while the 6-9-start cohort independently passed. This avoids relying on a pooled 3-9 average that could mask weakness at the exact new eligibility boundary.

## Implementation boundary

- Keep `MIN_STARTS = 10` as the maximum history window and workload-route contract.
- Add `PITCHER_STRIKEOUT_MIN_REQUIRED_STARTS = 3` only for the MLB pitcher-strikeout eligibility check.
- A pitcher with 10+ prior starts still receives the incumbent L10 evidence window.
- A pitcher with 3-9 prior starts receives all available official prior starts, with no padding or imputation.
- A pitcher with 0-2 prior starts remains `MLB_RECENT_STARTS_INSUFFICIENT` and maps to the existing typed `MODEL_INPUTS_INSUFFICIENT` terminal path.
- PITCHING_OUTS, STRIKES_THROWN, and BALLS_THROWN remain on the 10-start workload-history requirement; an adjacent-route regression locks this boundary.
- No coefficients, fitted constants, negative-binomial math, opponent factor, calibration formula, terminal reducer, or execution permission changes.

## Governed promotion authorization

The Class C replay, counterexample review, candidate implementation, lower-bound validation at `n_eff=3`, adjacent-route regression, and full protected CI were reviewed. Explicit human/WOW authorization to proceed with governed promotion was received on 2026-09-23.

That authorization permits merge and exact-SHA deployment only after the refreshed branch passes the protected `main` checks against current ancestry. It does not waive any V17 invariant and does not authorize live trading or market execution.

## Post-promotion verification and monitoring

- Verify the exact merged SHA is live in Render before calling the change promoted.
- Re-run the governed backend/runtime/Action contract checks on merged `main`.
- Confirm `can_execute=false` and dry-run-only remain true.
- Verify 3-start strikeout hydration succeeds without padding and 0-2 starts retain the typed failure.
- Continue monitoring 3-5 and 6-9 cohorts separately for calibration drift and shortened-outing frequency; do not expand to 1-2 without a new governed Class C evidence package.

`can_execute=false` and `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` are binding.