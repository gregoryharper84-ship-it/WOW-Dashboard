# WOW V17 — Six-sport historical player-prop source matrix (October 29, 2026)

**Scope:** source evidence and research-only historical outcomes. No model probability publication, automatic vendor data capture, production writer activation, trading or wagering. This memo records observed data, not permission to reuse any vendor content.

| Sport | Preferred context | Outcome/backfill source | Verified status | Next independent proof |
| --- | --- | --- | --- | --- |
| NFL | Authenticated PropsMadness visual player analytics | NFLverse (rights review) / licensed official football data | Signed-in NFL view verified; research adapter #2255 merged and web LIVE. No retained vendor corpus | Independent football outcome reconciliation; live browser adapter acceptance on second player |
| NBA | Authenticated PropsMadness visual player analytics | Licensed NBA player-game record / reviewed open dataset | Account-view offline adapter #2258 merged and web LIVE; real NBA view not yet accepted | User-controlled real player/market view and official game ID match |
| MLB | Authenticated PropsMadness visual player analytics | Existing MLB Stats API / Statcast governed source | Offline account-view adapter; official MLB results already available independently | Real signed-in MLB view, outcome/inning/stat reconciliation |
| WNBA | Authenticated PropsMadness visual player analytics | SportsDataverse WNBA Stats (pinned CC BY 4.0, attribution) | Offline account-view adapter; production source manifest approved WNBA Stats only | Real WNBA account-view and pinned source outcome join |
| NCAAF | **No PropsMadness coverage** | CFBD `/games` and `/games/players` | Existing CFBD client, hydrator, 7-stat normalizer, training challenger; 4,227 game records (2022–2026) but **zero /games/players snapshots** in connected validation DB at audit | Use existing `ncaaf_prop_history_maintenance` with a properly configured CFBD_API_KEY; capture 2023–2026 weekly player snapshots; reconcile exact game/player/stat before model fitting |
| NHL | **No PropsMadness coverage** | NHL official public schedule and gamecenter boxscore | 6,562 stored final-game source rows representing 6,560 distinct events. New official player-game stats research parser in this PR | Verify official player/stat boxscore rows across multiple completed games and separately review allowed data reuse |

## NCAAF actual data readback

Project `wow-engine-validation`, 2026-10-29:
- `public.wow_ncaaf_training_games`: **4,227** records split 2022 896; 2023 910; 2024 919; 2025 934; 2026 568.
- `public.wow_ncaaf_source_snapshots`: **242** rows: **142 /games** and **100 /ratings/elo**; **0 /games/players**.
- The in-repo `ncaaf_cfbd_client.py` already permits `/games/players`; `ncaaf_cfbd_hydrator.py` has `hydrate_cfbd_player_stats_season`; `ncaaf_prop_historical_adapter.py` supports the seven V17 Phase-1 stat families; `v17/ncaaf_prop_history_maintenance.py` already caches and acquires missing weeks. **Reuse these existing modules**. No new key should be committed to source or chat.

## NHL actual data readback and live sample

Project `wow-engine-validation`, 2026-10-29:
- `public.wow_d1_source_events` NHL subset: **6,562** rows / **6,560 distinct official event IDs** between 2021-10-12 and 2026-04-17. All are retrospective/retrieved after games began; this count is **not** a verified archive of pregame line prices.
- One public official final boxscore read, `https://api-web.nhle.com/v1/gamecenter/2025020910/boxscore`: ID `2025020910`, 2025–26 season, home `NJD`, away `BUF`, start `2026-02-26T00:00:00Z`, `OFF`. The live schema exposed **36 skaters and 4 goalies**, with each of 36 skaters having integer goals, assists, points and SOG, each goalie having saves/shotsAgainst; zero points inconsistencies. **148 candidate player-stat outcomes** (36×4+4) on that game *if all are admitted by the offline adapter*. This is a single source-layout observation, not a persisted 148-row corpus or an independently validated training dataset.
- New `v17/nhl_player_box_outcome_research.py` accepts a caller-supplied official final boxscore JSON and emits only candidate sporting outcomes; every historical book/line/price/time remains null; no network, persistence or fitted model invocation.
- Existing NHL goalie/shot historical replay and NHL total-goals source modules already have official schedule and game identity checks. Reuse them rather than inventing another collector.
- Manifest source `NHL_PUBLIC_API` remains `LICENSE_REVIEW_REQUIRED`; do not promote NHL outcomes into production training on the strength of a public URL alone.

## PropsMadness: maximum useful role within current terms

Proprietary PropsMadness account-view analytics offer game logs, opportunity/route/target and market context for **NFL/NBA/MLB/WNBA**. Existing GitHub PRs #2255 and #2258 merged and the web service deployed; the interfaces currently accept sanitized browser-visible text **offline only**. Neither integrates a signed-in automated browser worker nor runs a recurring collector or database writer.

Use account access for authorized visual research and parser compatibility, while keeping any account-derived data transfer, historical retention and trained-model reuse behind the specific permission necessary. The published March 9, 2026 PropsMadness terms restrict reproduction and reuse without prior written consent. User account login alone does not establish that permission. Do not use private endpoints, scrape around auth restrictions, or export session cookies.

The separate `wow_prop_history.research_outcome_snapshots` table exists in the validation database, RLS enabled, service role INSERT/SELECT only, zero rows. This is research infrastructure; its source-rights receipt requirement must be validated independently before a vendor writer is admitted.

## Chronological contract

- Postgame outcomes and retrospective chart comparison are **never** labeled historical pregame offers.
- A pregame market snapshot may only claim its actual observed timestamp, captured before the associated event start; no reconstructing yesterday's quote from today's selected line.
- Sporting outcomes use official identity and event/stat/period; market evidence uses sportsbook, exact threshold, direction, observed time and source provenance separately.
- Never substitute a hit-rate or market implied probability for a calibrated fitted WOW specialist; `V17_TERMINAL_REDUCER` alone publishes governed decisions, and `can_execute=false`.

## Existing-source next execution path

1. Prioritize live account-view **layout acceptance** for PropsMadness NBA, MLB and WNBA (the signed-in NFL page was inspected); then verify another market per league. Do not schedule unlicensed bulk collection.
2. Run the already-built NCAAF CFBD player-game history maintenance under its existing authenticated server-side source route, with a validated CFBD key and scope, then check exact `/games/players` snapshot receipts. Do not confuse 4,227 *team games* with player-prop history.
3. Run a bounded NHL official `gamecenter/{id}/boxscore` parser canary across more completed games and compare player IDs, event times and counts; review reuse rights before any archival collector or model training.
4. Use the six sports' independent sporting outcomes to build source-hashed, chronological training and holdout cohorts. Do not activate market card publication or trading.

Source references: CFBD official [Getting Started](https://api.collegefootballdata.com/getting-started) and [Usage](https://api.collegefootballdata.com/usage-and-access); PropsMadness [Terms](https://propsmadness.com/terms-and-conditions).
