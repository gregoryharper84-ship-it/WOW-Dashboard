# NCAAB schedule identity gap — 2026-11-04

- **date:** 2026-11-04
- **typed_reason:** NCAAB_PROVIDER_TEAM_ALIAS_MISSING / EVENT_IDENTITY_UNRESOLVED
- **mechanism:** The Daily cross-sport discovery handoff canonicalized NBA, NCAAF and NHL but returned NCAAB ESPN aliases unchanged. The identity purge runs before model routing, so NCAAB rows could not reach the explicit fitted-model capability hold.
- **detection point:** The first full-slate NCAAB row reconciliation should have asserted distinct identity-resolved vs identity-unresolved counts and a tested canonical handoff, not merely that events were discovered.
- **fix:** Add a fail-closed NCAAB SportsDataverse MBB schedule resolver requiring exact ESPN home/away IDs, timezone-aware start (within 30 minutes), time_valid, and one matching game ID; wire it into Daily canonicalization. This does not register or certify a model.
- **prevention artifact:** `artifacts/wow-engine/tests/test_v17_ncaab_event_identity.py` and NCAAB handoff tests in `test_v17_cross_sport_parity_repair.py`, including negative missing/ambiguous/source/invalid-time cases.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/tree/fix/v17-ncaab-schedule-identity-20261104
- **proof boundary:** Local isolated resolver tests 17/17; repository CI, protected merge, production live SHA, persisted reconciliation and coverage delta are not yet verified. Data-source 2027 season availability is not presumed.
