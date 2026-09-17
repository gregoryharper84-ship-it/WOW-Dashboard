# V17 Universal Prop Lifecycle — Engineering Completion Boundary

This document records the production control-plane contract added for #492/#493.

## Engineering path

`immutable forward evidence -> exact settlement -> artifact-isolated calibration audit -> reviewed route policy -> independent certification release -> exact registry promotion -> runtime adapter/calibrator/hydration audit -> real canonical Action canary -> PRODUCTION_REGISTERED`

The control plane is intentionally stricter than legacy/prospective certification records. A pre-existing `PROSPECTIVE_CERTIFIED` registry row does not automatically satisfy the new universal forward-certification gate.

## Current empirical boundary (2026-09-17)

At implementation time, the connected validation/production ledger contains no exact route with enough **certification-eligible forward evidence** to create a new reviewed release honestly. In particular, current MLB strikeout forward rows are still `PRECALIBRATION_SHRINKAGE`; the newer MLB scalar routes have little/no settled forward cohort; WNBA core routes remain on `WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1`; Fantasy Score routes remain candidates.

Therefore:

- `REVIEWED_ROUTE_POLICIES` is fail-closed until an exact route's metric thresholds are independently reviewed and versioned.
- `REVIEWED_CERTIFICATION_RELEASES` is fail-closed until the same immutable artifact earns `CALIBRATION_CERTIFIED_PASS`, deterministic replay passes, source/counterexample review passes, and an independent certification ID/evidence hash is approved.
- `wow_prop_action_canary_receipts` accepts proof only from a real canonical Action invocation with a persisted prediction, exact artifact/certification identity, valid raw/calibrated/lower-bound package, reconciliation `PASS`, and `can_execute=false`.
- No synthetic canary, legacy certificate, sport-wide provider declaration, Phase-A calibrator, sportsbook probability, recent hit rate, Scout confidence, or external projection may advance a route.

## Runtime endpoints

- `POST /v17/prop-calibration-certification-audit`
- `POST /v17/prop-production-registration-audit`

Both are authenticated, audit-only, and preserve `can_execute=false`.

## Meaning of completion

The #492/#493 engineering machinery is complete when these contracts are deployed and CI-enforced. Empirical graduation remains route-specific and cannot be compressed: routes lacking sufficient future settled evidence or independent policy/certification review remain at their exact typed lifecycle blocker until they earn promotion.
