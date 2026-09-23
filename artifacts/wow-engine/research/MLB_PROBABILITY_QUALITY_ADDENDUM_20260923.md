# MLB probability-quality addendum — grading ledgers, ridge grid, uncertainty semantics

Issue #777 / PR #778

## Ridge/regularization sub-hypothesis

A research-only ridge grid was run at the iteration-20 horizon using the same 2024 training path and the same centered-logit calibration protocol.

Ridge values tested: `0.003`, `0.01`, `0.03`, `0.10`.

On the untouched Sep 16–30, 2024 block, all four candidates produced the same selected-side hit rate (`56.76%`) and nearly identical probability distributions. The existing `0.003` ridge was microscopically best on Brier/log loss:

| Ridge | Brier | Log loss | Mean abs fitted run diff |
|---:|---:|---:|---:|
| 0.003 | **0.243102895** | **0.678786431** | **0.334442** |
| 0.01 | 0.243102948 | 0.678786554 | 0.334353 |
| 0.03 | 0.243103100 | 0.678786905 | 0.334098 |
| 0.10 | 0.243103631 | 0.678788135 | 0.333210 |

Conclusion: **increasing ridge is not an earned repair**. Keep the existing regularization level for the current leading challenger; investigate feature stability/selection separately rather than adding penalty strength without evidence.

## Immutable grading-ledger validation

A Class B observability schema was applied to the `wow-engine-validation` Supabase project and mirrored in PR #778.

`public.wow_mlb_v17_prediction_grade_ledger` has three immutable grade kinds:

- `EARLY_PREGAME_MODEL_GRADE`
- `FINAL_PREGAME_PUBLISHED_GRADE`
- `DOMINANCE_DIAGNOSTIC`

Canonical final identity is the latest `probability_publishable=true`, `final_refresh_status='PASS'` row whose `final_refresh_timestamp < event_start_time`, ordered latest-first per official event.

Backfill verification on currently settled publishable MLB history:

- 5 distinct early grades
- 5 distinct final-pregame published grades
- 5 distinct dominance diagnostics
- zero normalization failures
- zero `can_execute=true` rows
- repeat capture inserts zero duplicate rows
- UPDATE/DELETE mutation is rejected by trigger
- dominance diagnostics have valid probability domains and monotone margin tails (`P(win6+) <= P(win4+) <= P(win2+)`)
- final-published ledger rows are lineup-confirmed at score time for the five verified examples

One event also demonstrates why prediction-time identity must be immutable: its earliest shadow retained an earlier scheduled start while the final published event used the later actual start. The two ledgers preserve both prediction-time contexts rather than rewriting the early record.

The observed 5/5 selected-side result is **not** treated as model-quality evidence; it is only a ledger-mechanics check.

## Dominance semantics

`wow_mlb_v17_dominance_diagnostic_research` records:

- outright win probability from regulation win mass plus explicit extra-inning tie resolution
- regulation win probability
- tie-after-9 probability
- P(win by 2+), P(win by 4+), P(win by 6+)
- expected run differential
- P(loss by 4+)
- blowout asymmetry score

Margin tails are labeled `REGULATION_SCORE_DISTRIBUTION`; extra-inning tie handling is not silently injected into multi-run margin probabilities. The diagnostic is research-only and never rewrites the governed outright P(win).

## Lower-bound semantic separation

PR #778 now has separate typed artifacts for:

- `HISTORICAL_COHORT_RELIABILITY_INTERVAL`
- `EVENT_SPECIFIC_MODEL_UNCERTAINTY`

`LOCAL_DECILE_WILSON_95` belongs to the first type only. Event-specific uncertainty requires actual fitted-model/bootstrap probability replicates (minimum 200 in the current research utility) before an event uncertainty interval can be emitted.

No fake event interval is produced when bootstrap depth is unavailable. This closes the semantic conflation in code, while actual bootstrap generation remains a promotion blocker.

## Supabase advisor follow-up

After ledger DDL, the performance advisor identified three missing covering indexes for new foreign keys. All three were added in validation and in the PR migration:

- event prediction FK index
- score snapshot FK index
- source shadow-grade FK index

RLS remains enabled with no anon/authenticated access; the advisor reports this as informational `rls_enabled_no_policy`, which is intentional for this service-role/internal ledger.

## Current engineering disposition

- Threshold-only repair: **REJECTED**
- Blind train-to-convergence repair: **REJECTED**
- Forced positive shared-regime variance: **REJECTED**
- Higher ridge as repair: **REJECTED**
- Current leading challenger: **iteration-20 run model + centered logit calibration**
- Grade split / dominance observability: **IMPLEMENTED_AND_VERIFIED_IN_VALIDATION**
- Champion replacement: **NOT AUTHORIZED / NOT PERFORMED**
- Issue #777: remains **EXPERIMENT_CREATED / PR_CREATED** pending untouched forward certification and true event-bootstrap uncertainty.
