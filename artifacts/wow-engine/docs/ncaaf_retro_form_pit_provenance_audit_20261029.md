# WOW V17 NCAAF retrospective player form — point-in-time provenance audit

**Date:** October 29, 2026. **Database:** `wow-engine-validation`. **Role:** research only. This is **not** a historical pregame dataset.

## Real-data readback (from private Supabase validation schema)

The production CFBD boxscore recovery and normalization produced 205,144 settled player-game stat outcomes across 3,333 games and seven stat families, all from sources actually first retrieved on 2026-10-29 approximately 15:20–15:24 UTC.

The private view `wow_prop_history.ncaaf_retrospective_prior5_research_v1` reconstructs the **mean of up to five earlier games** for the same player/stat/season, requiring at least three prior stat observations and strict earlier kickoff order. It labels each resulting row as **retrospective** and retains the true maximum source retrieval timestamp for the prior outcomes.

| NCAAF season | Retrospective player-form rows with >=3 prior appearances | Prior source verifiably observed before target kickoff | Invalid chronology | Rows asserting probability/training/betting approval |
| --- | ---: | ---: | ---: | ---: |
| 2023 | 28,677 | 0 | 0 | 0 |
| 2024 | 28,680 | 0 | 0 | 0 |
| 2025 | 29,957 | 0 | 0 | 0 |
| 2026 (season-to-date) | 12,019 | 0 | 0 | 0 |
| **Total** | **99,333** | **0** | **0** | **0** |

These are descriptive *lookback cohorts*, **not verified pregame modeling features**. Event time before a target event is insufficient to prove source data availability: every CFBD boxscore in this recovery was acquired retrospectively on October 29, 2026. `point_in_time_verified` is hard-coded FALSE and `verified_feature_as_of` is NULL, even if a later source is obtained for a prior event.

## Data access / integrity

The view uses `security_invoker=true`, the underlying RLS table, and exclusive `service_role` SELECT. `anon` and `authenticated` have no SELECT. No public RPC or Data API role can use it. `can_execute=false`, `model_training_eligible=false`, `probability_publishable=false` for all view rows; no bookmaker or historical line is generated.

The source SQL is `artifacts/wow-engine/ncaaf_retrospective_prior5_research_v1.sql`; the tests are in `test_ncaaf_retrospective_prior5_research_contract.py`. Connected Supabase migration `wow_ncaaf_retrospective_unverified_prior5_view_20261029` was applied and independently validated prior to this PR.

## Required steps for fitted-model readiness

1. Establish whether the provider's official historical *publication/availability* timestamps can be independently verified. A replay of information backfilled in 2026 does not establish true historical pregame availability.
2. For **future** games, record each approved feature with actual `retrieved_at` and `feature_as_of < event_start_time`. Freeze pregame schedules, lineup context, injuries/weather or other permitted sporting inputs before kickoff. Settled boxscore outcomes remain strictly postgame labels.
3. Keep market snapshots separate: exact line, sportsbook, direction, capture time and vendor rights; no backfilled current chart threshold is a historical line.
4. Use chronological untouched training/validation/holdout partitions only once every contributing pregame feature has verified as-of provenance. Later use a certified NCAAF fitted specialist, calibration/uncertainty bounds, independent replay and the `V17_TERMINAL_REDUCER`. No attempt to train/probabilistically publish from this view is authorized.

This audit documents a genuine evidence gap, not MODEL_UNAVAILABLE or an outage of the recovered raw sporting results. `can_execute=false`.
