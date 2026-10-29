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
