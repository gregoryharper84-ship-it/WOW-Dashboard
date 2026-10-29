# V17 NCAAF no-key player-history acquisition — SportsDataverse NCAA MFB

`v17/ncaaf_sdv_player_history_acquisition.py` consumes the documented
`ncaa_mfb_player_stats_{season}.parquet` season release. The separate monthly
GitHub Actions acquisition starts with historical season **2024** and saves a
SHA-256 manifest plus an exact short-lived raw Parquet archive as a GitHub
Actions artifact (7-day retention). It does not need CFBD credentials, write
Supabase tables, invoke a model, modify PropsMadness, place a wager or publish
probabilities.

Documented release:
`https://github.com/sportsdataverse/sportsdataverse-data/releases/download/ncaa_mfb_player_stats/ncaa_mfb_player_stats_2024.parquet`
Documentation: `https://py.sportsdataverse.org/docs/cfb/reference/loaders/ncaa`.
Attribution: SportsDataverse contributors, dataset repository CC BY 4.0
`https://github.com/sportsdataverse/sportsdataverse-data/blob/main/LICENSE`.
Per-dataset third-party rights and actual release content require independent
review before model-training use.

This probe counts 7 candidate NCAAF player-game statistical categories and
reconciles malformed/unsupported rows. It never turns a retrospectively
retrieved outcome into a pregame feature or historical sportsbook market line.
The feed documents a `name` field but provides no authoritative unique
player identifier in this table; no canonical player-game join can occur from
name alone. `contest_id`, `espn_game_id` and `team_id` stay provider aliases
until matched to authoritative scheduled event/team records. Do not substitute
these snapshots into the existing `CFBD` /games/players ledger: sources and
stat formats are different.

Run offline: `cd artifacts/wow-engine && python -m pytest -q tests/test_ncaaf_sdv_player_history_acquisition.py`

Next: verify actual GitHub artifact download and source schema; independently
join completed `contest_id` / ESPN game identity + player identity against
official/persisted outcomes; validate point-in-time source release timestamps
and chronological splits; separately admit a source-hashed historical cohort
into private persistence. Existing CFBD `ncaaf_prop_history_maintenance`
continues alongside this no-key path. None of these steps grants calibrated
model probability without fitted specialist certification.

PropsMadness continues independently for NBA/NFL/MLB/WNBA discovery and
sanitized account-view research; no automated authenticated vendor copying,
historical line reconstruction, credentials or private endpoint extraction is
added in this change.

`V17_TERMINAL_REDUCER` remains sole terminal authority;
`can_execute=false`.

## 2024 live canary and per-stat quality follow-up

October 29, 2026: run #40899746382 successfully downloaded the 2024 release
and uploaded the verified SHA-256
`57fdeef7a849081a83e815a39284b0aba94061b29899cf5b5e4d7e08fd56c417`.
The initial row-level parser found 59,781 category rows, 43,667 candidate
rows, 16,114 held rows, 1,664 distinct **source game aliases**, and 15,547
`STAT_INVALID` + 567 `COUNT_NEGATIVE` whole-row holds. This conflated an
invalid metric cell with an unusable category row.

The revised parser validates each stat independently: valid attempts,
completions or yards can survive an unrelated invalid cell while invalid
values remain excluded and are separately counted by stat/reason.
Missing values are never imputed as zero. It emits `partial_candidate_rows`,
`rejected_stat_cells`, `rejected_stat_reasons`, and
`missing_stat_cells` to audit coverage. **All output remains unverified
historical research, not model-ready or official canonical outcomes.**
The archive source is still downloaded on protected-main CI with the
same short-lived provenance artifact. No licensed-vendor collection is added.


## Exact ESPN athlete-ID research crosswalk — October 29

Source: SportsDataverse `espn_cfb_player_box` release, 2024
(`https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_player_box/player_box_2024.parquet`).
Its player-box dataset carries an ESPN `game_id`, ESPN `athlete_id`,
`athlete_name`, team ID, category, and named passing/rushing/receiving
statistics. The earlier NCAA stats release uses NCAA `contest_id`, optional
`espn_game_id`, and name/category but no athlete ID in the selected table.
The sources cannot be naively joined by NCAA contest ID or team ID. The
new `v17/ncaaf_sdv_espn_crosswalk.py` reconciles only records with one
unambiguous ESPN event ID + normalized display name + stat category match,
two or more exact shared numeric stat values, and at least one nonzero value.

Duplicate name/event/category keys, incompatible stats, zero-only parity, and
missing athlete IDs are distinct research holds. Valid signed rushing yards
are handled without imputing missing values. Neither party's credentials,
private endpoints, bookmaker prices nor PropsMadness are accessed.

**Important:** A passing candidate here is not certified player identity:
- The NCAA team alias has not yet been independently mapped to the ESPN team
  identifier; generic names and name collisions remain material risks.
- Both the CFBD adapter and SportsDataverse ESPN player-box use ESPN-derived
  athlete information, so comparison does NOT demonstrate independent source
  confirmation.
- The connected CFBD historical `/games/players` outcome records and game
  identities were verified in Supabase separately. **This job does not
  connect to that Supabase database**, so matching ESPN IDs to CFBD athlete
  IDs is a future exact-key reconciliation and requires a source-read
  receipt.
- These are retrospectively obtained outcomes, not pregame model features,
  archived sportsbook quotes, or fitted probabilities.

The existing monthly+main-push GitHub Actions workflow reuses the same-run
NCAA 2024 Parquet artifact; after the normal acquisition step it temporarily
downloads an ESPN 2024 player-box release, computes source-hashed aggregate
counts, deletes the ESPN Parquet staging file, and uploads a seven-day
**aggregate-only** research manifest. This adds no database write, no extra
scheduled job, and no change to scorer/terminal paths.

**Next acceptance:** read the real main-branch workflow run's cross-source
manifest and distinguish candidate/mismatch/ambiguity. Only then consider
an authorized exact-ID `(game_id, athlete_id, stat_type)` match against the
existing CFBD sporting outcomes, independent stat contradiction checks,
chronological feature creation, and model replay.

`V17_TERMINAL_REDUCER` remains sole authority; `can_execute=false`.

### Live raw-Parquet schema correction, October 29

Protected-main run #40906843881 downloaded both releases and correctly
failed at schema validation. The ESPN **raw Parquet** contains
`passingYards`, `completions/passingAttempts`, `rushingYards`,
`rushingAttempts`, `receptions`, `receivingYards`. Public loader-facing
documentation exposes separately normalized snake_case names, which must
**not** be used to select raw Parquet fields. The audited correction pins
the actual source schema, preserves fail-closed missing field semantics,
and adds a synthetic regression that rejects loader aliases in raw data.
