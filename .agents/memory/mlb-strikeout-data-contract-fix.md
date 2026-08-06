---
name: MLB strikeout DATA_CONTRACT_FAIL fix
description: Four-bug root-cause for why pitcher strikeouts returned DATA_CONTRACT_FAIL; the fix; and what similar bugs remain for other MLB stat_keys.
---

# MLB pitcher strikeout DATA_CONTRACT_FAIL

## Fixed (2026-08-06)

Four compounding bugs:

1. `"K"` missing from `_MLB_STAT_FIELDS` in auto_game_log.py → GameLogUnavailable before any HTTP call.
2. `"k"/"so"` missing from `_PROP_TYPE_TO_MARKET_SUFFIX` in auto_enrichment.py → Odds API lookup silently skipped.
3. `build_auto_enrichment` populated only 7 of 19 required data_contract fields → run_intake failed.
4. `l5_l10_ledger` result keys never copied back to `enr` → run_deferred saw l5/l10 as None.

**Key naming quirk**: l5_l10_ledger stores `l5_games`/`l10_games`/`l10_avg` internally; data_contract expects `l5_values`/`l10_values`/`l10_mean`. The Fix-4 write-back maps these explicitly.

**Sentinel contract for pipeline-output fields**: `provisional_label="PENDING_GATE_EVALUATION"`, `payout_context/failure_path_matrix/model_probability_ledger="NOT_COMPUTED_AT_AUTO_ENRICHMENT"`, `directional_exposure_tags=[]`. These satisfy `_is_present()` without injecting false data (gates read from `row["gates"]`, not `enr`).

**_fetch_mlb now returns 3-tuple** (values, source, metadata). Test files that call `_fetch_mlb` directly must unpack 3 values. Cache stores metadata under `"meta"` key.

## Remaining similar bugs (same prop_type = stat_key pattern)

`HR` is the most urgent — completely absent from `_MLB_STAT_FIELDS` and market suffix:

| stat_key | game log fetch | market lookup |
|----------|---------------|---------------|
| HR       | ❌ MISSING    | ❌ "hr" missing |
| SB       | ❌ MISSING    | ❌ no market   |
| IP/OUTS  | ❌ MISSING    | ❌ no market   |
| H        | ✅ OK         | ❌ "h" missing |
| RBI/TB   | ✅ OK         | ❌ short-key missing |

**Why:** `app.py/_norm_to_pipeline_row` sets `prop_type = stat_key` (short key). All dictionaries in auto_enrichment.py and auto_game_log.py were written for long-form prop_type strings. Fix pattern: add short-key aliases to `_MLB_STAT_FIELDS` and `_PROP_TYPE_TO_MARKET_SUFFIX`.
