# MLB pitcher-strikeout minimum-three-start candidate

Status: **CLASS_C_CANDIDATE__NOT_PRODUCTION_PROMOTED**

This document records the evidence and exact scope of the candidate that would lower the `PITCHER_STRIKEOUTS` hydration eligibility floor from 10 prior official MLB starts to 3 while preserving the incumbent up-to-L10 history window for established pitchers.

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

The 1-2-start holdout remains blocked: 89 rows, absolute calibration gap and ECE both 0.14545063281676723. This candidate does not expand coverage to 1-2 prior starts.

## Implementation boundary

- Keep `MIN_STARTS = 10` as the maximum history window and workload-route contract.
- Add `PITCHER_STRIKEOUT_MIN_REQUIRED_STARTS = 3` only for the MLB pitcher-strikeout eligibility check.
- A pitcher with 10+ prior starts still receives the incumbent L10 evidence window.
- A pitcher with 3-9 prior starts receives all available official prior starts, with no padding or imputation.
- A pitcher with 0-2 prior starts remains `MLB_RECENT_STARTS_INSUFFICIENT` and maps to the existing typed `MODEL_INPUTS_INSUFFICIENT` terminal path.
- No coefficients, fitted constants, negative-binomial math, opponent factor, calibration formula, terminal reducer, or execution permission changes.

## Promotion requirements

This candidate is Class C. Merge/deployment requires governed review of PR #785 replay evidence, candidate CI/regression evidence, counterexample review, preservation of calibrated lower-bound validity at `n_eff=3`, and explicit approval under the V17 promotion process. Until then production remains unchanged.

`can_execute=false` and dry-run-only are binding.