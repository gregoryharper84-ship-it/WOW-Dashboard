# WOW V17 multi-source historical collectors — BACKLOG / DRAFT

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
