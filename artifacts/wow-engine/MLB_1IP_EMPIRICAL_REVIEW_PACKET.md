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
6. Supported lines are pinned to the empirically validated grid `11.5, 13.5, 15.5, 17.5, 19.5, 21.5`; unsupported tails may not be silently extrapolated.
7. `mlb_1ip_empirical_promotion.py` is packet construction only: it verifies artifact checksum, immutable lineage, split hash, exact validation metrics, supported lines, distinct reviewer context, approval verdict, and a 64-character review-evidence hash before it can produce a `PROSPECTIVE_CERTIFIED` payload.
8. The promotion payload still hard-sets `probability_publishable=false` and `can_execute=false`; no function in the bundle writes Supabase, deploys Render, creates a production scheduler, or changes V17 activation state.
9. Tests explicitly reject self-review, missing/non-approval review evidence, tampered artifact checksum, line-support mismatch, and checksum/split/Brier/ECE mismatches.
10. Confirm the simpler aggregate empirical model is justified by the disjoint temporal validation and that adding the pitcher-shrunk layer is not warranted by the measured holdout results.

## Temporal shadow evidence

Expanded official-source shadow run used 1,332 training rows from the 2024 sample and 1,323 untouched 2025 validation rows. On that temporal holdout:

- current Gaussian event tree: Brier `0.21218464411186702`; ECE `0.05242418745275859`
- pitcher-shrunk empirical: Brier `0.20956144071149074`; ECE `0.030175502684320784`
- aggregate empirical conditional-total-pitches PMF: Brier `0.20677374121890155`; ECE `0.014757387773260975`

All three passed the current absolute numerical gates on the expanded holdout, but the aggregate empirical PMF was best on both Brier and ECE. This packet therefore advances only the simpler aggregate empirical model.

## Latest machine verification

At commit `860cf876355d2d80461591a4a542e71ccfd8a95f`, GitHub Actions run `33570320155` completed successfully:

- focused MLB 1IP governance/runtime suite: **38 passed**
- full WOW engine regression: **660 passed, 3 skipped, 0 failed**

Warnings were deprecation/future warnings only and did not fail the suite.

## Promotion remains blocked

Machine validation is not reviewer approval. Promotion remains blocked until a distinct reviewer submits `APPROVE_FOR_PROMOTION` with reproducible review evidence. Even after such approval, persistence/deployment remains a separate governed action. `probability_publishable=false` and `can_execute=false` remain invariants.
