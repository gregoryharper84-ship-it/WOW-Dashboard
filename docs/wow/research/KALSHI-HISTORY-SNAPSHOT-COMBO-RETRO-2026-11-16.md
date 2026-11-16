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
- A three-market position had a positive total payout but negative net economics:
  payout-positive and profitable are distinct outcomes.
- Three positions showed **zero settlement payout but positive total payout**.
  This can reflect sale/early exit or other payout accounting, but the mechanism
  and whether the original legs won are unverified without order history.
  Never classify these three as confirmed sporting wins.
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
- Total payout unequal to settlement payout must produce an explicit reconciliation flag; positive sales/proceeds are not settled leg success.
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


## Additional slip-construction research — independently checked structure examples

**This section is anonymized because the repository is public.** Underlying private
Kalshi settlement emails and personal order references remain in the authorized
account and are NOT copied into GitHub. Two contrasting construction types were
observed in settlement correspondence and cross-checked against official sporting
box scores; this does not authenticate pregame model quality or executable prices.

### Type A — SINGLE_PLAYER_SAME_GAME_MULTI_MARKET (settled positive example)
- Two distinct thresholds on one star player's one-game performance.
- The joint thesis is a shared usage/minutes/role environment, not a naive
  product of two unrelated success probabilities.
- Model the joint conditional distribution of the *exact* two validated stat
  families; common-cause injuries, minutes ceilings, foul trouble, blowout risk,
  usage and matchup are explicit. An unidentified numeric threshold (e.g.,
  "6+") must NOT be assigned a sport-specific stat type from a box score.
- A positive settlement is a single observational outcome, not certification.
  Preserve losing same-player pairs as counterexamples to survivorship bias.

### Type B — MULTI_PLAYER_SAME_GAME (settled negative example)
- Four player thresholds from one matchup, including multiple receivers from
  one team's passing attack and opposing skill-player performance.
- One secondary receiving-yardage target missed materially even as the team's
  leading receiver produced a strong yardage total. Touchdown count did not
  rescue a separate yardage threshold. This illustrates a *target allocation*
  failure mode, distinct from total game offense failing.
- Model the shared passing-volume distribution **and** conditional target shares,
  routes/snaps, defensive matchups, injuries, game script, pace, and player-specific
  usage. A shared high-volume game can increase opportunities while within-team
  receivers compete for finite targets; the net statistical sign is not known
  without fitted/validated dependence evidence.
- Verify exact fourth-leg stat family before labeling its hit or miss, and
  decompose card outcome by every market, not only the named star.

### Universal candidate changes (research-only)
1. Make construction topology a first-class diagnostic:
   `SINGLE_PLAYER_SAME_GAME_MULTI_MARKET`, `MULTI_PLAYER_SAME_GAME`,
   and `MULTI_EVENT_COMBINATION`; require exact stable event/player/team,
   market ID and stat family for engine-grade classification.
2. A same-team, same-event pair of `RECEIVING_YARDS` markets must emit
   `SAME_TEAM_RECEIVING_OPPORTUNITY_COMPETITION`. Do not assume negative
   correlation or arbitrarily shrink probabilities.
3. Preserve a ranked two-leg baseline; for a four-plus-leg exploratory
   construction, create a time-correct two-leg alternative and score both on
   fitted joint probability, fees/quote economics, fragility and exposure.
   Existing production structure ceilings are NOT modified.
4. Preserve both winning and losing examples; require chronological holdout
   evidence before changing default card size, model parameters or qualification.
5. Never infer exact underlying stat families or cash profit from a notification's
   abbreviated displayed thresholds and settlement contract count.

`artifacts/wow-engine/v17/research/kalshi_slip_construction_taxonomy.py`
and its corresponding tests implement fail-closed structural topology diagnostics.
The module sets joint probability to null, calibration ineligible, publishability
false, and can_execute false. It is not wired to V17 production scoring.

