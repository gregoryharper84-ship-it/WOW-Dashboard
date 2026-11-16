# Kalshi history snapshot: structure-versus-economics research

Status: **OBSERVED SNAPSHOT / NOT DATE-VERIFIED / NOT MODEL-QUALIFIED** (2026-11-16).
Lane: V17 `PRESERVE -> REFINE -> REGRESSION_CHECK`. This note does not publish personal order details.

## Evidence and limitations
A read-only browser capture of an authenticated Kalshi portfolio History screen exposed
eight completed combo positions with market counts and entry/payout economics, but not
the expanded constituent markets, stable position IDs, order timestamps, fee detail,
or settlement chronology. **Do not label these as all or only 2026-11-15 slips** until
the transaction-level history independently proves that date, and do not reconstruct
model probabilities or leg outcomes from retrospective results.

Snapshot-only observations:
- Four **two-market** positions had positive net returns (4/4 profitable).
- Four **three-or-more-market** positions were net-negative (0/4 profitable).
- A three-market position had a positive cash payout but negative net economics:
  payout-positive and profitable are distinct outcomes.
- The two groups each represented approximately half the observed entry cost. A
  positive group offset a negative group, leaving near-flat total snapshot economics.
- With only eight unlinked positions and unknown prices, sports, legs, and cross-slip
  dependencies, **no causal claim, statistical edge, certification, or leg-size
  threshold change is warranted**.

## PRESERVE
- Keep the existing two-leg default, maximum-three research structure and binding
  weakest-leg/joint-probability/cross-slip guards in
  `docs/wow/contracts/canonical/WOW-PATCH-2026-07-23-SLIP-CONSISTENCY.md`.
- Keep a separate economic return ledger from hit/settlement and price-as-confidence
  labels. Do not label nonzero gross payout as a profitable card.
- Preserve `can_execute=false` and `V17_TERMINAL_REDUCER`.

## REFINE (hypotheses, not automatic gates)
1. Add card-size performance slices by sport, market family, settlement/entry date,
   and exposure cluster, with net return, entry-weighted ROI, gross-payout ratio,
   realized full-hit count, count of partial payouts, and sample size.
2. For each losing >2-market card, find the first failed/voided underlying market,
   exact line, source-timestamped pregame probability and price, dependence factor,
   same-event shared failure, and conditional what-if after weakest-leg removal.
3. Compare every candidate against a time-correct two-market alternative, using
   executable prices, fees, uncertainty intervals and an independently fitted
   joint probability; no hindsight replacement or probability backfilling.
4. Audit cross-card duplicates and exposure concentration: observed eight cards
   do not imply eight independent sporting theses.
5. Keep market type/winner/spread/total/player-prop segments separate. Do not infer
   that one segment caused the observed larger-card losses without leg evidence.

## REGRESSION CHECKS / ACCEPTANCE
- A history row without a source-settled date must NOT become a daily cohort.
- A position without expanded legs cannot enter leg win-rate, calibration, or
  model evaluation. No backfilling pregame estimates from winning tickets.
- A gross payout smaller than entry remains a negative-economic result.
- Duplicate stable position IDs cannot inflate win rate or return.
- Same-game/participant/market legs must not be treated as independent.
- No card-size preference becomes a production rule from eight observations.
- Run against independently authenticated **November 15** fills and settlements,
  with stable IDs, original quoted entry prices, fees, timestamps, card construction,
  actual leg outcomes, and immutable WOW pregame prediction receipts; require
  independent research and release acceptance before effecting any production gate.

## Research utility
`artifacts/wow-engine/v17/research/kalshi_combo_snapshot_audit.py` gives a
fail-closed snapshot economics and missing-data report. It intentionally does not
ingest credentials, discover private account records, certify dates, infer leg outcomes,
or write production ledgers. Its test file is
`artifacts/wow-engine/v17/test_kalshi_combo_snapshot_audit.py`.
