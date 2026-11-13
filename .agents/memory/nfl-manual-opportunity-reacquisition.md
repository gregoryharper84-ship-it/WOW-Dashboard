# NFL manual prop opportunity ledger source reacquisition — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** `OPPORTUNITY_LEDGER_NOT_READY` / `NFL_PROP_SOURCE_UNAVAILABLE`
- **mechanism:** Caller-supplied recent stat rows may be accepted as evidence yet their opportunity ledger remains NOT_READY. The runtime only ran its trusted NFL hydrator when the entire evidence payload was absent. It then rejected all four Nabers/Daniels directions before fitted inference. The same branch also mislabeled any auto-hydrated NFL receipt as the MLB provider. Daily discovery is a separate missing producer, already owned by PR #2390.
- **detection_point:** Before manual /score-pick-request submission and in the first immutable INGESTED row receipt; no model invocation should be inferred from backend health.
- **fix:** For existing fitted NFL stat routes only, request a fresh independent NFL ESPN/nflverse bundle when the caller's opportunity status is not ready; preserve exact event, player, market and line, use a fresh acquisition timestamp, and label the actual registered provider. Fail closed if source retrieval, canonical event binding or pregame validation fails. No automatic retry of closed immutable rows.
- **prevention_artifact:** `artifacts/wow-engine/test_nfl_opportunity_reacquisition.py` tests verified-source replacement, source unavailability, non-NFL isolation, model-unavailable preflight, exact line and non-execution. Daily discovery remains PR #2390.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/blob/fix/v17-nfl-opportunity-reacquire-20261112/artifacts/wow-engine/test_nfl_opportunity_reacquisition.py ; https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2390

**Evidence status:** Candidate patch authored; hosted exact-head checks, independent semantic acceptance, production route and Supabase persisted-row confirmation are not implied by source changes.
