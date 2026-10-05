# NFL Moneyline Challenger #1342 — Full Retrospective Sequence Receipt

Date: 2026-10-05  
Change class: C — challenger research only  
Production probability behavior changed: **No**

## Executive result

The incumbent NFL moneyline model was audited from feature geometry through
calibration and publication uncertainty. The structural problem is systematic:
cumulative `home_prior_games` and `away_prior_games` are non-stationary and
far outside their fitted training range in 2026.

The research configuration that advances to true forward shadow is:

- stationary history signal: `prior_games >= 8` sufficiency flag;
- rolling source window: latest 8 prior games;
- recency weighting: 224-day half-life;
- base estimator: logistic regression;
- calibration: Platt scaling;
- research lower bound:
  `min(local Wilson-90% k=50, block-bootstrap q10)`;
- no sportsbook probability input;
- no production promotion;
- `can_execute=false`.

This package is not certified and is not allowed to replace the incumbent.

## Step 1 — Remove cumulative counters

Same train/calibration/validation split as the promoted artifact:

- train 2021–2023: n=787
- calibration 2024: n=285
- validation 2025: n=284

2025 holdout:

| Metric | Incumbent | Drop cumulative counts |
|---|---:|---:|
| Brier | 0.231161 | 0.226150 |
| Log loss | 0.652913 | 0.641336 |
| AUC | 0.655602 | 0.669458 |
| ECE-10 | 0.041981 | 0.057728 |

Paired bootstrap intervals for Brier and log-loss improvements crossed zero, so
Step 1 earned continuation but not a superiority claim.

## Step 2 — Stationary replacement tournament

Rolling temporal folds used test seasons 2023, 2024, and 2025 with the prior
season used as the calibration fold.

Weighted Brier leaders:

1. drop counts: 0.2293172
2. `games>=8` sufficiency: 0.2293175
3. sufficiency>=24: 0.2294281
4. cap8: 0.2294420
5. sufficiency>=16: 0.2294928
6. incumbent cumulative counts: 0.2308430

`games>=8` was retained because it is stationary, essentially tied with the
drop ablation on proper scoring, and preserves an interpretable sample-
sufficiency signal. It is not a cumulative experience proxy.

## Step 3 — Offseason / recency tournament

Using the stationary sufficiency representation, rolling temporal folds compared
flat recent-8 history, prior-season shrinkage, and exponential time decay.

Weighted results:

| Window | Brier | Log loss | AUC | ECE |
|---|---:|---:|---:|---:|
| half-life 224d | **0.2290030** | **0.6491672** | 0.663155 | 0.071653 |
| prior-season rho=.50 | 0.2292029 | 0.6496774 | 0.661278 | 0.061772 |
| prior-season rho=.75 | 0.2292208 | 0.6498082 | 0.660489 | 0.065937 |
| flat | 0.2293175 | 0.6500966 | 0.659365 | 0.060029 |
| half-life 112d | 0.2293568 | 0.6497834 | 0.663004 | 0.069989 |

The 224-day half-life won Brier/log loss over the rolling folds and advances.
The value is a retrospective challenger hyperparameter, not production config.

## Step 4 — Calibrator tournament

On the 224-day-decay structural challenger:

| Calibrator | Weighted Brier | Weighted log loss | Weighted ECE |
|---|---:|---:|---:|
| Platt | **0.2290030** | **0.6491672** | 0.071653 |
| beta | 0.2307962 | 0.6535920 | **0.060953** |
| temperature | 0.2319617 | 0.6554487 | 0.080686 |
| uncalibrated | 0.2341916 | 0.6626221 | 0.078573 |
| isotonic | 0.2382717 | 0.7230408 | 0.067444 |

Platt remains the point-probability calibrator. No calibration method wins by
theory alone; isotonic was unstable in these temporal samples.

## Step 5 — Lower-bound / decision-confidence tournament

The incumbent lower bound uses a flat global error margin derived from
`1.96*sqrt(Brier/n)`. That quantity is not treated here as an instance-level
confidence interval.

Retrospective rolling decision evaluation across 854 test decisions:

