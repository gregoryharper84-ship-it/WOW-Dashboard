# WOW V17 — NCAAF real player-game outcome normalization, 2026-10-29

**Scope:** research-only settled sporting outcomes; absolutely no sportsbook line, offer, price, implied probability, certified model result or wager execution. V17 source manifest marks CFBD `V17_APPROVED` for sporting data use, but that does not grant a fitted model or publishable probability.

## Verified completed four-season data acceptance

The NCAAF raw source acquisition outage was fixed in PR #2267 and verified on production Render `f57c7d3`; GitHub OIDC run #40750788534 then returned `CORPUS_UPDATED`, 80 source snapshots and 3,333 games. The new separate normalization table and function are installed in connected **wow-engine-validation** Supabase, isolated schema `wow_prop_history`. The source SQL in this PR is `artifacts/wow-engine/ncaaf_cfbd_player_outcome_normalization_v1.sql`.

| Season | Available source weeks normalized | Official game IDs | Normalized stat outcomes | Negative yards correctly preserved |
| --- | ---: | ---: | ---: | ---: |
| 2023 | 15 | 910 | 55,285 | 2,133 |
| 2024 | 16 | 919 | 55,419 | 1,805 |
| 2025 | 16 | 934 | 58,006 | 1,977 |
| 2026 (through last available source week) | 9 | 570 | 36,434 | 1,315 |
| **Total** | **56** | **3,333** | **205,144** | **7,230** |

* Twenty source snapshots per season were initially retrieved, but 24 were marked EMPTY because their week windows had no games. The 56 AVAILABLE snapshots were normalized. These observations are not historical betting market lines.

Canonical seven-stat counts from connected DB:
- PASSING_YARDS, COMPLETIONS and PASS_ATTEMPTS: 10,608 records each.
- RUSHING_YARDS and RUSH_ATTEMPTS: 37,712 records each.
- RECEIVING_YARDS and RECEPTIONS: 48,948 records each.

Independent SQL audits compared every normalized record with its exact CFBD `source_snapshot_id`, season/week and payload SHA-256: **zero source identity/hash mismatches**. Every outcome joined its `official_event_id` and `event_start_time` to the existing completed NCAAF training-game ledger: **zero event/team/start mismatches**. All 205,144 records are `can_execute=false`, `probability_publishable=false`, `model_training_eligible=false`, and contain NULL historical sportsbook book/line/odds/market timestamp. This is not a calibrated probability package.

## Repeatable private weekly operation

`wow_prop_history.normalize_ncaaf_player_week(season, week)` is an *invoker-only* PostgreSQL function, installed in a non-exposed schema. It is executable only by service-role-capable research operators (not `anon` or `authenticated`) and is not a public Supabase RPC. It requires exactly one available CFBD source snapshot, validates the matching completed official game season/week/teams, chronology, player/stat format and completion/attempt relationships, then performs an idempotent weekly research-only insert. A count mismatch raises an exception and rolls back that week's transaction.

Run through an authorized database maintenance context after the NCAAF source writer completes; for example:

```sql
select wow_prop_history.normalize_ncaaf_player_week(2026, 9);
```

A repeated 2023 Week 1 call returned exactly 8,730 outcomes without duplicating any rows. Before normalizing a later week, independently verify source availability and completed games; a week without an AVAILABLE snapshot correctly fails closed. **No recurring database normalizer scheduler is claimed** by this SQL file. Integrate it into the existing guarded NCAAF source maintenance lane only with typed completion receipts and source availability checks. Do not add service_role secrets to GitHub Actions.

## Non-negotiable downstream gates

Normalize and independently verify pregame sporting features with chronological `feature_as_of < kickoff` before fitting. Keep settled outcomes from acting as pregame evidence; use time-forward splits and held-out calibration. Sport-specific fitted probability packages, validated calibration bounds, registered specialist selection and the `V17_TERMINAL_REDUCER` remain independent. `can_execute=false`.

Cross-sport matrix: PropsMadness remains account-view research for NFL/NBA/MLB/WNBA only; real page checks for NBA/MLB/WNBA remain outstanding; recurring retention requires a permitted basis. NHL's official player boxscore adapter was merged in PR #2262, but a recurrent licensed source writer is not active.


## Validated incremental schedule (same day)

2026-10-29: After PR #2273 merged, database migration `wow_ncaaf_private_research_outcomes_daily_cron_20261029` applied. `pg_cron` job **#17**, `wow-v17-ncaaf-research-outcomes-daily`, schedule `45 14 * * *` UTC, active, created under database operator role `postgres`.

- The source acquisition workflow already runs at 13:35 UTC. This DB-local 14:45 UTC research normalizer provides its own incremental post-ingestion follow-up without exposing bearer credentials or requiring GitHub Actions service_role secrets.
- `wow_prop_history.refresh_next_ncaaf_outcome_week()` checks only CFBD `/games/players` snapshots that are `AVAILABLE`, `can_execute=false` and not yet normalized by the exact source snapshot id.
- At most **one pending week per scheduled invocation**; the strict `normalize_ncaaf_player_week` owns all data, identity, chronology, source-hash and row-count integrity. A hold is an actual SQL job failure, not silently counted as a success.
- An independent operator canary returned `NO_PENDING_PLAYER_WEEKS`, `new_outcome_rows=0`, all authority flags false.
- Readback: `anon` and `authenticated` roles do not have EXECUTE on the refresh function; `service_role` does. It is in a private, non-exposed schema and is `SECURITY INVOKER`.
- **The cron registration and no-pending manual canary are verified; the first future automatic cron execution is not yet observed.** Check `cron.job_run_details` following the next scheduled slot.

No new player data or probabilities were fabricated. The real-data 205,144-row acceptance remains unchanged and non-executable.
