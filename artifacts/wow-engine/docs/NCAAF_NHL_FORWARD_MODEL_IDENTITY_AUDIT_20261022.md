# Research NCAAF/NHL source-to-model identity audit — October 22, 2026

## Evidence received

Original off-host evidence (user Library): `WOW_NCAAF_NHL_VERIFIED_FORWARD_SOURCE_EVIDENCE_2026-10-22.zip`.
GitHub capture run: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/39852468868.
This branch includes the full 56-row identity fixture and deterministic standalone audit report, but not the entire raw JSON response ZIP (preserved in Library and CI artifact).

- NCAAF: 44 ESPN pregame events, 44 raw event/team ID matches, SHA256 `976d8ac562a9eaaa8011fdaf4158b06d2e908a05a28a97f66223d8dafa5f3428`.
- NHL: 12 ESPN pregame events, 12 raw event/team ID matches, SHA256 `224e2f7f1c2592eedadb236322bf5f1661c408f4554cb8facd13aa8df2e28199`.
- Archive-to-frozen-identity audit: `SOURCE_VERIFIED_MODEL_INPUTS_NOT_CERTIFIED`; 56/56 raw-to-fixture matches; local regression `16 passed`.

## Exact repository contracts

- NCAAF fitted candidate: `ncaaf_trainer.FEATURES` (18 derived numerical inputs); `ncaaf_feature_transform.model_features_from_snapshot` computes these from the raw, all-or-nothing snapshot; `ncaaf_fitted_provider` requires prospectively certified artifact. `wow_ncaaf_latest_event_features` resolves exact official event ID, feature schema version, home/away identity and pregame as-of time. **No source-only inference or pseudo-feature defaults.**
- NHL current-event identity: `v17.nhl_event_identity.resolve_nhl_current_event_identity` resolves canonical NHL-owned event ID using official NHL club schedules and exact team/start match; ESPN ID remains an alias. `nhl_candidate_pipeline` has 10-feature historical `NHL_REGULAR_SEASON_FEATURES_V1` research candidate; it is not a certified live feature adapter.
- `v17.team_event_feature_contract_registry` marks NCAAF `DEVELOPMENT_ONLY` and NHL feature contract `UNDECLARED`.

## Read-only validation project check

At 2026-10-22, the connected Supabase **wow-engine-validation** database had:
- 0 records in `wow_ncaaf_event_feature_snapshots` for event dates October 24–25.
- 0 NCAAF fitted artifact rows; 0 active publishable NCAAF artifacts.
- 0 publishable NCAAF/NHL specialist certification rows.

This is a **validation database**, not evidence that production necessarily matches. No database writes or schema changes performed.

## Implementation / operation

- `scripts/forward_model_identity_audit.py` verifies the retained ZIP's original raw hashes, event IDs, timestamps, team IDs, and fixture equality; optionally validates supplied NCAAF snapshot JSON (not supplied). It does not call a fitted model.
- `scripts/nhl_forward_official_crosswalk.py` calls the existing first-party NHL resolver and captures typed event-level outcomes, regular-season checks and official IDs. No score or market access.
- `tests/test_forward_model_identity_audit.py` and `tests/test_nhl_forward_official_crosswalk.py` cover positive/negative paths.
- `.github/workflows/wow-research-ncaaf-nhl-feature-identity-once.yml` runs the offline tests and live official NHL identity reconciliation once on this PR's specific workflow-file synchronization, archiving report to GitHub Actions.

## Remaining model blockers

1. Materialize and independently verify exact pregame NCAAF feature snapshots (46? No: **44** captured event IDs), not just source schedules.
2. Confirm NCAAF fitted champion artifact, bound and calibrator availability in target runtime; validation database has none.
3. NHL: resolve first-party canonical game IDs; assemble genuine pregame candidate/goalie/status features; replay and independently certify fitted model/calibration/bounds.
4. Keep `MODEL_UNAVAILABLE` (missing certified capability) separate from `MODEL_INPUTS_INSUFFICIENT` (missing exact features); no public probability until V17 terminal reducer validates the full package.

`can_execute=false`. `probability_publishable=false`. No wagers, deployments, production API modifications or governance changes.