| Research lower bound | >=55% qualified | Qualified hit rate | Reliability-bin violations |
|---|---:|---:|---:|
| incumbent static margin | 402 | 66.92% | 1 |
| Venn-Abers-style lower | 503 | 67.20% | 3 |
| local Wilson-90%, k=50 | 238 | 72.27% | 0 |
| block bootstrap q10 | 214–265* | ~68–75%* | 0 |
| composite min(Wilson k50, bootstrap q10) | **173** | **77.46%** | **0** |
| composite min(Wilson k50, bootstrap q05) | 125 | 79.20% | 0 |

*Bootstrap counts vary modestly with the finite research replicate set.

The q10 composite advances because it explicitly requires both:
1. local empirical calibration reliability, and
2. parameter/model stability under chronological block bootstrap.

This is a conservative research decision-confidence bound, not a claimed
distribution-free confidence interval for the latent game probability.

## Step 6 — Context-family ablations

The structural challenger was tested before adding context.

### Prior-QB continuity

QB IDs exist for 2,848/2,848 normalized team-game summaries. Leakage-safe prior
QB-continuity features were derived only from completed prior games.

Weighted rolling metrics:

- structural base Brier: 0.2290030
- + prior-QB continuity Brier: 0.2306701
- structural base log loss: 0.6491672
- + prior-QB continuity log loss: 0.6526136
- structural base AUC: 0.663155
- + prior-QB continuity AUC: 0.653928

Result: **REJECTED_FOR_NOW**.

### Weather / roof

Historical roof is complete, but temperature/wind coverage varies materially by
season. Missingness was explicitly modeled rather than silently dropping games.

Weighted rolling metrics:

- structural base Brier: 0.2290030
- + weather/roof Brier: 0.2303551
- structural base log loss: 0.6491672
- + weather/roof log loss: 0.6518269
- structural base AUC: 0.663155
- + weather/roof AUC: 0.652739

Result: **REJECTED_FOR_NOW**.

### Injury / current-starter context

Historical injury and weekly-roster source files are preserved for 2021–2026,
but provenance is not uniform enough for a certified point-in-time replay:

- injury files 2021–2024 include `date_modified`;
- injury files 2025–2026 do not;
- weekly roster files are week-indexed but have no row-level capture timestamp;
- source assets themselves were bulk captured in September 2026.

Therefore injury/current-starter feature promotion is:

`BLOCKED_WITH_EXACT_REASON —
POINT_IN_TIME_INJURY_STARTER_PROVENANCE_NOT_UNIFORM_ACROSS_REPLAY_YEARS`

No revision-sensitive injury/roster feature is admitted to the challenger.

## True forward-shadow seed

The first true-forward event was seeded while still pregame:

- event: Atlanta Falcons @ New Orleans Saints
- official event: `2026_04_ATL_NO`
- provider event: `401872979`
- kickoff: `2026-10-06T00:15:00Z`

Challenger refit for the forward prediction:
- train: 2021–2024, n=1072
- calibration: 2025, n=284
- only history before 2026-10-05 used for target features

Research prediction:
- selected side: Atlanta
- selected calibrated probability: **58.1583%**
- local Wilson-90% k=50 lower: **36.7819%**
- block-bootstrap q10 lower: **40.6813%**
- composite q10 lower: **36.7819%**

The challenger therefore remains below the 55% publication floor as well.
This is shadow evidence only and must not be published or ranked.

The outcome cannot be graded before the event completes. Exact remaining
forward blocker:

`BLOCKED_WITH_EXACT_REASON —
TRUE_FORWARD_OUTCOME_NOT_YET_AVAILABLE_FOR_2026_04_ATL_NO`

## Governance decision

Retrospective sequence: **COMPLETE**  
True-forward cohort: **SEEDED / AWAITING OUTCOMES**  
Production promotion: **NOT AUTHORIZED**

No production scorer, fitted artifact, registry, calibration package, lower-
bound policy, publication threshold, or routing is modified by this experiment.

`V17_TERMINAL_REDUCER` remains authoritative.  
`can_execute=false`.
