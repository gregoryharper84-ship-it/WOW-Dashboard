# V17 Daily active-sport discovery and ID scope
date: 2026-11-06
typed_reason: DISCOVERY_BUDGET_EXHAUSTED; cross-sport event suppression by unscoped canonical ID
mechanism: Daily iterated the thirteen-sport discovery catalog in MLB-first order under an 8-second total source budget, making NBA/NFL/NHL/NCAAF susceptible to never-attempted states when earlier feeds consumed the budget. Daily also compared official_event_id without a sport scope against MLB canonical IDs, which could silently filter non-MLB rows with colliding league-owned IDs.
detection_point: Daily cross-sport discovery integration test and real full-slate acquisition-budget receipt (before any model routing or publication).
fix: Explicitly query NBA/NFL/NHL/NCAAF first, then every other cataloged sport; compare covered-event keys only as (sport, official_event_id).
prevention_artifact: artifacts/wow-engine/v17/test_daily_snapshot_runtime.py::test_daily_active_sports_get_first_discovery_budget; ::test_mlb_event_id_collision_never_hides_active_sports; existing full-catalog reconciliation expectation.
link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2498
status: protected P change proposed; main, Render, real route and Supabase verification not yet proven. No model or qualification promotion.
