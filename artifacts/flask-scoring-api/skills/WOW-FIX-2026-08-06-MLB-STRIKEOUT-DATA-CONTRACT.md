# WOW Fix — MLB Pitcher Strikeout DATA_CONTRACT_FAIL (2026-08-06)

## Problem

Every MLB pitcher-strikeout submission through `/analyze-and-score` returned
`DATA_CONTRACT_FAIL` regardless of whether real data was available.  Four
compounding bugs combined to produce the failure.

## Root Causes

### Bug 1 — `_MLB_STAT_FIELDS` missing "K"
`gate_engine/auto_game_log.py`: `_MLB_STAT_FIELDS` had no entry for `"K"`.
`normalizer.py` maps all pitcher-strikeout aliases (`"pitcher strikeouts"`,
`"strikeouts"`, `"k"`) → `stat_key = "K"`.  `_fetch_mlb` called
`_MLB_STAT_FIELDS.get("K")` → `None`, then immediately raised
`GameLogUnavailable` before making any HTTP request.

### Bug 2 — `_PROP_TYPE_TO_MARKET_SUFFIX` missing "k" / "so"
`gate_engine/auto_enrichment.py`: the market-suffix dict was written for
long-form prop_type strings (`"pitcher strikeouts"`).  `app.py`
`_norm_to_pipeline_row` sets `prop_type = stat_key` = the short key `"K"`.
`_PROP_TYPE_TO_MARKET_SUFFIX.get("k")` returned `None` → no Odds API lookup
→ `book_or_platform`, `odds_or_payout`, `market_no_vig_probability` stayed
`None`.

### Bug 3 — `build_auto_enrichment` populated only 7 of 19 required fields
`data_contract.ENRICHMENT_REQUIRED_FIELDS` requires 19 fields.
`build_auto_enrichment` only filled 7 (sportsbook_line, best_available,
consensus_line, status_payload, game_log, data_sources, data_gaps).  The
remaining 12 — including `l5_values`, `l10_values`, `l10_median`, `l10_mean`,
`l5_line_used`, `game_date`, `opponent`, `book_or_platform`, `odds_or_payout`,
`market_no_vig_probability`, and six pipeline-output sentinel fields — stayed
`None` → `run_intake` found them absent → `DATA_CONTRACT_FAIL`.

### Bug 4 — `l5_l10_ledger` results never written back to `enr`
`gate_engine/pipeline.py`: `l5_l10_ledger.run()` writes its output to
`row["gates"]["l5_l10_ledger"]`.  `data_contract.run_deferred()` reads from
the caller-supplied `enr` dict.  The pipeline never copied `l5_games`,
`l10_games`, `l10_avg`, `l10_median`, `l5_line_used` from the gate result
back into `enr`, so even when the ledger succeeded the deferred check still
saw those fields as `None`.

## Fix

### auto_game_log.py
- Added `"K": "strikeOuts"` to `_MLB_STAT_FIELDS` (module level).
- Added `"K"` and `"SO"` to `pitcher_keys` inside `_fetch_mlb` so the
  correct MLB Stats API stat group (`"pitching"`) is requested.
- Changed `_fetch_mlb` return from `(values, source)` to
  `(values, source, metadata)` where `metadata = {"game_date": ..., "opponent": ...}`
  is extracted from the most-recent split (used by Fix 3 below).
- Updated `_cache_set` to accept and persist `meta`.
- Updated `fetch_game_log` to capture the 3-tuple and expose metadata keys
  in its result dict (including on cache hits).

### auto_enrichment.py
- Added `"k"`, `"so"`, `"strikeouts"` aliases to `_PROP_TYPE_TO_MARKET_SUFFIX`.
- Added `import datetime`, `import statistics`, two helper functions
  (`_american_odds_to_implied`, `_compute_no_vig_prob`).
- `build_auto_enrichment` per-row loop now populates all 19 contract fields:
  - `book_or_platform`, `odds_or_payout` — from Odds API matching entry
  - `market_no_vig_probability` — two-sided no-vig; sentinel `"SOURCE_CONFLICT"`
    / `"MARKET_UNAVAILABLE"` when one or both prices absent
  - `opponent`, `game_date` — from Odds API event, then overridden by MLB
    split metadata (more precise name)
  - `game_date` fallback — from `row["game_time"]` / `row["slate_date"]`
  - `l5_values`, `l10_values`, `l10_median`, `l10_mean`, `l5_line_used` —
    computed from `fetch_game_log` result immediately after the fetch
  - `status_timestamp`, `role_timestamp` — `_fetch_ts` (utcnow at function
    entry) when sport is in `SPORT_ESPN`; absent otherwise → honest gap
  - `data_timestamp` — always set to `_fetch_ts`
  - `provisional_label`, `validation_status` — sentinel `"PENDING_GATE_EVALUATION"`
  - `payout_context`, `failure_path_matrix`, `model_probability_ledger` —
    sentinel `"NOT_COMPUTED_AT_AUTO_ENRICHMENT"`
  - `directional_exposure_tags` — `[]` (empty list passes `_is_present()`)

### pipeline.py
- After `l5_l10_ledger.run()` succeeds: copies `l5_games` → `enr["l5_values"]`,
  `l10_games` → `enr["l10_values"]`, `l10_avg` → `enr["l10_mean"]`,
  `l10_median`, `l5_line_used` back into `enr` when the ledger `passed=True`
  and `enr[field]` is currently `None` (caller values always win).

### test_auto_game_log.py
- Updated 3 direct `_fetch_mlb` call sites to unpack the new 3-tuple
  (`values, source, _meta` / `values, _, _meta`).

## Test result
2623 passed, 6 skipped — all pre-existing tests pass, no regressions.

## Related bugs NOT fixed in this patch (same class)

The same `prop_type = stat_key` short-key pattern affects all MLB hitter
stats on the `analyze_and_score` path:

| stat_key | _MLB_STAT_FIELDS | market suffix |
|----------|-----------------|---------------|
| `HR`     | ❌ MISSING      | ❌ "hr" missing |
| `SB`     | ❌ MISSING      | ❌ no market    |
| `IP`     | ❌ MISSING      | ❌ no market    |
| `OUTS`   | ❌ MISSING      | ❌ no market    |
| `H`      | ✅ hits         | ❌ "h" missing  |
| `RBI`    | ✅ rbi          | ❌ "rbi" missing|
| `TB`     | ✅ totalBases   | ❌ "tb" missing |
| `R`      | ✅ runs         | ❌ no market    |
| `BB`     | ✅ baseOnBalls  | ❌ no market    |
| `ER`     | ✅ earnedRuns   | ❌ no market    |

`HR` is the most urgent: it will raise `GameLogUnavailable` immediately,
same as `K` before this fix.
