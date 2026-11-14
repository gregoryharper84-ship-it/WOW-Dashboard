# WOW V17 — 13-sport source acquisition and certification work order

**Objective:** `WOW-V17-DATA-SOURCE-ALL13-20261111`  
**Owner:** WOW Betting Intelligence (data fitness, candidate union, model acceptance); AMRT/Engineering (source acquisition, receipts, code); SIRT/Independent Verification (independent rights, replay, release verification).  
**Status:** `WORKING_NOT_COMPLETE` — research candidates, **not newly certified models**.  
**Safety:** `can_execute=false`; `V17_TERMINAL_REDUCER` sole final authority; never place a wager, auto-promote a model, or treat a source as a sporting probability.

## Source inventory — exactly the existing 13-sport V17 catalog

This is a **research/acquisition routing plan**, not a new authority or source-rights decision. It reuses `v17/model_source_entitlements.py`, `v17/free_source_catalog.py`, `v17/source_coverage_audit.py`, `v17/team_event_model_development_manifest.py` and `v17/all_sport_recovery_proof_matrix.py`. All 13 must remain visible even if no admissible data exist.

| Sport | Existing source or research candidate | Immediate dataset work | Hold before fitted training/certification |
| --- | --- | --- | --- |
| NFL | nflverse frozen release datasets; dataset-specific CC BY/CC BY-SA separation | Pin season/file SHA, official identity, source release and as-of lag; reconcile missing player-games | Upstream and exact-dataset rights, holdout, calibrated bounds; forward cohort remains distinct |
| NBA | SportsDataverse ESPN and BALLDONTLIE | Reconstruct verified historical UTC pregame tipoffs and source-specific opponent IDs; protect existing corpus | ESPN-derived upstream rights, leakage-free chronology, artifact/calibrator source review |
| NCAAF | CFBD (credential) and SportsDataverse NCAA MFB archive | Pin team/player/game identity, frozen features, source snapshot and pregame clock | Credential/source scope, complete fitted replay and unpromoted specialist |
| NCAAB | SportsDataverse ESPN | Canonical ordered home/away identities, season schedules, pre-tip feature snapshots | Source rights and model/calibrator replay digest, independent team-event acceptance |
| MLB | Existing stats API and research adapters | Acquire *only* licensed/approved official settlement and starter/lineup/bullpen histories | MLB's published noncommercial/automated-use restrictions; no unauthorized scraping |
| NHL | Existing official GameCenter research + separate two-provider goalie contract | Independently licensed starter attestations, injury/rest/strength/PP/PK, OT/SO settlement | NHL first-party commercial rights, independent actual goalie providers, calibrated artifact |
| WNBA | SportsDataverse and BALLDONTLIE | Source-pinned settled match logs, UTC start, minutes/injury/rotation availability | Upstream rights/credential, fitted chronology, shadow-calibrator promotion review |
| SOCCER | openfootball/football.json (publisher states CC0) | Source-pin seasons/leagues, ordered teams, kickoff times; separate 90-minute 3-way draws from extra time | Pre-match as-of evidence; event/time identity and 3-way calibration |
| TENNIS | Valuebetennis CSV (publisher states CC BY 4.0) | Pin ATP/WTA season files, player/tour IDs, UTC starts, retirement/walkover status | Dataset-level attribution, cutoff identity, retirement-aware calibration |
| PGA | DataGolf commercial entitlement only in current governed registry | Explore genuinely open tournament/field history; don't reuse paid API without entitlement | Source/license rights and full-field winner distribution; BUILD_REQUIRED |
| MMA | UFCStats public candidate | Find documented permitted historical bout/fighter inputs and verified pre-fight timing | First-party rights undocumented, collector not proven, calibrator absent |
| BOXING | **New research-only** Open Boxing public title-bout JSON/CSV | Verify its published dataset license, historical completeness, fighters, bouts and results | No recognized training license; title fights are not full boxing event coverage |
| CRICKET | **New research-only** Cricsheet **international T20** JSON ZIP (ODC-BY publisher notice) | Bounded explicit publisher download for **international T20 only** (not all domestic T20 competitions); pin SHA, verify included LICENSE, unique match IDs, winners/ties and no ZIP traversal | Source license scope/independent review; match dates are not archived pre-match team inputs; T20 model BUILD_REQUIRED |

## Source discovery performed — no rights granted

- Cricsheet primary publication: https://cricsheet.org/licensing/ and https://cricsheet.org/downloads/ . As of 2026-10-07 the publisher describes its JSON/YAML/CSV match data as ODC-BY 1.0, while XML is CC BY 4.0. The source makes explicit that some matches are withheld. **No full publisher archive was downloaded or trained here.**
- Open Boxing public title-bout API: https://openboxing.org/api/ . Available URLs are discovery evidence only; a training license and full-fight corpus have **not** been verified.
- Existing open datasets: https://github.com/openfootball/football.json ; https://www.valuebetennis.com/en/donnees.htm . Publication descriptions are not raw-byte source or certified replay receipts.
- NFLVERSE / NHL / MLB source rights reviewed in existing `docs/wow/engineering/2026-11-09-free-source-coverage-receipt.md`; do not downgrade stronger known noncommercial or upstream-rights holds.

