# WOW V17 multi-source historical collectors — BACKLOG / DRAFT

## NFL-first pilot — 2026-10-21

Added `v17/nfl_multi_source_pilot.py` and `tests/test_nfl_multi_source_pilot.py` to the same unmerged branch. NFL now leads the source pilot because WOW already contains:

- `nflverse_historical_adapter.py` and `nflverse_dataset_policy_v1.json`: canonical game/player IDs, kickoff joins, stable stats and CC BY licensing at dataset level.
- `nfl_prop_auto_hydration.py`: existing ESPN athlete/event identity normalization and NFLverse evidence for supported prop routes.
- `scripts/train_nfl_props.py`: existing offline NFL prop model development (no new training or runtime activation here).

Pilot manually reads NFLverse `stats_player_week_{season}.csv` and the independent nflverse/nfldata `games.csv` schedule feed, hashes exact bytes, normalizes six existing historical stat families and separately reports optional already-verified ESPN event parity. **These two CSV feeds are related nflverse sources, not proof of independent publisher agreement.** Passing/rushing/receiving TD counts are historical outcomes; anytime-TD model scoring is NOT performed.

**Entitlement reconciliation remains pending:** global `historical_source_manifest_v1.json` says NFLVERSE `LICENSE_REVIEW_REQUIRED`, while dataset policy marks core player stats and schedules as CC-BY-4.0. The pilot explicitly reports `RESEARCH_ONLY`, `ready_for_offline_training=false`, `production_certified=false`, `probability_publishable=false`, `can_execute=false`. Participation and FTN charting remain excluded. Do not promote global rights or production training from dataset-policy alone.

### Manual research-only verification

```bash
cd artifacts/wow-engine
pytest -q tests/test_nfl_multi_source_pilot.py
python -m v17.nfl_multi_source_pilot --season 2025
# Alternatively, validate pre-acquired and entitlement-reviewed raw CSV files:
python -m v17.nfl_multi_source_pilot --season 2025 --schedule-csv ./games.csv --player-csv ./stats_player_week_2025.csv
```

The read-only URL allowlist includes only NFLverse 20xx `stats_player` releases and the canonical nfldata games.csv. No automatic ESPN scraping, Supabase staging, scheduler, credential use, or model integration is added. Treat the 2025 season as a closed historical pilot; partial 2026 weeks require separate final/identity and as-of validation before any assertions.

### NFL acceptance still pending

- Run actual repository test collection/CI; no claim of green until receipts observed.
- Execute a read-only live-source canary and retain hashes, row counts, stopped/unjoined counts, and timestamp.
- Supply separately verified ESPN final event records to the optional parity function, not synthetic identity matches.
- Keep issue #1933 open and draft PR #1937 unmerged/undeployed.

---

**Backlog:** #1933 (parent #204). No production integration, deployment, scheduler, Supabase writes, model training, calibration, or wagering execution.

## Included in draft

- `v17/multi_source_historical_collector.py`: explicit-invocation, read-only acquisition.
- `MLB_STATS_API`: a specified finalized game, official event/team/player IDs and settled batting/pitching counts.
- `SPORTSDATAVERSE_WNBA_STATS`: a specified season of player game logs, alias-aware CSV parsing, played/nonplayed reconciliation.
- Both providers must be `SPORTING` and `V17_APPROVED` in WOW's preexisting source manifest. No new entitlement is granted.
- Results contain provider, source URL, UTC retrieval time, SHA-256 of raw source bytes, source identity, and license metadata where available.
- WNBA role, opponent identity, and exact start-time joins remain unresolved. No pregame training features are manufactured.
- Source access errors, 403/429, malformed data, restricted origins, and duplicate rows stop ingestion without bypass attempts.
- All batches declare `BRONZE_OUTCOMES_ONLY`, `training_materialized=false`, `probability_publishable=false`, `can_execute=false`.

## Manual development use

From `artifacts/wow-engine`:

```bash
python -m v17.multi_source_historical_collector --provider MLB_STATS_API --game-pk 123456
python -m v17.multi_source_historical_collector --provider SPORTSDATAVERSE_WNBA_STATS --season 2026
pytest -q tests/test_multi_source_historical_collector.py
```

MLB 123456 is an illustrative placeholder, not a verified actual game. The command manually fetches selected source and prints JSON to stdout. No database writes occur.

## Follow-on acceptance before operational claims

1. Run fixture tests against full repository dependencies in CI; then execute real read-only provider canaries and retain signed-off source receipts.
2. Reconcile WNBA mutable release bytes with WOW-owned pinned snapshots, schedule, and boxscore identity; do not train from unpinned releases.
3. Add versioned staging tables, idempotent writes, quarantine and scheduling in separate reviewed change, not this draft PR.
4. Implement NBA and NFL sources only after checking permissions; PropsMadness remains contract/permission-required for recurring extraction and reuse.
5. Implement provider budgets, backoff, authorized fallback and source health telemetry; keep MARKET data separate from SPORTING data.
6. Send features to offline Model Excellence challengers only after temporal leakage validation, holdout, and certification.

**Definition of done for this draft:** code and offline fixture tests available for review. #1933 remains OPEN until two real permitted-source receipts and isolated staging acceptance are verified.
