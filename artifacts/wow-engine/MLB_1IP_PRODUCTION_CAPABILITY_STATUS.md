# MLB 1IP Production Capability Status

Date: 2026-09-01
Branch: `chatgpt/v17-1ip-production-capability-20260901`

## Review status

`IMPLEMENTATION_COMPLETE_MACHINE_VERIFIED — INDEPENDENT_REVIEW_REQUIRED`

The first production-capability pass was adversarially reviewed before any production mutation. Three material gaps were found: validation could self-promote an artifact from caller-supplied probability vectors; the final-refresh job did not perform the actual rerun/retry lifecycle; and live 1IP acquisition was implemented as a sidecar adapter rather than the canonical `/score-pick-request` 1IP ingress.

All three implementation findings are now remediated. Production activation remains blocked until a distinct reviewer context independently reviews the current head and returns an approval verdict.

## Implemented

- Official MLB Stats API acquisition for:
  - pitcher identity/probable-starter context,
  - current official lineup when available,
  - deterministic recent-lineup projection when official lineup is TBD,
  - top-batter handedness and season pitches-per-PA profile,
  - prior-start first-inning batters-faced distribution,
  - prior-start first-inning pitches-per-batter distribution.
- Historical first-inning play-by-play dataset builder.
  - Only the first pitcher encountered in each first-inning half is admitted as the starter/opener row.
  - Mid-inning relief-pitcher events are explicitly excluded and audited in the manifest.
- Artifact candidate builder with minimum training requirements.
- Validation lineage binds candidate checksum/version, training dataset/code, scoring code, temporal split, source snapshot hashes, targets, and predicted probabilities.
- Passing empirical validation advances only to `SHADOW`; validation cannot promote or activate an artifact.
- Promotion requires a distinct independent reviewer context, explicit `APPROVE_FOR_PROMOTION`, and a review-evidence hash. Promotion produces only a persistence-ready payload; it does not write Supabase.
- Pregame final-refresh state machine.
- Refresh queue carries line/direction/money-lane information required for deterministic rerun.
- Final-refresh job increments attempts, schedules the next check while lineup is TBD, and performs the actual 1IP specialist rerun when the official lineup confirms.
- Refresh/runtime acquisition errors remain refresh-layer diagnostics and are not relabeled `MODEL_UNAVAILABLE`.
- Canonical `/score-pick-request` 1IP ingress now:
  1. preserves specialist/capability/certified-artifact preflight before acquisition;
  2. automatically hydrates official 1IP evidence when caller evidence is absent and the artifact gate is READY;
  3. runs the mandatory Scout -> Research barrier before specialist scoring;
  4. invokes the controlling 1IP specialist;
  5. attempts deterministic refresh-queue persistence for provisional lineups;
  6. preserves a completed sporting probability if refresh-queue persistence is unavailable.
- Repository SQL remains unapplied.
- Temporary one-shot source-patching script/workflow used to make the large canonical file edit were removed after verification.

## Machine verification

GitHub Actions run `33546120718` on the PR merge ref completed the substantive test steps successfully:

- Focused MLB 1IP tests: **12 passed, 0 failed**.
- Full `artifacts/wow-engine` regression suite: **643 passed, 3 skipped, 0 failed**.
- Only existing deprecation/future warnings were emitted; no test failures occurred.

The tested merge ref bound head `6fd310b5860f5b835fb00575bcab9a62ec93a484` to base `8c745a72ea9724d77440d54108dd2446e3c7b880`.

Subsequent commits remove temporary patch tooling and update this status document only. The final PR head should receive one more CI pass before reviewer approval is accepted.

## Independent-review gate

A fresh reviewer context must inspect the actual current diff rather than relying on this status file. At minimum, independently verify:

1. absent certified 1IP artifact still terminates as genuine `MODEL_UNAVAILABLE` before expensive auto-acquisition;
2. a READY artifact plus absent caller evidence enters the official MLB 1IP hydrator;
3. acquisition/runtime failure is never relabeled model unavailability;
4. mandatory Scout -> Research precedes controlling-specialist scoring;
5. projected/TBD lineup can produce only a non-publishable `MODEL_QUALIFIED_HOLD` and is queued for final refresh;
6. official lineup refresh reruns the specialist and stale starter purges row-locally;
7. validation cannot self-promote and artifact promotion requires distinct implementer/reviewer contexts;
8. training data excludes first-inning relievers after a mid-inning pitching change;
9. `can_execute=false` and `V17_CUTOVER_ALLOWED=false` remain invariant;
10. no production Supabase/Render mutation is contained in this implementation PR.

## Not yet performed

- No production Supabase migration has been applied.
- No trained/certified 1IP artifact has been inserted into `wow_prop_fitted_model_artifacts`.
- No Render cron has been created or deployed.
- No V17 cutover has occurred.
- No production service redeploy has occurred.

## Production activation order

1. Obtain independent review of the final current head.
2. Build immutable historical 1IP dataset and candidate artifact.
3. Run temporal holdout scoring through the exact candidate scorer and bind lineage.
4. Independently review the validation packet.
5. Only if empirical gates and review pass, persist a promoted artifact through the governed write process.
6. Apply the refresh-queue migration.
7. Merge the reviewed intended commit to `main`.
8. Deliberately redeploy Render because `autoDeploy=no`.
9. Confirm deployed SHA parity and stable `/health`.
10. Create/enable the Render final-refresh cron.
11. Run probability-only, market-lane, and failure-path smoke tests.

## Invariants

- `CAN_EXECUTE=false`
- `V17_CUTOVER_ALLOWED=false`
- missing odds may not erase a completed sporting probability
- runtime/deployment failure may not be mislabeled as `MODEL_UNAVAILABLE`
- validation cannot self-promote an artifact
- independent reviewer context is required for promotion
