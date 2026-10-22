# NCAAF 44-event prospective result/form input recovery — 2026-10-22

## Evidence accepted (research only)

- ESPN source: previously hash-verified GitHub run [#39852468868](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/39852468868), SHA-256 `976d8ac562a9eaaa8011fdaf4158b06d2e908a05a28a97f66223d8dafa5f3428`.
- CFBD official event evidence: `wow-engine-validation.public.wow_ncaaf_source_snapshots` UUID `8c06ddb8-0fa2-4da9-b3ae-2630f3714bf9`, 2026 season, `/games`, 888 source rows, retrieved `2026-10-22T17:04:50.870976+00:00`, source hash `70ab7532d8e93608976aeb7dcada5a2e285b84143240d1a768f7b41218bc207e`.
- All **44/44** ESPN IDs, numeric home/away team IDs and UTC event start times matched the CFBD source snapshot. The CFBD neutral-site value is explicit and Boolean for every event.
- Read-only historical results: `public.wow_ncaaf_training_games`, settled sources with `event_start_time < result_source_timestamp < target kickoff`; each team has at least 9 eligible prior games. Prior event IDs, counts, wins, point-differential sums, last start times and latest source retrieval timestamps are frozen per event.
- All **44/44** exact nine-feature candidate vectors constructed: `NCAAF_RESULT_FORM_PRIOR_V1` belonging to fitted `NCAAF_RESULT_FORM_LOGIT_V1`. Numerical semantics: all prior games, prior win rates and mean point differential, log1p prior counts, 28-day capped rest intervals, official neutral-site Boolean. Market prices are not used.
- Frozen data: `data/research/ncaaf_result_form_forward_features_20261022.json`. Replay code: `scripts/ncaaf_frozen_result_form_audit.py`. Adversarial tests: `tests/test_ncaaf_frozen_result_form_20261022.py`.
- One-shot independent GitHub replay: `.github/workflows/wow-research-ncaaf-result-form-forward-once.yml` (not scheduled).

## What this **does not** claim

- **No 18-feature NCAAF_LOGISTIC_V1 restoration.** Rich `NCAAF_FEATURES_V1` still needs all 27 evidence slots, including pregame QB, injury, team strength, weather and situational data. Connected validation database has **0** historical training feature rows, **0** NCAAF pregame evidence rows, **0** live event feature snapshots and **0** rich fitted artifact records. Existing source registry has five rows and mainly availability-related evidence families, so the rich feature compiler cannot create the missing values.
- **No certified / publishable model probability.** The validation database has 34 separately named NCAAF result/form research candidate artifacts (all research-screen passing, none active/publishable), plus 53 dynamic-team-state candidates (49 screen passing, none active/publishable). These are **different fitted model families** with different feature contracts. Do not relabel one as the missing 18-feature specialist.
- **No independent timestamp attestation.** CFBD source retrieval times and the independent GitHub/ESPN capture are point-in-time evidence but not cryptographic time witnesses.
- The research feature vector has `model_probability=null`, `calibrated_probability=null`, `calibrated_lower_bound=null`, `probability_publishable=false` and `can_execute=false`. A source-ready candidate feature row is **not** a V17 terminal-approved pick.

## Next downstream work

1. Compare these 44 frozen candidate inputs against the exact currently selected `NCAAF_RESULT_FORM_LOGIT_V1` research artifact's feature schema and frozen fit parameters; keep any shadow inference separate from governed probabilities.
2. Independently evaluate historical calibration/bounds and material contradiction/status evidence for the selected candidate, before any fit promotion or routing.
3. Continue the rich 18-feature source-provider restoration as an independent track. Do not replace absent evidence with football narrative, market-implied probabilities, or reconstructed defaults.
4. Keep prototype results read-only; no database writes, live action or deployment are required to verify this receipt.
