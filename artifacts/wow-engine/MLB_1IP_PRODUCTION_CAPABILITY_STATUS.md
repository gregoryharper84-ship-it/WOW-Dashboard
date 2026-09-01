# MLB 1IP Production Capability Status

Date: 2026-09-01
Branch: `chatgpt/v17-1ip-production-capability-20260901`

## Implemented

- Official MLB Stats API acquisition for:
  - pitcher identity/probable-starter context,
  - current official lineup when available,
  - deterministic recent-lineup projection when official lineup is TBD,
  - top-batter handedness and season pitches-per-PA profile,
  - prior-start first-inning batters-faced distribution,
  - prior-start first-inning pitches-per-batter distribution.
- Acquisition-to-specialist runtime adapter.
- Historical first-inning play-by-play dataset builder.
- Fitted artifact candidate builder with empirical validation gates.
- Certification refuses promotion unless minimum training/validation gates pass.
- Pregame final-refresh state machine.
- Supabase-backed final-refresh job entrypoint.
- Repository SQL contract for an idempotent 1IP refresh queue.
- Safety tests for artifact gating and refresh behavior.

## Not yet performed

- No production Supabase migration has been applied.
- No trained/certified 1IP artifact has been inserted into `wow_prop_fitted_model_artifacts`.
- No Render cron has been created or deployed.
- No V17 cutover has occurred.
- No production service redeploy has occurred.

A certified artifact cannot be fabricated from the current generic prop artifact registry. The training dataset must first be built from official historical first-inning play-by-play, then validated out-of-sample, then independently reviewed before a promoted artifact row is written.

## Production activation order

1. Run repository tests/independent review.
2. Build immutable historical 1IP dataset and candidate artifact.
3. Run temporal holdout validation and calibration checks.
4. If and only if gates pass, insert promoted artifact through reviewed migration/data-write process.
5. Apply the refresh-queue migration.
6. Merge intended commit to `main`.
7. Deliberately redeploy Render because `autoDeploy=no`.
8. Confirm deployed SHA parity and stable `/health`.
9. Create/enable the Render final-refresh cron.
10. Run probability-only, market-lane, and failure-path smoke tests.

## Invariants

- `CAN_EXECUTE=false`
- `V17_CUTOVER_ALLOWED=false`
- missing odds may not erase a completed sporting probability
- runtime/deployment failure may not be mislabeled as `MODEL_UNAVAILABLE`
- certification may not be self-issued without empirical validation
