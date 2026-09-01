# MLB 1IP empirical candidate — independent review packet

Review target branch: `chatgpt/v17-1ip-artifact-research-20260901`
Base branch: `chatgpt/v17-1ip-production-capability-20260901`

## Required reviewer verdict

A distinct reviewer context must return one of:

- `APPROVE_FOR_PROMOTION`
- `HOLD_WITH_FINDINGS`
- `REJECT`

The implementer context must not approve its own work.

## What to verify

1. Historical labels use official MLB Stats API first-inning play-by-play and preserve the first pitcher encountered in each half-inning while excluding relief-pitcher events.
2. The temporal split is clean: deterministic 2024 training versus untouched 2025 validation.
3. `MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1` is a compact empirical BF-conditional total-pitches PMF and contains no caller-controlled probability inputs.
4. Exact MORE/LESS/push probability mass sums to one and is deterministic for a fixed artifact and line.
5. The artifact remains `probability_publishable=false`, `can_execute=false`, inactive and unpromoted before independent approval.
6. Supported lines are limited to the empirically validated line range represented by the candidate packet; unsupported tails must not be silently extrapolated.
7. No Supabase write, active artifact promotion, Render deployment, or V17 cutover is included in this research branch.
8. Confirm the simpler aggregate empirical model is justified by the disjoint temporal validation and that adding the pitcher-shrunk layer is not warranted by the measured holdout results.

## Shadow evidence already observed

On the 2025 temporal holdout:

- current Gaussian event tree: Brier 0.2121846441; ECE 0.0524241875
- pitcher-shrunk empirical: Brier 0.2095614407; ECE 0.0301755027
- aggregate empirical conditional-total-pitches PMF: Brier 0.2067737412; ECE 0.0147573878

All three passed the current numerical gates on the expanded 1,323-row holdout, but the aggregate empirical PMF was best on both Brier and ECE. This packet therefore advances only the simpler aggregate empirical model for formal candidate construction.

## Promotion remains blocked

Even if machine tests and candidate validation are green, promotion remains blocked until a distinct reviewer submits `APPROVE_FOR_PROMOTION` with reproducible review evidence. `can_execute=false` remains invariant.
