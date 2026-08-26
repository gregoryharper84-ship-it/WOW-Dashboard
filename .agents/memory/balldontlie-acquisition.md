---
name: BallDontLie TRUSTED_STRUCTURED_STATS acquisition layer
description: Architecture and key decisions for gate_engine/balldontlie/ — the formal BDL integration added in WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS
---

# BallDontLie TRUSTED_STRUCTURED_STATS acquisition layer

## Source grade: A- (not B)
BDL is graded A- in `source_grade.SOURCE_TYPE_GRADES` under keys `"balldontlie_api"` and `"balldontlie"`.
The existing `BallDontLieAdapter.source_grade` in `opportunity_acquisition/adapters.py` was also upgraded from "B" to "A-".

**Why:** BDL is a direct timestamped API with machine-readable game/player/team IDs. Grade A- = direct
structured records, below official league feeds (A), above B-grade stat-site reconstruction.

## Package layout
```
gate_engine/balldontlie/
  __init__.py
  types.py            — BDLProvenance, BDLGameRow, BDLPlayerPackage, BDLStatus, BDLTier
  client.py           — HTTP client; tier detection (lazy, 1hr cache); fetch_all pagination
  normalizer.py       — normalize_nba_wnba_row / normalize_mlb_pitching_row / normalize_mlb_batting_row
  nba_wnba.py         — fetch_player_package(player_id, sport, season, n_games)
  mlb.py              — fetch_pitcher_package / fetch_batter_package / fetch_game_lineups
  reconciliation.py   — reconcile_value / reconcile_game_log / reconcile_lineup / reconcile_enrichment_game_log
  anti_double_count.py — deduplicate_odds / deduplicate_player_props
```

## Key contracts

### game_log / box_score_log
`BDLPlayerPackage.wow_game_log(stat_key, n)` → `list[float]`, most recent first.
`BDLPlayerPackage.wow_box_score_log(n)` → `list[dict]`, most recent first.
Season averages are NEVER placed in game logs (marked `_NOTE: SEASON_AVERAGES`).
DNP rows (min < 1.0 for NBA/WNBA) excluded from qualified_rows.

### IP from outs
`ip = (outs // 3) + (outs % 3) / 10` → WOW canonical format (e.g. 7 outs = 2.1, 18 outs = 6.0, 20 outs = 6.2).

### Tier detection
`client.detect_tier()` probes GOAT → ALL_STAR → STARTER → FREE endpoints on first use, caches 1hr.
GOAT pitch data enriched only when `endpoint_available_for_tier(BDLTier.GOAT)`.

### Reconciliation precedence
```
official_feed/official_gamelog (10) > box_score (9) > espn_api (8) > balldontlie_api (7) > statmuse/bbref (5) > espn_blurb (3)
```
SOURCE_CONFLICT: higher-precedence source wins; conflict surfaced explicitly, never silently averaged.
BDL lineup data cannot override official contradiction (`reconcile_lineup` → OFFICIAL_CONTROLS).

### Odds dedup
`deduplicate_odds()`: key = (normalized_book_name, side); price tolerance ±2 American odds units.
Same book + matching price → CORROBORATED (not added again).
Same book + different price → SOURCE_CONFLICT (existing marked `bdl_conflict=True`).

## Entry point from auto_game_log
`fetch_bdl_player_package(player_id, sport, stat_key, season, n_games, target_date)` — exported from
`auto_game_log.py`. Routes MLB to pitcher vs batter by stat_key (IP/OUTS/K/BB/BF/PC/FANTASY_SCORE_PIT → pitcher).

## Mock patch path
Tests must patch `gate_engine.balldontlie.nba_wnba._get` (not `client._get`) because the
function is imported by name at module load time.

## Null handling
Null fields are tracked in `BDLProvenance.null_fields` and never imputed. `normalize_*` functions
record every None field explicitly; `stat_value()` returns None for absent/null stat keys.

## can_execute=False
Set at module level in every balldontlie submodule — unconditional.
