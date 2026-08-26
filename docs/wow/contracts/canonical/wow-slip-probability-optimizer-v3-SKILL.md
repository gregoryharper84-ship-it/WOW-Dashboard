# Skill: wow.slip-probability-optimizer-v3

## Purpose

Build the smallest verified PrizePicks or Kalshi research card with the highest calibrated joint hit probability while enforcing failure-path, discrete-event, upper-tail, duplicate-exposure, fragility, and TEST_ONLY-lane controls.

This skill is probability-only.

```text
lane_status=PROBABILITY_ONLY
lowest_ceiling=MODEL_QUALIFIED_HOLD
can_execute=false
stake=0
money_label_allowed=false
final_approval_allowed=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

## Primary Objective

```text
maximize_joint_calibrated_hit_probability
```

Explicitly disabled:

```text
maximize_leg_count=false
preserve_requested_card_size=false
edge_optimization=false
ev_optimization=false
staking=false
execution=false
```

## Supported Platforms

```text
PrizePicks
Kalshi research-only
```

Kalshi remains subject to all active inventory, settlement, price-freshness, and portfolio-governor rules.

## Default Structure

```text
default_card_size=2
maximum_card_size=3
power_4_to_5_legs=false
flex_max_legs=3
```

A smaller card is always preferred over a larger fragile card.

## Decision Labels

```text
YES_MODEL_QUALIFIED
YES_MODEL_QUALIFIED_MODIFIED
NO_REPLACE
NO_REMOVE
NO_SOURCE_COVERAGE
NO_DATA_QUALITY
NO_ROLE_OR_STATUS
NO_MARKET_CONTRADICTION
NO_LOW_PROBABILITY
NO_BAD_STRUCTURE
NO_CORRELATION
NO_DUPLICATE_THESIS
NO_SETTLEMENT_UNCLEAR
NO_UNSUPPORTED_MARKET
NO_TEST_ONLY_LANE
```

## Required Inputs Per Leg

```text
row_id
platform
sport
player_or_contract
team
opponent
event
event_date
market_type
stat_type
line
direction
offer_type
board_timestamp
settlement_status
role_status
lineup_status
historical_ledger_status
model_status
market_sanity_status
```

## Core Workflow

```text
normalize
→ slate_purge
→ board_verify
→ reality_verify
→ role_valid_ledger
→ sport_specific_model
→ failure_path_audit
→ discrete_low_count_audit
→ composite_less_upper_tail_audit
→ market_sanity
→ bidirectional_score
→ modification_search
→ weakest_leg_elimination
→ cross_slip_exposure_governor
→ joint_probability_model
→ fragility_audit
→ test_only_lane_quarantine
→ slip_rebuild
→ binding_finalizer
→ ledger_registration
→ QA
```

## Probability Floor

Default per-leg floor:

```text
calibrated_probability_lower_bound >= 65%
```

This floor is necessary but not sufficient. A leg may still be removed for fragility, duplicate exposure, test-only status, correlation, or poor joint contribution.

## Failure-Path Audit

Any workload-dependent prop must use unconditional probability after failure paths.

For MLB pitcher props, call:

```text
wow.mlb-pitcher-failure-path-expert
```

Required:

```text
failure_path_score
normal_workload_probability
conditional_probability_given_normal_workload
unconditional_probability
primary_failure_path
calibrated_lower_bound
```

Rank the leg using the unconditional calibrated lower bound.

## Discrete Low-Count Audit

Apply when one event materially changes the result.

Required:

```text
minimum_winning_count
P(0)
P(1)
P(2)
P(3_plus)
P(exactly_minimum_winning_count)
P(clear_by_at_least_one_additional_event)
minimum_clearance_dependence_share
role_minutes_floor
```

Rules:

```text
projected_median == minimum_winning_count
=> POWER_PROHIBITED

minimum_clearance_dependence_share >= 40%
=> FRAGILE

low_count_promo_lower_bound < 75%
=> REMOVE_FROM_POWER

continuous_distribution_only
=> NO_DATA_QUALITY
```

## Composite LESS Upper-Tail Audit

For PRA or other composite LESS props on high-usage/high-minutes players, require:

```text
minutes_distribution
usage_distribution
component_covariance
close_game_tail
overtime_probability
P50
P75
P90
P95
```

Rules:

```text
P90 within 10% of line
=> NO_POWER

covariance_unmodeled
=> NO_DATA_QUALITY

