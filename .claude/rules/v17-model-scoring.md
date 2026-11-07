---
paths:
  - "artifacts/wow-engine/**/*.py"
  - "artifacts/wow-engine/**/*.sql"
---
# Model/scoring-path guardrails

- Every row has a canonical event/stat/line/side identity and exact-once terminal accounting.
- Keep `WOW_PROP_LANE`, `LLP_TEAM_BETTING_ENGINE`, certified weather and `V17_TERMINAL_REDUCER` authority distinct.
- Fit/calibration artifacts must originate from controlling certified specialists; never generate missing values or promote research to numeric probability.
- Keep model input, missing route, unavailable artifact, scorer timeout, malformed output, Action failure and market-data error distinct.
- Calibration/lower-bound, immutable pregame ledger, final refresh, and official settlement remain non-negotiable.
- Changes to fitted model behavior require evidence, replay, counterexamples, out-of-time validation and governed review.
- Test neighboring paths, exact-line alternatives, started/final events, typed failure propagation and existing successful cohorts.

## Additional non-negotiable numeric and settlement rules

- Validate `0 < p < 1`; require `0 <= calibrated_lower_bound <= calibrated_probability` and valid upper bounds/normalization where applicable. Reject malformed outputs with `MODEL_OUTPUT_INVALID`, never silently clamp or repair them.
- Two-sided props require both available directions to be independently resolved; a MORE failure never approves LESS. Honor exact at-least thresholds (`7+`, `8+`) as literal offers unless a certified adapter maps them.
- All required modeled failure regimes affect the **unconditional** sporting probability; never apply a duplicate narrative penalty after the fitted specialist consumed a factor.
- Preserve explicit push/draw/void/DNP outcomes and settlement rules. Official settlement must be derived from frozen identity plus independently sourced official outcome data, not caller-authored hit flags.
- Keep research/score/blocked/purged/duplicate instances and terminal outcome accounting separate; assert `rows_in = rows_completed + rows_held + rows_rejected` when that is the controlling backend formula, with an audited duplicate/provenance mapping and no lost rows.
- Until independent proof of global ceiling enforcement, keep `backend_global_ceiling_enforcement_status=PARTIAL_OR_PENDING` and obey the stricter controlling V17 ceiling.
- For card construction, audit dependence, session exposure, duplicate theses and weakest-leg removal/replacement. Never force requested leg count; shrinking is preferable to filler.
