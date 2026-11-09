# NCAAF candidate-training pagination integrity (2026-11-07)

- **date:** 2026-11-07
- **typed_reason:** `NCAAF_TRAINING_PAGINATION_INVALID`
- **mechanism:** `ncaaf_candidate_training_runner._paged` used offset-only PostgREST range reads without unique total ordering, exact primary-key projection, or duplicate identity rejection. Once the existing 4,288 NCAAF training games are paired with genuine pregame feature rows, moving page boundaries could silently skip/repeat training samples.
- **detection point:** At the candidate-trainer page-read contract, before any fitted artifact is produced; the NCAAF feature compiler already had this check, but the downstream candidate trainer did not.
- **fix:** Order both training-game and training-feature pages by their exact primary key, include the key in sparse selects, and fail closed on missing/nonstring/duplicated identities or malformed pages.
- **prevention artifact:** `artifacts/wow-engine/test_ncaaf_candidate_training_runner.py` tests ordered multi-page projections, exact page-boundary duplicates and invalid IDs.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2500

**Scope:** Research-only candidate corpus integrity; no model probabilities, calibration thresholds, publication, execution or production Supabase mutations. Validation data at diagnosis: 4,288 settled NCAAF training games, zero `wow_ncaaf_training_features`, zero fitted model artifacts. Those missing sources remain an independent blocker, and the fix does not qualify a model.
