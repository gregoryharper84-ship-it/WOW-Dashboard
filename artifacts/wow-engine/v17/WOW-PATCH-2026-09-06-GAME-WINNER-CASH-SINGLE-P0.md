# WOW-PATCH-2026-09-06-GAME-WINNER-CASH-SINGLE-P0

## Status

```text
status=P0_REPAIR_IMPLEMENTED_ON_BRANCH_PENDING_CI_AND_MERGE
runtime_generation=V17_ACTIVE
probability_owner=LLP_TEAM_BETTING_ENGINE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Incident

The reviewed cash-single Game Winner sample produced:

```text
settled_selections=7
sporting_wins=2
cash_entered=132.58
net_result=-64.64
roi=-48.8%
```

This is a P0 profitability incident. It does **not** suspend the Game Winner probability lane. It requires a hard separation between:

```text
SPORTING_PROBABILITY_QUALIFIED
and
CASH_SINGLE_PROMOTION_QUALIFIED
```

## Root cause

V17 correctly kept `WOW_V17_ML_WINNERS` probability-only. `full_model_card_admission.py` also correctly admits team/event rows to a probability card from governed probability packages. However, the profitability workflow had no binding V17 cash-single promotion gate proving that the exact PrizePicks payout still offered positive conservative value.

An older August 14 specification already described this architecture, but it remained `IMPLEMENTATION_PENDING`. The missing enforcement allowed a probability-ranked Game Winner to be consumed as a cash single without separately proving current price/value eligibility.

## Non-negotiable lane separation

Do not modify LLP sporting probability because the payout is unattractive.

```text
LLP_TEAM_BETTING_ENGINE
→ governed outright-win probability
→ calibrated lower bound
→ probability leaderboard
```

Then, only when the user requests a cash single, paid card, profitability plan, or monetary comparison:

```text
governed probability package
→ exact PrizePicks payout
→ exact two-way market/no-vig audit
→ GAME_WINNER_CASH_SINGLE_GATE
→ structure/exposure
→ final refresh
→ immutable pregame write
→ V17_TERMINAL_REDUCER
```

A cash rejection must preserve the completed sporting probability.

## Binding cash-single gate

Implementation:

```text
v17.game_winner_cash_single_gate.evaluate_game_winner_cash_single
```

Required inputs:

```text
candidate/event identity
selected participant
probability_publishable=true
rank_eligible=true
raw/calibrated/lower/upper probability package
calibration_health_status=PASS
market_prior_weight <= 0.50 when supplied
failure_path_status=PASS or NOT_APPLICABLE when supplied
PrizePicks exact gross multiplier
PrizePicks capture timestamp
exact two-way market verified
sportsbook source count >= 1
sportsbook timestamp
market no-vig probability
market/model disagreement status when material
final_refresh_status=PASS
immutable_prediction_write_status=WRITTEN/PASS
```

For a one-pick PrizePicks Game Winner:

```text
platform_break_even_probability = 1 / gross_multiplier
lower_bound_platform_edge = calibrated_lower_bound - platform_break_even_probability
lower_bound_edge_after_buffer = lower_bound_platform_edge - active_safety_buffer
```

Default provisional economic buffer when runtime configuration does not supply one:

```text
active_safety_buffer=0.025
status=PROVISIONAL_PENDING_CALIBRATION
```

This buffer is downstream market/economic friction. It is not a sporting-model haircut and may not alter LLP model probability.

Promotion requires:

```text
lower_bound_edge_after_buffer > 0
```

If not:

```text
REJECT_NO_EDGE
cash_single_eligible=false
probability_rank_eligible may remain true
sporting_probability_preserved=true
```

## Market disagreement protection

If the exact external no-vig fair probability is below the PrizePicks break-even probability while the model alone claims enough probability to promote, require an explicit resolved market/model disagreement audit.

Unresolved:

```text
MODEL_ONLY_DISAGREEMENT_UNRESOLVED
cash_single_eligible=false
terminal_ceiling=MARKET_VERIFIED_HOLD
```

Large model-versus-market disagreement also fails closed until reconciled.

## Freshness

Default maximum age:

```text
PrizePicks payout <= 10 minutes
exact two-way market <= 10 minutes
```

Stale/missing price evidence cannot promote.

## Calibration-health repair

The 2-for-7 incident is a revalidation trigger, not a license to retrospectively rewrite probabilities. Cash promotion now requires the active controlling cohort's calibration-health status to pass. Candidate-specific dynamic calibration remains required. Market prior above 50% remains a model-dependence blocker.

The immutable outcome ledger should be used to compute Brier score, log loss, calibration bias/ECE, lower-bound reliability, and CLV as the Game Winner sample grows. The seven-result incident itself is too small to justify an arbitrary sport-model coefficient change without the original pregame probability rows.

## Finalization is binding

Economic preflight is not enough. Cash eligibility requires:

```text
final_refresh_status=PASS
immutable_prediction_write_status=PASS|WRITTEN|COMPLETED
```

A later lineup/status/price/settlement change invalidates the prior economic decision and requires rerun.

## Regression invariants

1. A team may rank highly in the probability-only leaderboard and fail the cash lane.
2. A non-positive lower-bound platform edge returns `REJECT_NO_EDGE`.
3. Positive point probability is insufficient when the lower bound fails the buffer.
4. Missing exact two-way market evidence blocks cash promotion.
5. Stale PrizePicks or market pricing blocks cash promotion.
6. Market-prior weight above 50% blocks cash promotion.
7. Unresolved model-only disagreement blocks cash promotion.
8. Failed calibration health blocks cash promotion.
9. Final refresh is binding.
10. Immutable pregame write is binding.
11. Realized postgame result never changes the pregame gate result.
12. `can_execute=false` remains invariant.

## Profitability-plan rule

Game Winner remains active. The profitability plan must consume only rows with:

```text
cash_single_eligible=true
```

Probability-only leaderboard rows must never be counted as expected cash opportunities merely because they are `rank_eligible` or have a strong calibrated lower bound.

## Revalidation target

Do not declare the lane repaired from the historical 2/7 alone. After merge/deploy:

```text
run current Game Winner candidates through the new cash gate
persist every pregame prediction + exact payout + market snapshot
settle outcomes immutably
review calibration and lower-bound reliability by sport/model cohort
separate sporting-model misses from price-gate/process failures
```

The lane remains usable while this forward revalidation occurs; only candidates that pass the new promotion gate may enter the cash-profitability output.

## Safety

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```
