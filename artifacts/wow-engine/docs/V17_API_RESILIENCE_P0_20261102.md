# WOW V17 — API resilience P0 / quota-safe market acquisition

**Objective:** V17-API-RESILIENCE-P0
**Status:** WORKING_NOT_COMPLETE until CI, deploy, and independent live acceptance
**Owner:** Engineering/AMRT implementation; SIRT challenge; Independent Verification acceptance
**Scope:** Acquisition only; no fitting, calibration, weights, ranking, terminal override or execution.
**Safety:** `can_execute=false`; `V17_TERMINAL_REDUCER` remains authoritative.

## Repair in this change

1. Normalize *documented* The Odds API `OUT_OF_USAGE_CREDITS` (HTTP 422)
   to the pre-existing `FAILED: quota exhausted` value. This allows the
   pre-existing `get_h2h_odds()` TheRundown failover to run for every
   mapped supported sport. Other HTTP 422 errors (e.g., `INVALID_MARKET`)
   are not mislabeled as exhausted and do not rotate through other keys.
2. When all configured Odds API key tiers are proactively skipped by
   shared PostgreSQL remaining=0 snapshots, return the same canonical
   exhausted status so the available TheRundown fallback is tried without
   wasting another Odds API request.
3. Classify HTTP 429 as temporary rate limiting, *not* monthly exhaustion.
   Keep that failure distinct; bounded scheduling/backoff belongs to
   provider-specific retry policy, not this moneyline fallback patch.
4. For V17's existing per-scan paid-call budget, protect the final-refresh
   reserve from discovery **and** initial enrichment. Consume paid
   discovery reservations atomically across concurrent callers in the
   same scan. Unknown explicit source-mode strings fail to FREE_CORE,
   not to a potentially billable HYBRID configuration.

## Boundaries and explicit gaps

- The existing TheRundown fallback is **moneyline-only**; it is not a
  substitute for exact player-prop boards, injuries, player workloads,
  official canonical IDs, fitted models, or guaranteed market coverage.
- `PaidCallBudget` is scan-local; cross-process/cross-deployment provider
  quotas require the existing shared DB and further integration. Do not
  advertise cross-worker hard enforcement from this patch.
- Existing source permission requirements remain binding. No new website
  scraping, hidden API extraction, login automation, or rights assumptions.
- Missing optional market data must not erase a valid independent sporting
  probability. Missing required sporting inputs must preserve their typed
  blockers. No unsupported line, side, price, or probability substitution.
- No production settings, credentials, subscriptions, or model artifacts
  are changed by this PR.

## Tests added

- Provider-native 422 exhausted -> actual moneyline fallback.
- Exhausted cross-worker key-tier snapshot -> zero primary HTTP calls and
  actual fallback.
- Non-quota 422 -> no key cycling or fallback.
- Rate-limited 429 -> no false monthly-exhaustion classification.
- Discovery cannot consume final-refresh reserve.
- Parallel same-scan requests cannot exceed reserved per-scan budget.
- Invalid explicit source mode triggers zero-paid-call behavior.
- Actual `_before_paid_call` acquisition boundary honors the reserve.

## Independent acceptance (not established by source changes)

1. Exact-head CI passes existing and newly added regression suites.
2. SIRT reviews semantics, code, source rights, and regressions independently.
3. Authorized release promotes the exact reviewed SHA.
4. In production, demonstrate safe 422/quota-snapshot failover, no unexpected
   paid calls, correct source provenance and event matching, and end-to-end
   Action receipts on multiple supported sports.
5. Compare input coverage/freshness and model output invariance at identical
   pregame cutoffs, then verify forecast Brier/log loss and lower-bound
   reliability on forward cohorts where fallback changes fitted inputs.
6. Confirm `can_execute=false` and no market failure is reclassified as
   `MODEL_UNAVAILABLE`.

## Follow-on architecture

Prioritize a unified feature-level source registry and legal/permission-aware
adapters for independent public schedules, statistics, official outcomes,
injury/status and exact prop markets. Reuse the existing PostgreSQL quota
ledger and durable market caches; enforce provider capabilities, sport-specific
freshness, centralized single-flight where possible, per-provider circuits,
and source receipts. Do not introduce a new paid data vendor or bypass model
requirements to claim uninterrupted picks.