close_game_minutes_unresolved
=> NO_ROLE_OR_STATUS
```

## Bidirectional Scoring

Always score both sides. A failed original side does not automatically qualify the opposite side.

Required:

```text
original_side_probability
opposite_side_probability
probability_gap
why_original_failed
why_replacement_passed
```

## Modification Search

Search in this order:

```text
1. Keep original leg
2. Same leg at verified safer threshold
3. Same player, cleaner verified stat
4. Same event, lower-correlation verified replacement
5. Same slate, higher-lower-bound verified replacement
6. Remove leg
```

Never invent a replacement.

## Binding Weakest-Leg Elimination

After all candidates are scored:

```text
1. Rank by calibrated lower bound.
2. Identify weakest leg.
3. Calculate marginal contribution to joint failure.
4. Search for verified replacement.
5. Rebuild and rescore.
6. Repeat until no improvement remains.
7. Remove the leg if no replacement qualifies.
```

Required finalizer flags:

```text
weakest_leg_cycle=PASS
replacement_search_complete=true
fragility_status!=FRAGILE
duplicate_exposure_status=PASS
joint_probability_status=PASS
test_only_lane_status=PASS
```

No formatter may restore a removed leg.

## Cross-Slip Daily Exposure Governor

Build:

```text
exposure_key =
participant + event + stat + exact_line + side

thesis_key =
participant + event + underlying_stat_distribution
```

Rules:

```text
same exposure_key on second active card
=> NO_DUPLICATE_THESIS

same thesis_key on second active card
=> NO_CORRELATION unless joint-modeled

one player/stat/event distribution per daily portfolio by default
```

## Joint Probability Model

Required slip-level outputs:

```text
joint_hit_probability_point_estimate
joint_hit_probability_lower_bound
joint_hit_probability_upper_bound
joint_failure_probability
correlation_method
simulation_count
weakest_leg
critical_leg_index
largest_single_failure_contribution
slip_fragility_score
```

Independent multiplication is prohibited when shared assumptions exist.

## Fragility Classification

```text
BALANCED:
largest_single_failure_contribution < 25%

CONCENTRATED:
25% to 35%

FRAGILE:
> 35%
```

A `FRAGILE` card cannot publish. It must be rebuilt or shrunk.

## TEST_ONLY Lane Quarantine

Any TEST_ONLY lane:

```text
final_card_eligible=false
```

Current application:

```text
MLB 1st Inning Pitches Thrown
```

1IP may remain in research or forward-test output but cannot enter a Power/Flex final card.

## Slip Outcome Ledger

Register every published and settled slip.

Required slip fields:

```text
entry_amount
gross_return
net_profit
platform_result_label
full_card_hit
positive_net_return
predicted_joint_probability
joint_probability_lower_bound
weakest_leg
fragility_status
process_label
```

Required leg fields:

```text
predicted_probability
calibrated_lower_bound
actual_result
actual_stat
margin_to_line
minimum_clearance_hit
failure_category
duplicate_adjusted_weight
```

A green platform badge with negative net return is not an economic win.

## Required Output

```text
DECISION: YES / NO / PARTIAL
Mode: Probability-only joint-hit optimization
Platform:
As of:
can_execute=false
```

### Leg Audit

| Leg | Raw Probability | Calibrated Lower Bound | Failure Path | Discrete Fragility | Upper-Tail Status | Duplicate Status | Action |
|---|---:|---:|---|---|---|---|---|

### Joint Card Audit

```text
card_size:
joint_probability:
joint_probability_lower_bound:
joint_failure_probability:
correlation_method:
weakest_leg:
critical_leg_index:
slip_fragility_score:
fragility_label:
test_only_legs_removed:
duplicate_legs_removed:
recommended_structure:
```

### Final Card

| Slot | Selection | Side | Line | Calibrated Lower Bound | Joint Contribution | Status |
|---:|---|---|---:|---:|---:|---|

### Compliance

```text
workflow=normalize→verify→ledger→model→failure_path→discrete_gate→upper_tail_gate→market_sanity→bidirectional→modify→weakest_leg→exposure_governor→joint_model→fragility→test_only_quarantine→rebuild→finalizer→ledger→QA
mode=probability_only
card_size_ceiling=3
edge_ev_evaluated=false
lowest_ceiling=MODEL_QUALIFIED_HOLD
can_execute=false
```

## Acceptance Tests

1. Four- or five-leg Power cards are rejected.
2. Flex cards above three legs are shrunk.
3. No filler leg is used.
4. Low-count props use discrete distributions.
5. High-usage composite LESS props use upper-tail models.
6. TEST_ONLY 1IP legs are excluded from final cards.
7. Duplicate daily exposures are rejected.
8. Weakest-leg removal is binding.
9. Fragile cards cannot publish.
10. Every settled result is logged economically and probabilistically.
11. Correlated legs do not use independent multiplication.
12. `can_execute=false` is always present.
