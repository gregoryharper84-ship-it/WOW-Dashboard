# NBA/WNBA maintenance response contract

- **Date:** 2026-11-15
- **Typed reason:** `BASKETBALL_MODEL_MAINTENANCE_FAILED` (existing canonical code; no new failure code).
- **Mechanism:** The same typed code is emitted at two different boundaries. An exception escaping the authenticated HTTP wrapper returns a fixed HTTP 500 body with status/stage/code and false execution/promotion flags. An exception caught inside the per-sport loop instead becomes a `BLOCKED` item inside the normal maintenance response's `rows` array, with per-sport context and optional `error_type`/`hydration_blocker`. Documentation that described only the wrapper response hid the per-sport shape from clients.
- **Detection point:** CodeRabbit review on PR #2560 identified the mismatch between the wrapper's fixed 500 and the per-sport `_blocked(...)` result.
- **Fix:** Document both response shapes and require clients to inspect per-sport `rows` as well as the HTTP-level response. Neither result indicates model readiness, certification, promotion or execution.
- **Prevention artifact:** `artifacts/wow-engine/docs/failure_codes.md` entry for `BASKETBALL_MODEL_MAINTENANCE_FAILED`; source contract in `artifacts/wow-engine/v17/basketball_model_maintenance.py`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2560
