---
name: Auto-Enrich Acquisition Key-Promotion Bug
description: Two-bug root cause of DATA_CONTRACT_FAIL on auto_enrich=true MLB hitter props and the R4/R4b fix chain.
---

## Root Cause (WOW-PATCH-2026-08-16-R4)

When `auto_enrich=true` and the caller supplies no `player_id`/`game_log`:

1. `build_auto_enrichment` writes full enrichment (game_log, sentinels, sportsbook_line,
   failure_path_matrix, player_id) under key `"jeremy peña:hits"` (player:prop format).
2. `_check_prop_game_log` (acquisition_orchestrator) only checked `enrichment.get(row_id)` → None.
   Bug: missed the player:prop entry.
3. Fallback: tried fetch with raw display label `"Hits"` (not canonicalized) →
   `GameLogUnavailable` → `_stamp_enrichment(row_id, "...FAIL...")` created a SPARSE
   `enrichment[row_id]` with no game_log/sentinels.
4. `_get_enrichment` (pipeline) checks row_id first → returns sparse entry.
5. `data_contract` failed on `failure_path_matrix` (absent from sparse entry).

Bug 1: `_check_prop_game_log` missed player:prop enrichment key.
Bug 2: `_attempt_game_log_fetch` didn't canonicalize stat_key ("Hits"→"H") before fetch.

## Fix (R4 + R4b — commits 8eb437e, 6909d43)

**`gate_engine/acquisition_orchestrator.py` — `_check_prop_game_log`:**
- Now checks BOTH `enrichment[row_id]` AND `enrichment["player:prop"]`.
- If game_log found via player:prop, PROMOTES the full entry to `enrichment[row_id]`:
  `enrichment[row_id] = {**_enr_by_pp, **_enr_by_rid}` (rid entry wins on conflicts).
- Unconditionally carries `player_id` from `enr_entry["player_id"]` to `row["player_id"]`.

**`gate_engine/acquisition_orchestrator.py` — `_attempt_game_log_fetch`:**
- Calls `_canonicalize_stat_key(stat_key)` (from `auto_enrichment`) before `fetch_game_log`.
- Converts "Hits"→"H", "Runs"→"R", etc. before the MLB Stats API call.

**`gate_engine/auto_enrichment.py` — `build_auto_enrichment`:**
- After successful game_log fetch, writes `entry["player_id"] = player_id` to the enrichment
  entry so key-promotion can carry it downstream.

**`gate_engine/pipeline.py` — main processing loop:**
- After `enr = _get_enrichment(enrichment, row)`, unconditionally stamps
  `row["player_id"] = enr["player_id"]` if player_id is in enrichment and not on row.
  Previously only R3c block set player_id, and it was skipped when game_log already present.

## Key Invariants

- `failure_path_matrix` is an ENRICHMENT-level field (not row-level); must be a dict with
  `PRIMARY_KILL_PATH`, `SECONDARY_KILL_PATH`, `BLACK_SWAN_PATH` keys — a string sentinel fails
  `failure_path.run()` structural check.
- `opponent` and `odds_or_payout` are ENRICHMENT-level fields (not row-level despite being
  game/market data). They go in `enrichment[rid]`, not raw_row.
- `model_probability_ledger` triggers the Stage-2 prob_ledger pre-check enforcer (422 before
  pipeline) if supplied in incomplete Stage-2 format. Omit or provide complete Stage-2 ledger.

**Why:** `_get_enrichment` checks rid first, so any `_stamp_enrichment` call that creates
`enrichment[row_id]` (even sparse) shadows the full `enrichment["player:prop"]` entry.
The promotion pattern (full merge into rid key) is the canonical fix.
