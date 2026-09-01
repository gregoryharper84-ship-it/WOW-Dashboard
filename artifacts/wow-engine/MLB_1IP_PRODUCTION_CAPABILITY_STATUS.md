# MLB 1IP Production Capability Status

Date: 2026-09-01
Branch: `chatgpt/v17-1ip-production-capability-20260901`

## Review status

`HOLD_WITH_FINDINGS`

The first production-capability pass was adversarially reviewed before any production mutation. Three material gaps were found: validation could self-promote an artifact from caller-supplied probability vectors; the final-refresh job did not perform the actual rerun and reset its retry counter; and live 1IP acquisition was implemented as a sidecar adapter but is not yet wired into the canonical `/score-pick-request` 1IP ingress.

The first two findings are remediated on this branch. The third remains a merge blocker.

## Implemented

- Official MLB Stats API acquisition for:
  - pitcher identity/probable-starter context,
  - current official lineup when available,
  - deterministic recent-lineup projection when official lineup is TBD,
  - top-batter handedness and season pitches-per-PA profile,
  - prior-start first-inning batters-faced distribution,
  - prior-start first-inning pitches-per-batter distribution.
- Historical first-inning play-by-play dataset builder.
- Artifact candidate builder with minimum training requirements.
- Validation lineage now binds candidate checksum/version, training dataset/code, scoring code, temporal split, source snapshot hashes, targets, and predicted probabilities.
- Passing empirical validation advances only to `SHADOW`; it does not promote or activate the artifact.
- Promotion requires a distinct independent reviewer context, explicit `APPROVE_FOR_PROMOTION`, and a review-evidence hash. Promotion still produces only a persistence-ready payload; it does not write Supabase.
- Pregame final-refresh state machine.
- Refresh queue now carries line/direction/money-lane information required for deterministic rerun.
- Final-refresh job increments attempts, schedules the next check while lineup is TBD, and performs the actual 1IP specialist rerun when the official lineup confirms.
- Refresh/runtime acquisition errors remain refresh-layer diagnostics and are not relabeled `MODEL_UNAVAILABLE`.
- Repository SQL remains unapplied.
- Safety tests were updated to pin no-self-promotion and confirmed-lineup rerun behavior.

## Remaining merge blocker

### Canonical ingress wiring

The production `/score-pick-request` route still reaches its dedicated 1IP branch only after specialist/capability/certified-artifact preflight, and that dedicated branch still requires caller-supplied `RawPropEvidence.lineup_evidence`. The new `mlb_1ip_live_acquisition.py` is not yet invoked there.

Required completion before merge:

1. When the exact MLB 1IP route has a certified artifact and caller evidence is absent, invoke the official 1IP hydrator inside the canonical 1IP branch.
2. Preserve the mandatory Scout -> Research barrier before specialist scoring.
3. Preserve artifact preflight: no certified artifact still returns genuine `MODEL_UNAVAILABLE` before expensive acquisition.
4. Persist/return a deterministic refresh-queue payload for provisional lineups so the scheduler can discover the row.
5. Add regression coverage proving no-evidence 1IP uses automatic hydration when the artifact gate is READY.

Until this is complete, the three pieces exist but do not form a production end-to-end path.

## Machine-verification state

The PR head currently has no attached GitHub status checks/workflow results. Do not claim the full regression suite is green for this branch until tests actually execute.

## Not yet performed

- No production Supabase migration has been applied.
- No trained/certified 1IP artifact has been inserted into `wow_prop_fitted_model_artifacts`.
- No Render cron has been created or deployed.
- No V17 cutover has occurred.
- No production service redeploy has occurred.

## Production activation order

1. Complete canonical ingress wiring.
2. Run focused + full repository machine tests.
3. Perform independent review in a distinct reviewer context.
4. Build immutable historical 1IP dataset and candidate artifact.
5. Run temporal holdout scoring through the exact candidate scorer and bind lineage.
6. If empirical gates pass, independently review the validation packet.
7. Only after review approval, produce/persist a promoted artifact through the governed write process.
8. Apply the refresh-queue migration.
9. Merge the reviewed intended commit to `main`.
10. Deliberately redeploy Render because `autoDeploy=no`.
11. Confirm deployed SHA parity and stable `/health`.
12. Create/enable the Render final-refresh cron.
13. Run probability-only, market-lane, and failure-path smoke tests.

## Invariants

- `CAN_EXECUTE=false`
- `V17_CUTOVER_ALLOWED=false`
- missing odds may not erase a completed sporting probability
- runtime/deployment failure may not be mislabeled as `MODEL_UNAVAILABLE`
- validation cannot self-promote an artifact
- independent reviewer context is required for promotion
