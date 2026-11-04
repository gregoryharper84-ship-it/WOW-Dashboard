# NCAAB schedule identity gap — 2026-11-04

- **date:** 2026-11-04
- **typed_reason:** NCAAB_PROVIDER_TEAM_ALIAS_MISSING / EVENT_IDENTITY_UNRESOLVED
- **mechanism:** The Daily cross-sport discovery handoff canonicalized NBA, NCAAF and NHL but returned NCAAB ESPN aliases unchanged. The identity purge runs before model routing, so NCAAB rows could not reach the explicit fitted-model capability hold.
- **detection point:** The first full-slate NCAAB row reconciliation should have asserted distinct identity-resolved vs identity-unresolved counts and a tested canonical handoff, not merely that events were discovered.
- **fix:** Add a fail-closed NCAAB SportsDataverse MBB schedule resolver requiring exact ESPN home/away IDs, timezone-aware start (within 30 minutes), time_valid, and one matching game ID; wire it into Daily canonicalization. This does not register or certify a model.
- **prevention artifact:** `artifacts/wow-engine/tests/test_v17_ncaab_event_identity.py` and NCAAB handoff tests in `test_v17_cross_sport_parity_repair.py`, including negative missing/ambiguous/source/invalid-time cases.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/tree/fix/v17-ncaab-schedule-identity-20261104
- **proof boundary:** Local isolated resolver tests 17/17; repository CI, protected merge, production live SHA, persisted reconciliation and coverage delta are not yet verified. Data-source 2027 season availability is not presumed.

## Review-discovered failure class (same incident)

- **mechanism:** A season-level source outage multiplied into a 20-second HTTP request for each NCAAB event, delaying shared cross-sport scans; clearing official_event_id before retaining the discovery alias also discarded evidence needed for diagnostics.
- **detection point:** The first negative-source and alias-missing end-to-end handoff checks, before a protected PR enters semantic review.
- **fix:** A 30-second typed per-season acquisition-failure cache (not a model fallback), provider alias retention on every identity hold, and documented-schema fixture coverage.
- **prevention artifact:** `test_unavailable_source_is_cached_per_season_during_shared_scan`, `test_success_clears_expired_negative_cache`, `test_ncaab_missing_team_alias_preserves_original_discovery_id`, `test_ncaab_canonical_identity_does_not_create_model_capability`; all exact-head CI verification remains required.
