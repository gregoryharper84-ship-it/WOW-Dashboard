# SIRT Independent Assurance Contract — V17 Decision Intelligence

Date: 2026-10-08
State: PROPOSED / NOT EXECUTED
Authority: SIRT audits, diagnoses and detects; Engineering implements; Independent Verification verifies; V17_TERMINAL_REDUCER alone governs final publication.

## Scope and independence

Audit a read-only market-value + exposure + outcome-measurement extension without accepting assertions of correctness from the producing components. The assurance service must not be part of the sporting-specialist inference path, must not overwrite model probabilities and must not change terminal reducer output. The operating invariant remains can_execute=false.

SIRT may raise incidents and recommend safety holds through existing authorized governance; this specification does not grant unilateral terminal publication authority.

## Evidence chains to verify

1. **Identity:** canonical sport/event, side, period, stat, exact line and settlement match between prediction receipt and quote; provider aliases are resolved with durable evidence.
2. **Time:** source observed/available timestamps, model_timestamp and quote time were known pregame. An out-of-time quote cannot be treated as a valid executable offer.
3. **Probability:** controlling specialist, certified artifact, calibration/lower bound, valid numerical package and final reducer result are independently traceable. Underlying sporting predictions are read-only.
4. **Economics:** sign and units of odds conversion, raw break-even, same-book de-vig, unit EV, push/tie/void/fees assumptions, rounding and missing bid/ask are explicitly testable. A positive point gap may coexist with a non-positive conservative gap and must not be presented as robust value.
5. **Exposure:** duplicated theses counted once for model evidence but every exposure instance accounted for; same-game/participant/scenario dependence and unknown correlation never silently become independence.
6. **Outcomes:** exact immutable pregame prediction matched to verified settlement; closing prices, live updates and postgame facts never backfill pregame knowledge.

## SIRT fault-injection matrix

| Adversarial fixture | Required behavior |
|---|---|
| Sporting receipt missing/invalid or not display-authorized | No value conclusion; exact upstream blocker remains |
| Model scorer unavailable but quote exists | No price-derived surrogate probability |
| Probability p outside [0,1], NaN/Inf, LB above p | Invalid package cannot produce value |
| Completed sporting receipt, market feed 429/403/5xx | Preserve sporting probability; market research unavailable with original source failure |
| Adjacent prop line or reversed direction | No exact-line comparison |
| Same price with mismatched event, period, selection or settlement | No value comparison |
| Stale, started, suspended or non-executable quote | No current-executable claim |
| Cross-book sides joined for de-vig | Reject fabricated same-book fair probability |
| PrizePicks missing Power/Flex/offer payout or valid joint distribution | No parlay EV; no fictional standalone leg odds |
| Correlation/portfolio inputs absent | UNKNOWN/NOT_ASSESSED, never 0% dependence |
| Duplicate thesis on three hypothetical slips | One model thesis; three exposure instances |
| Postgame grading on changed line/direction | Model attribution unavailable for mismatched thesis |
| Any downstream research service timeout | Sporting lane remains available; no terminal downgrade |
| New component tries to write probability, rank status, order or bankroll | Must be impossible by schema/permission/test |
| Concurrent/retried jobs or broken persistence | Idempotent unique receipts; no double counting; no false completion |

For implementation, reuse canonical typed failures from docs/failure_codes.md; any additional status must be proposed and registered with its owning gate and semantics in the same code change.

## Monitoring and lifecycle

- Track exact-match rate, percent of usable quote snapshots, freshness distribution, market provider timeout/error rates, analysis receipt completion, duplicate analyses, unsupported payout cases, correlation unknown rate and production version.
- Track Brier/log loss and calibrated lower-bound reliability only on verified immutable forecasts, by meaningful sport/stat/model/version cohorts. Do not set automatic qualification thresholds from thin samples.
- Track realized ROI/CLV/expected return separately from sporting calibration. CLV is a diagnostic, not a guarantee of market edge, profitability or model authority.
- New alert thresholds must cite baseline measurements and reviewer approval; monitoring alone may not revoke/publish a prediction.
- Correlate request_id -> candidate_id -> prediction_id -> market_snapshot_id -> analysis_id -> terminal_status through logs without secrets, private account numbers or tokens.
- Every independent check produces an immutable receipt: fixture ID, exact source/head/deployed SHA, environment, timestamp, observed behavior, test expectation, verifier identity, result, blocker and trace.

## Release prohibition and closure definition

A document or local-fixture pass is not production verification. For any activated deployment require:
1. Verified STANDARD_OPERATOR gate if the owning backlog requires it; active P0 work unaffected.
2. Pinned exact-head CI and complete QA matrix with replay fixtures and negative tests.
3. An independent reviewer who did not implement the change and has appropriate repository permission.
4. Production revision identity, actual endpoint/worker/read-only canary receipts, drift and failure monitors, rollback proof.
5. No probability mutation, no terminal override, and can_execute=false confirmed end-to-end.

Until then: DESIGN_ONLY or SHADOW_UNVERIFIED. Do not mark FIXED_VERIFIED.