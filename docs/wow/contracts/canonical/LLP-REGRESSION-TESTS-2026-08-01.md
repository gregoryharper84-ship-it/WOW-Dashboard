# LLP Regression Tests — 2026-08-01 Upgrade

## Test Format

```text
ID
Input
Expected Gate
Expected Result
Failure Prevented
```

## RT-001 — Wrong-Day Leakage

Input: User requests August 1 slate; candidate is officially scheduled August 2.

Expected:

```text
wow.llp-slate-integrity-expert
WRONG_DATE
candidate_removed=true
```

Prevents: next-day WNBA game appearing in same-day rankings.

## RT-002 — Wrong-Year Search Result

Input: Search returns a 2025 matchup with matching teams while requested year is 2026.

Expected:

```text
WRONG_YEAR
candidate_removed=true
```

## RT-003 — Event Starts During Analysis

Input: Event is scheduled at 7:30 PM; final refresh occurs at 7:32 PM.

Expected:

```text
EVENT_ALREADY_STARTED
REMOVE_FROM_FINAL_OUTPUT
```

## RT-004 — Finished Favorite Still Ranked

Input: A favorite was modeled pregame but official status is FINAL at output time.

Expected:

```text
EVENT_FINISHED
row removed from all pregame leaderboards
```

## RT-005 — Duplicate-Team Impossible Slate

Input: Chicago Sky appears against two opponents on the same date with overlapping times and no official doubleheader.

Expected:

```text
DUPLICATE_TEAM_EVENT
both conflicting rows held until one official event identity is resolved
```

## RT-006 — Soccer Missing Draw

Input: NYCFC -175 / Toronto +140 with no draw price.

Expected:

```text
MISSING_DRAW_PRICE
MARKET_NORMALIZATION_FAILURE
no edge calculation
```

## RT-007 — Soccer Three-Way Correct Normalization

Input:

```text
Home +120
Draw +300
Away +185
```

Expected approximate raw probabilities:

```text
45.4545%
25.0000%
35.0877%
```

Expected no-vig:

```text
43.068%
23.687%
33.245%
sum≈100.000%
```

## RT-008 — Wrong Outcome Comparison

Input: Away model probability is compared to favorite/home no-vig probability.

Expected:

```text
OUTCOME_IDENTITY_MISMATCH
edge invalid
```

## RT-009 — Raw Implied Labeled No-Vig

Input: Underdog +165 raw implied 37.74% is labeled no-vig despite opponent -200.

Expected no-vig:

```text
36.15% approximately
```

Expected failure if 37.74% is used:

```text
RAW_AS_NO_VIG_FAILURE
```

## RT-010 — No-Vig Sum Failure

Input: Two-way reported no-vig probabilities are 61.76% and 36.11%.

Expected:

```text
sum=97.87%
NORMALIZATION_FAILURE
```

## RT-011 — Point Edge Mislabeled Lower-Bound Edge

Input:

```text
point=67.96%
lower_bound=64.56%
no_vig=65.81%
```

Expected:

```text
point_edge=+2.15%
lower_bound_edge=-1.25%
NO_EDGE in edge lane
```

## RT-012 — Universal 5% Haircut

Input: Calibration method is only `point_probability × 0.95` for every sport and market.

Expected:

```text
UNCALIBRATED_MODEL
highest_result=WATCH
```

## RT-013 — MLB Starter Scratch

Input: Original starter scratched and replacement confirmed after prior model run.

Expected:

```text
MODEL_RERUN_REQUIRED
prior probability invalidated
```

## RT-014 — NHL Goalie Change

Input: Expected goalie changes after model run.

Expected:

```text
MODEL_RERUN_REQUIRED
candidate removed until rerun
```

## RT-015 — Moneyline/Spread Failure-Path Mismatch

Input: Moneyline favorite failure path is “opponent backdoor covers spread.”

Expected:

```text
MARKET_MISMATCHED_FAILURE_PATH
failure model invalid
```

## RT-016 — Conditional Presented as Unconditional

Input: Favorite probability assumes normal lineup and no failure regime but is published as final.

Expected:

```text
CONDITIONAL_PROBABILITY_PRESENTED_AS_UNCONDITIONAL
REJECT_DATA_QUALITY
```

## RT-017 — Market-Dependent Model

Input: Market prior weight is 70%; independent evidence is weak.

Expected:

```text
MARKET_DEPENDENT_MODEL
highest confidence tier prohibited
```

## RT-018 — Stale Price

Input: Price is 14 minutes old at final output.

Expected:

```text
MARKET_STALE_REMOVE
```

## RT-019 — No Qualified Upset

Input: Every underdog fails lower-bound probability, event status, or data-quality requirements.

Expected:

```text
HIGHEST-PROBABILITY UPSET: NONE QUALIFIED
```

## RT-020 — Separate Probability and Edge Rankings

Input: Candidate A has 72% lower-bound probability and negative lower-bound edge; Candidate B has 46% lower-bound probability and +3% edge.

Expected:

```text
A may rank in probability leaderboard only
B may rank in upset edge leaderboard
neither ranking overwrites the other
```

## RT-021 — Final Row Reconciliation

Input: 30 discovered rows; 8 wrong date; 5 started; 3 normalization failures; 4 uncalibrated; 10 survive.

Expected:

```text
30 = 8 + 5 + 3 + 4 + 10
row_reconciliation=PASS
```

## RT-022 — Kalshi Inventory Empty

Input: `/api/wow/kalshi/health` returns `signal=INVENTORY_EMPTY`.

Expected:

```text
KALSHI lane stopped
no contract audit
no probability injection
can_execute=false
```

## RT-023 — Kalshi Combo During Recovery

Input: Two qualified LLP winners are requested as a Kalshi combo while Recovery Mode is active.

Expected:

```text
KALSHI_REJECT_BAD_STRUCTURE
portfolio_status=PORTFOLIO_REJECTED
```

## RT-024 — No Forced Output

Input: No candidate survives final refresh.

Expected:

```text
NO_VERIFIED_REMAINING_PREGAME_PLAY
```
