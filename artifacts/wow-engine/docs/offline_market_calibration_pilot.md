# WOW V17 offline model–market calibration pilot

**2026-11-12. Research-only branch; not merged into main, not published, not certified.**

## Why this exists

Repository PR #2605 already proved that GitHub-hosted rapid CI can download and verify 11 pinned official nflverse CSVs (61,620,003 bytes) and five PBP archives (95,753,616 bytes). The blocker is now cross-source identity / zero-opportunity and independent semantic certification, not a global runner DNS failure. This patch does **not** fix the 3,994 missing weekly-stat participant-games or 48 unresolved PFR-to-GSIS player-games. It provides a strict *evaluation-only* companion for already-frozen, independent model forecasts and as-of market snapshots, without any new scheduler, schema, marketplace or paid dependency.

## Run

```bash
cd artifacts/wow-engine
python -m pytest -q tests/test_offline_market_calibration.py
python v17/offline_market_calibration.py --csv /path/to/frozen_unattested_rows.csv --out /path/to/new_research_receipt.json
```

The associated local pilot archive contains an invented `synthetic_fixture.csv` **solely for validation**, but no fixture is admitted as training or production evidence. The checked-in tests generate their own synthetic inputs. The resulting metrics are `*_unattested`. No source is downloaded. No model is fitted, certified, promoted or registered. This patch has not been independently QA/Release/SIRT accepted; V17 terminal and execution settings are untouched.

## Required input contract

One CSV row per exact selection/line in a **two-outcome MONEYLINE pilot**, with `selection`/`quote_selection` equal to `HOME` or `AWAY`, `direction=quote_direction=opposite_quote_direction=WIN`, `opposite_quote_selection` the other side, and `line=NO_LINE`. Other markets, directions, and unproven complements (spread/total/props included) are held as `REPLAY_MARKET_QUOTE_PAIR_MISMATCH` until market-specific contracts are independently reviewed. Must include: `sport`, `event_id`, `market`, `period`, `selection`, `line`, `direction`; `home_team_id`, `away_team_id`, `quote_home_team_id`, `quote_away_team_id`, `settled_home_team_id`, `settled_away_team_id`, `quote_event_id`, `settled_event_id`, `quote_market`, `quote_period`, `quote_direction`, `quote_selection`, `quote_line`; `event_start_utc`, `forecast_asof_utc`, `latest_feature_available_utc`, `training_cutoff_utc`, `settled_at_utc`, `quote_asof_utc`; `partition=HOLDOUT`, `immutable_prediction_id`, `model_artifact_checksum` (64 lowercase hex), `model_probability`; `quote_source_sha256` (64 lowercase hex), `quote_book`, `opposite_quote_source_sha256`, `opposite_quote_book`, `opposite_quote_asof_utc`, `opposite_quote_event_id`, `opposite_quote_market`, `opposite_quote_period`, `opposite_quote_selection`, `opposite_quote_direction`, `opposite_quote_line`, `settled_market`, `settled_period`, `settled_selection`, `settled_direction`, `settled_line`, `quote_outcome_count=2`, `market_settlement_type=BINARY`, `selection_decimal_odds`, `opposite_decimal_odds`; `result_status=FINAL`, `void_or_push=false`, `tie=false`, `outcome=0/1`.

Both opposite sides must identify the same book, event, market, period, line and capture instant, and distinct side/direction; source hashes are syntactic only. Settlement must independently match event, teams, market, period, selection, direction and line. Conflicting team identities for one official event HOLD all of its selections. Reuse of one immutable prediction ID across independent theses is rejected, and blank bookmaker tokens are invalid. All source identifiers must match exactly; never resolve identity by team name or a ±1 day date fallback. Model and latest-feature/training timestamps must precede game start; same-book, two-sided quote must be no later than the model forecast and no older than **60 minutes** by default (research-only comparison parameter, *not* a qualification threshold); settlement must occur after start. Unmatched, stale, malformed, duplicate, three-way, tie, push, void or non-final rows are held, not silently dropped into metrics. Closing-only odds captured *after* forecast time are not a valid time-aligned model comparator; CLV is a separate diagnostic. All source digests are syntactic claims until independently attested.

Brier and log loss are computed separately, without clipping strictly valid extreme probabilities, for externally supplied fitted-model probability and the proportional two-sided no-vig market estimate; fixed ten-bin reliability tables are emitted for both. Metrics do not imply forecast improvement, a calibrated interval, statistical significance or certification. Fixture tests include red-then-green coverage for missing opposite-book evidence, settlement market mismatch and contradictory team/event records. Initial upload: 35 tests; hardened patch: 50 tests passing locally.

## Source intake policy

- `nfl_data_py` is archived/deprecated; current Python adapter is `nflreadpy` (`load_schedules`, `load_player_stats`, etc.), but continue the existing pinned-byte nflverse retrieval inside the governed NFL candidate PR to avoid unexplained source changes.
- SportsDataverse supports `sdv.nba.load_nba_team_boxscore` and other loaders, but game boxscores/results are strictly post-event labels. Use only genuinely pregame, timestamped prior-game features for fitting.
- Football-Data exposes CSV European soccer odds; distinguish open/closing quotes, inspect per-column coverage; **Pinnacle columns after 2025-07-23 are warned by the publisher as systematically outdated**.
- Tennis-Data openly says its files are intended for private individuals, **not automated AI/data-training products**. No autonomous ingestion without explicit rights clearance.
- StatsBomb Open Data provides research event files subject to its public terms/attribution. Rights approval for WOW training/distribution is distinct from technical accessibility.
- OddsHarvester is scraper software, not proof OddsPortal allows harvesting in this workflow. Do not enable an unattended scrape pending terms/rights review.
- A public package/API can be free to install but still depend on a paid historical endpoint, usage limits or nonpermissive source terms. No purchases or new subscriptions authorized.

## Acceptance before merge/deploy

This is a **local, pre-review research patch**. Merge only through repository governance after same-head CI and independent review. Admissible model certification additionally requires proven source rights, point-in-time raw feature provenance, train/calibration/untouched holdout split, non-leaking exact odds identity, calibrated uncertainty, real forward grades, production SHA matching and `scoreWowPickRequest` receipt via `V17_TERMINAL_REDUCER`. No fallback to market, heuristic or LLM probabilities.

Safety invariants: `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## Code-review integrity follow-up

Five CodeRabbit-negative-path cases (extreme valid probability not clipped, year-boundary offset conversion, same-outcome aliases and uncontracted spread) were reproduced RED and passed GREEN. A model probability is never supplied by the research tool; source attestation, independent QA/Release/SIRT, model certification and production ingestion remain separate.