## Cricket candidate archive preflight — implementation

Run only after an authorized source-use review. A manually acquired ZIP can be audited offline without touching external services:

```bash
cd artifacts/wow-engine
python -m v17.cricsheet_source_candidate \
  --archive /secure/quarantine/t20s_json.zip \
  --expected-sha256 <trusted-64-character-SHA256> \
  --receipt /secure/quarantine/cricsheet-t20-source-receipt.json
```

For an explicit bounded, same-origin **research-only** fetch in a network-enabled environment:

```bash
python -m v17.cricsheet_source_candidate \
  --download \
  --quarantine-archive /secure/quarantine/t20s_json.zip \
  --receipt /secure/quarantine/cricsheet-t20-source-receipt.json
```

Neither command registers a model, ingests data to a durable fitted ledger or declares the source authorized. The receipt always reports `model_training_authorized=false`, `probability_publishable=false`, `can_execute=false`, `pregame_feature_chronology_status=UNVERIFIED`. If `--expected-sha256` is omitted, the acquisition remains explicitly `UNPINNED`.

## Deterministic release and model acceptance

1. **Source (A1):** exact dataset and permitted use; raw source bytes+SHA, terms+license digest, source timestamp, official sport/event/participant identity, corrections/retractions and duplicate quarantines; actual pregame feature archive rather than postgame lookahead.
2. **Fitted specialist (D1):** separate `TEAM_EVENT_WINNER` fitted model per sport, with exact market/settlement semantics. Player/scalar props belong to `WOW_PROP_LANE`; do not silently share D1 probabilities.
3. **Chronological replay:** train on prior seasons/windows, calibrate on later data, keep untouched test/forward sets, no future injuries/lineup/weather; require model and feature hashes and frozen as-of snapshots.
4. **Quality:** cohort `n`, Brier, log loss, calibration bias, lower-bound reliability, uncertainty/tails and suitable timestamp-correct baselines; no forced number of picks and no thin-sample certification.
5. **Governance:** independent source-rights review, independent fitted artifact/replay/adversarial SIRT, protected exact-SHA QA+Release and authorized promotion. Passing the source preflight never auto-certifies.
6. **Production:** verified live web+worker SHA parity, exact authenticated `scoreWowPickRequest` response, immutable prediction identifier and Action receipt, calibrated probability/lower bound, V17 terminal disposition and live editor parity. No wagers.

**Objective acceptance target:** all 13 source routes reconciled to `SOURCE_RECEIPT_VERIFIED`, typed `BLOCKED`, `UNSUPPORTED`, or `RESEARCH_ONLY`, never omitted. A sport achieves `COMPLETE` only with independently accepted numerical model and actual serving proof. Missing price never erases sporting probability, and research/market evidence never substitutes for a fitted one.

**Current verified environment limitation:** shell in this ChatGPT workspace cannot resolve `cricsheet.org`; live archive download and hash validation **not executed** here. Only synthetic negative tests ran locally. The existing hosted CI and nonauthor review must independently reproduce the new source code and receipts.


## Verified OpenFootball source observation — 2026-11-12 UTC (research-only)

At exact GitHub upstream commit `e6744429ee395bc86f247348c6184bb08d4eb361` in `openfootball/football.json`, the live connected read-only GitHub source returned CC0-1.0 `LICENSE.md` blob SHA-1 `670154e3538863b2d9891fd5483160fbdfc89164` (decoded UTF-8 SHA-256 `36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673`) and five league JSON files for 2024/25.

- League files: Premier League, Bundesliga, La Liga, Serie A and Ligue 1; **1,752 exact match records** across all five; **1,732 with final scores**, **434 draws**, **20 missing scores**, **zero duplicate (date, team1, team2) keys** within files.
- Missing-score discrepancy is **10** in `2024-25/es.1.json` and **10** in `2024-25/it.1.json`; these are `MISSING_FINAL_SCORE`, not automatic losses, voids, ties or proven postponed events.
- The decoded `2024-25/en.1.json` has UTF-8 SHA-256 `c3d18f552d83382cd4b6b21127adbbfe38e7f8618ddc979fb5404f2df07d0a75` and Git blob SHA-1 `504f2e732fbc3c9caeb98e4382b13b094453b840`. Other exact file blob IDs and full counts are in `v17/research/source_openfootball_five_leagues_2024_25_observation_20261112.json`.
- All 1,752 fixtures have HH:MM clock strings; timezone is **not established**, no archived historical feature snapshot is proven pregame, and repository scores are **not independently verified official settlement**. This is authentic historical file observation, not signed licensing, model replay or promotion.
- The observation JSON and conservation tests are **read-only** and do not register training permission, model probabilities, calibrated bounds, production receipts or terminal acceptance. Required evidence remains `CLOCK_TIMEZONE`, `ORIGINAL_PREGAME_SNAPSHOT_TIME`, source upstream rights, official event settlement, model calibration and independent review.

Use this evidence to target the soccer data-identity chronology patch next; do not fill missing scores, assign UTC offsets or manufacture pregame lineups from this source.
