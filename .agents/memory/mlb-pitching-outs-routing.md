---
name: MLB pitching outs stat_key routing
description: Four-file fix required to wire "pitching outs" → pitcher_outs data path; MLB Stats API uses "outs" (int), not "recordedOuts".
---

## Rule
Any prop that normalizes to stat_key "OUTS" needs entries in four places simultaneously.  Missing any one silently drops the fetch or produces an empty enrichment.

## The Four Files
1. **gate_engine/auto_game_log.py** — `_MLB_STAT_FIELDS["OUTS"] = "outs"` + add `"OUTS"` to `pitcher_keys` set so the pitching split group (not hitting split) is queried.
2. **gate_engine/auto_enrichment.py** — add `"pitching outs": "outs"` and `"outs": "outs"` to `_PROP_TYPE_TO_MARKET_SUFFIX`; add both to `_PITCHER_PROP_TYPES` so the prefix becomes `"pitcher"` not `"batter"` → final market key = `"pitcher_outs"`.
3. **services/player_logs.py** — add `"pitcher_outs": ["outs"]` to `PROP_STAT_MAP` (ESPN core API field is also `"outs"` for pitching splits).
4. **services/odds_api.py** — add `"pitcher_outs"` to the supported-markets list so quota-aware fetches include it.

## Critical field name
MLB Stats API pitching `gameLog` splits carry `"outs"` (integer, already in recorded-outs units).  `"recordedOuts"` does NOT appear in gameLog splits — it exists only in boxscore contexts.
- `inningsPitched` is available as a fraction-string ("4.1" = 13 outs) but `"outs"` is cleaner.
- 4.1 IP → outs=13, 5.0 IP → outs=15, 6.0 IP → outs=18.

**Why:** The ESPN player_logs.py path returns 0 values for pitcher_outs because ESPN core API does not expose a recorded-outs field.  The MLB Stats API path (auto_game_log) is the only live data source.

## Test guard
`gate_engine/tests/test_auto_game_log.py::TestMLBPitchingOuts` (5 tests) + `gate_engine/tests/test_auto_enrichment.py::test_pitching_outs_*` (4 tests) cover all four files.
