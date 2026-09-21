# LLP V17.1 Sharpness Challenger — Replay Evidence

Date: 2026-09-21
Environment: `wow-engine-validation` Supabase project
Mode: READ-ONLY REPLAY / SHADOW
Production mutation: none
can_execute: false

## Objective

Test whether a single universal `calibrated_lower_bound DESC` ordering is optimal for the user objective “most likely ML winner,” while preserving lower bound as a real uncertainty measure and preserving all V17 structural safety/governance requirements.

The challenger score is:

```text
score(lambda) = calibrated_probability
                - lambda * (calibrated_probability - calibrated_lower_bound)
```

where:

```text
lambda=0   => pure calibrated-probability ordering
lambda=1   => pure calibrated-lower-bound ordering
```

Intermediate lambda values are shadow-only research hypotheses.

---

## Replay A — immutable event prediction ledger

Source: `wow_event_predictions` joined to `wow_event_outcomes`.
Window: settled MLB slates from 2026-09-03 through 2026-09-20.

Method:

1. Require settled, non-void outcome.
2. Require complete calibrated home/away point probabilities and lower bounds.
3. Deduplicate repeated scans by `(requested_slate_date, official_event_id)`, taking the latest immutable pregame model row.
4. Choose the model-favored side by the larger calibrated probability.
5. Rank the slate by the challenger score.
6. Evaluate Top-1 winner rate, Top-3 hit rate, Top-5 hit rate, and selected Top-3 proper scoring metrics.

Sample:

```text
settled slates: 10
unique deduplicated events: 101
```

| lambda | interpretation | Top-1 win rate | Top-3 hit rate | Top-5 hit rate | Top-3 Brier | Top-3 log loss |
|---:|---|---:|---:|---:|---:|---:|
| 0.00 | calibrated probability | **90.0%** | 73.33% | 67.35% | 0.22814 | 0.64925 |
| 0.25 | light uncertainty penalty | 80.0% | **76.67%** | **69.39%** | **0.22778** | **0.64855** |
| 0.50 | midpoint | 80.0% | 73.33% | **69.39%** | 0.23113 | 0.65527 |
| 0.75 | heavy uncertainty penalty | 80.0% | 73.33% | **69.39%** | 0.23113 | 0.65527 |
| 1.00 | pure lower-bound ordering | 80.0% | 73.33% | **69.39%** | 0.23113 | 0.65527 |

Directional observations:

- Pure calibrated-probability ordering won Top-1 on 9/10 slates versus 8/10 for pure lower-bound ordering.
- A light penalty (`lambda=0.25`) produced the best Top-3 hit rate and best selected Top-3 Brier/log loss in this small replay.
- The sample is too small to select a production lambda.

Example rank-order change:

```text
2026-09-20
calibrated-probability Top-1: Cleveland Guardians
P(win): 55.43%
LB: 39.83%
result: WIN

lower-bound Top-1: Texas Rangers
P(win): 53.81%
LB: 51.36%
result: LOSS
```

This example is evidence that the two ranking objectives can disagree; it is not by itself proof that one ranking rule is superior.

---

## Replay B — corrected forward-shadow ledger

Source: immutable `wow_mlb_forward_shadow_grades`, `wow_mlb_forward_shadow_events`, and `wow_mlb_forward_score_snapshots`.
Window: 2026-08-28 through 2026-09-20.

Sample:

```text
graded predictions: 322
settled slates: 24
```

### Side-identity correction

During replay, a data-contract issue was detected: `wow_mlb_forward_shadow_grades.prediction_probability` can be home-oriented even when `predicted_side='AWAY'`.

Therefore every replay row must bind the probability and lower bound to the same selected side directly from the immutable score snapshot:

```text
HOME -> calibrated_home_probability / home_lower_bound
AWAY -> calibrated_away_probability / away_lower_bound
```

This correction removed impossible negative point-to-lower-bound widths. The uncorrected replay is not valid evidence and is superseded by the results below.

### Corrected ranking replay

| lambda | Top-1 | Top-3 | Top-5 | Top-3 Brier | Top-3 log loss |
|---:|---:|---:|---:|---:|---:|
| 0.00 | 70.83% | 65.28% | 63.87% | 0.23581 | 0.66463 |
| 0.25 | 70.83% | 68.06% | **68.07%** | 0.23493 | 0.66288 |
| 0.50 | 70.83% | 68.06% | 65.55% | 0.23589 | 0.66481 |
| 0.75 | 70.83% | 68.06% | 63.87% | 0.23589 | 0.66481 |
| 1.00 | 70.83% | 68.06% | 63.87% | 0.23589 | 0.66481 |

A 0.05-grid scan found the strongest tested region around `lambda=0.25–0.40`:

```text
lambda=0.25
Top-3 68.06%
Top-5 68.07%
Brier  0.23493
LogLoss 0.66288

lambda=0.30
Top-3 68.06%
Top-5 67.23%
Brier  0.23474   # best tested Top-3 Brier in this region
LogLoss 0.66251 # best tested Top-3 log loss in this region

lambda=0.40
Top-3 69.44%   # best tested Top-3 hit rate
Top-5 66.39%
Brier  0.23523
LogLoss 0.66348
```

Interpretation:

- Top-1 was unchanged across the tested ranking strategies.
- A light uncertainty penalty improved deeper-list discrimination versus pure point-probability ordering on this sample.
- A light penalty also improved Top-5 and proper scoring versus pure lower-bound ordering.
- Pure lower-bound ordering did not show a universal advantage.
- No production lambda is selected by this replay.

---

## Lower-bound width diagnostic

Using the corrected selected-side probability and selected-side lower bound, the forward rows were segmented by:

```text
width = calibrated_probability - calibrated_lower_bound
```

| Width band | n | Mean P | Mean LB | Observed hit rate | Brier | Log loss |
|---|---:|---:|---:|---:|---:|---:|
| 0–2 pp | 35 | 54.63% | 54.05% | 80.00% | 0.22697 | 0.64693 |
| 2–5 pp | 52 | 52.79% | 50.17% | 55.77% | 0.24793 | 0.68897 |
| 5–10 pp | 74 | 52.68% | 45.64% | 56.76% | 0.24742 | 0.68799 |
| 10–15 pp | 33 | 54.09% | 41.58% | 75.76% | 0.22684 | 0.64671 |
| 15+ pp | 124 | 51.77% | 33.91% | 46.77% | 0.25142 | 0.69599 |

The relationship is not monotonic.

Important implications:

1. Very large gaps (`15+ pp`) are associated with materially worse outcomes and poorer proper scoring in this sample.
2. Medium gaps are not uniformly bad; the `10–15 pp` cohort performed unusually well.
3. Therefore a universal rule such as “larger lower-bound gap always means weaker candidate” is not supported by this sample.
4. The lower-bound mechanism appears cohort-like/quantized in the forward data; several exact lower-bound values recur across many candidates. That deserves calibration/bounds-method review before interpreting every gap as game-specific uncertainty.

This is exactly why a learned uncertainty penalty should be tested by cohort rather than hardcoded globally.

### Status-certainty limitation

The forward-shadow sample is not suitable for a confirmed-vs-projected lineup comparison:

```text
PASS_RESEARCH_BOUND + lineup NOT_YET_AVAILABLE: 317 rows
PASS_RESEARCH_BOUND + lineup CONFIRMED:          1 row
```

No lineup-confirmation penalty or promotion decision should be inferred from this dataset.

---

## Rank-eligibility diagnostic

Among event-ledger rows in the replay window with complete calibrated home/away point probabilities and lower bounds:

```text
rank_eligibility_status=FAIL: 804
rank_eligibility_status=PASS:   7
```

Most common recorded reasons on failed rows:

```text
MODEL_NOT_READY                         804
PROBABILITY_AUDIT_NOT_PASS              804
CALIBRATION_HEALTH_NOT_PASS             803
SCORING_EVIDENCE_SNAPSHOT_INCOMPLETE    803
SELECTED_PARTICIPANT_UNRESOLVED         585
MODEL_STALE_OR_INVALID                  327
MARKET_ROLE_NOT_LOCKED                  219
SELECTED_SIDE_ROLE_UNRESOLVED           219
MARKET_SNAPSHOT_MISSING                 219
MARKET_ROLE_CONSENSUS_FAIL              219
MODEL_PROBABILITY_LEDGER_INCOMPLETE     218
```

These rows span changing governance/instrumentation states. They are diagnostic evidence only. They do not authorize bypassing any current hard blocker.

The important architectural distinction is:

```text
numeric sporting probability package exists
!=
current publication/rank-eligibility chain is complete
```

---

## What this evidence does NOT justify

It does not justify:

- replacing the incumbent production rank metric;
- selecting a production lambda from this replay;
- weakening hard identity/status/model-output blockers;
- converting a historical instrumentation gap into a soft blocker automatically;
- blending sportsbook implied probability into the fitted forecast;
- treating `15+ pp` as a universal rejection rule;
- treating the unusually strong `10–15 pp` cohort as proof that uncertainty does not matter;
- automatic promotion.

## Next evidence required

1. Run counterexample review on every material rank-order change between `lambda=1` and the light-penalty challenger.
2. Extend the forward sample and evaluate stability of the `lambda=0.25–0.40` region.
3. Segment by probability band, bound cohort, favorite/underdog role, forecast-to-start time, starter certainty, and any genuinely populated status-certainty dimensions.
4. Audit the bounds implementation and uncertainty provenance to distinguish candidate-specific residual uncertainty from cohort-level bound assignment.
5. Add market-divergence recheck diagnostics without blending market probability.
6. Add event sensitivity attribution for starter/lineup/bullpen/weather counterfactuals where certified model inputs support them.
7. Require historical replay, counterexample review, untouched holdout/forward evidence, and regression before any Class C promotion recommendation.

## Current conclusion

The V17.1 sharpness challenger is **earned for continued shadow testing**.

The combined evidence does not support assuming that either pure calibrated probability or pure calibrated lower bound is universally optimal for every leaderboard objective. The strongest current hypothesis is a **light, evidence-learned uncertainty penalty**, with the tested region around `lambda=0.25–0.40` deserving further shadow validation.

Production ranking behavior remains unchanged.
`V17_TERMINAL_REDUCER` remains sole global terminal authority.
`can_execute=false` remains invariant.
