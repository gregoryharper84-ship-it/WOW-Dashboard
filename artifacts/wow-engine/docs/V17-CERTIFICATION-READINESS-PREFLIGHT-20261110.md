# V17 certification readiness preflight — operator contract

Incident: #2020

## Scope
Run `artifacts/wow-engine/scripts/v17_certification_promotion_preflight.sql` **read-only** against the authorized `wow-engine-validation` database only. The SQL reads governed team/event certification receipts, basketball and NCAAF fitted artifacts/calibrators, NFL forward calibration health, and the prop fitted-artifact registry. It returns one row each for NBA, NCAAB, NCAAF, NFL, and WNBA team-event moneyline and MLB, NBA, NFL, WNBA prop inventories.

The resulting inventory is **not** a fitted-model promotion, independent sign-off, market qualification, or `V17_TERMINAL_REDUCER` prediction receipt. In particular, `active_promoted_artifacts` for props matches the canonical registry's `active AND promoted AND lifecycle_state IN ('PROSPECTIVE_CERTIFIED','CHAMPION')` *lookup eligibility*, not `probability_publishable` (which that registry enforces false). A successful lookup still requires exact-line feature/schema, calibrated output, model provenance and runtime qualification before any prediction can be published.

## Read-only acceptance

1. Use an authorized read-only database role or a read-only transaction.
2. Execute the SQL without migration privileges or production writes.
3. Confirm one row per requested team sport (five: NBA, NCAAB, NCAAF, NFL, WNBA) plus one row per requested prop stat family — four requested sports, so a sport with two stored `stat_type` families returns two rows and a total above nine is expected. Every row must have `preflight_authorizes_publication=false` and `can_execute=false`.
4. Preserve typed holds for missing immutable certification, uncalibrated candidates, and missing/nonpassing NFL forward-health records. Never invent a fitted artifact from odds, heuristics, or research.
5. Compare counts with source tables and keep observations labeled by database and timestamp. Do not infer production from validation.

## Release controls

This is a routine **R-class observational** change with no model probability, threshold, routing or release-policy modification. A PR for it still requires the live PR description to include **exactly one** standalone `Incident: #2020` field; the existing independent QA checks enforce that canonical incident binding. Successful CI and a read-only inventory do not authorize publication. Do not bypass required independent App checks, overwrite `main`, or treat a healthy Render endpoint as a persisted scoring receipt.

All outcomes preserve `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, and `can_execute=false`. No live wagers, orders or capital allocation.