### Still missing for model training
Exact full Kalshi market IDs/statistic names, entry timestamps/prices/fees, immutable
pregame probability receipts, independent source review and enough matched
historical outcomes to establish chronology-respecting lift. Actual engine-grade
labels remain unverified even when a settlement notification and public box score
agree at the surface level.


## November 16 implementation continuation: quote-aware acceptance harness

Kalshi publicly confirms that a combo is a separately quoted RFQ market and
settles as the product of constituent contract values, sometimes including
intermediate scalar values for DNP/protected player markets. Those are not
equivalent to multiplying displayed implied probabilities or assuming that
every positive total payout represents a winning sporting settlement.

The added research-only `kalshi_structure_quote_replay.py` accepts **only**
explicitly structured rows with:
- stable quote and underlying market evidence and exact component stat family;
- immutable fitted-model prediction receipt and fitted artifact ID, pregame
  calibrated joint probability and lower bound;
- chronological fit cutoff, prediction timestamp, quote timestamp, event
  start, official settlement timestamp and settlement evidence;
- entry quote, observed fee and settlement value in cents per contract;
- construction style, comparator group and independent thesis cluster.

It *rejects* unknown market families, unverified research source status,
candidate-only calibration, missing fitted receipts, missing prices and
impossible timestamps. Scalar settlement contributes to **economics only**,
never a false binary win/loss grade. It reports descriptive Brier/log loss
and structure-level ROI only for supplied legitimate pregame rows, and
always returns `comparative_lift_established=false`,
`probability_publishable=false`, `can_execute=false`.
Even a completed input sheet does not authenticate upstream evidence:
source signatures, independent sample provenance, nonoverlapping holdout,
actual fills and independent signoff are external acceptance requirements.

### Observed methodology counterexamples
Review of existing private settlement notices identified both successful
and unsuccessful single-player, two-market construction examples across
different games. This invalidates the idea that *single-player two-leg* is
an automatic winning formula. It remains a candidate structure for
joint-calibrated pregame comparisons. No specific private positions or
Gmail message links are committed to this public document.

### Kalshi market label ambiguity
The short label `6+` on a player does **not** specify whether it refers
to assists, three-pointers or another counting statistic. The same game
can have multiple active thresholds for the same player. An official box
score can rule out interpretations but cannot prove the actual selected
component without its original `market_id` and full market rules. Market
identification must be **fail closed**, never chosen post-event to match
the desired win/loss result.

### Follow-on release acceptance
- exact-head hosted unit/negative tests and CodeQL pass;
- independent review of code and no-hindsight probability/quote policy;
- authentic, time-correct user fill and component records (not email-only);
- replay style comparisons across nonoverlapping genuine holdout cohorts,
  account for duplicate theses, event/player nesting and execution costs;
- no unapproved production import, default-threshold change or live trade.


## Nested-threshold follow-up: two markets need not be two independent claims

A same-player two-market structure must be divided into:
- **Complementary different-stat** examples: points and assists, or points and
  rebounds. Shared minutes/usage induce dependence; a fitted joint
  distribution is needed.
- **Potentially nested same-stat** examples: two numerical cutoffs for the
  identical player/stat family. These are NOT automatically confirmed as
  mathematical nesting from a short label such as `3+` and `4+`.
- **Verified nested thresholds** require the same player/event, exact stat,
  direction, period and settlement rule identity, with both exact numeric
  cutoffs. If both are GE events on the same played/sporting outcome, success
  at the stricter threshold implies success at the easier threshold. But the
  Kalshi combo remains a separately priced RFQ market, and scalar/DNP or
  protected settlement handling must be evaluated under the exact terms.

The classifier now emits potential vs verified nesting counters and
`COMPLEMENTARY_STATS_SHARED_PLAYER_ROLE` separately. It **does not**
invent any statistic identity, numeric correlation, value, or probability,
and does not change production structure limits. A counterfactual
comparison between nested and complementary strategies requires
recorded pregame quotes, market IDs and exact independent settlement
rules, not just email results.
