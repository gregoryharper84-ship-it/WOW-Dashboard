# WOW-PATCH-2026-07-23-SLIP-CONSISTENCY-AND-FRAGILITY

## Status

```text
ACTIVE
patch_priority=CRITICAL
framework=WOW_v16_CLEAN_CORE
activation_date=2026-07-23
```

## Purpose

Improve full-slip consistency without weakening WOW's fail-closed standards.

This patch addresses the following observed failure modes:

- individually strong legs assembled into structurally weak slips;
- four- and five-pick cards diluting otherwise strong model accuracy;
- one weak leg destroying an otherwise qualified card;
- low-count Goblin-style props being treated as safer than their discrete event risk supports;
- high-usage composite LESS props being approved without upper-tail modeling;
- first-inning pitch-count research leaking into final cards despite TEST_ONLY governance;
- the same player/stat/event exposure appearing on multiple slips;
- PrizePicks green "Win" results being counted as economic wins despite negative net return;
- card optimization focusing on leg count instead of calibrated joint hit probability.

## Non-Negotiable Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
lane_status=PROBABILITY_ONLY
can_execute=false
stake=0
money_label_allowed=false
final_approval_allowed=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

No patch in this pack authorizes execution, stake sizing, or live trading.

---

# PATCH-014 — Structure-Adaptive Joint Probability Gate

## Status

```text
ACTIVE
priority=CRITICAL
```

## Rule

The slip builder must optimize:

```text
maximize_joint_calibrated_hit_probability
```

It must not optimize:

```text
maximize_leg_count
maximize_displayed_multiplier
preserve_requested_card_size
```

## Default Structure

```text
default_card_size=2
maximum_card_size=3
power_4_to_5_legs=false
flex_max_legs=3
```

A larger card may not be created merely because more individually qualified candidates exist.

## Required Joint Outputs

```text
joint_hit_probability_point_estimate
joint_hit_probability_lower_bound
joint_hit_probability_upper_bound
joint_failure_probability
correlation_method
simulation_count
largest_single_failure_contribution
weakest_leg
critical_leg_index
slip_fragility_score
```

## Correlation Rule

Independent multiplication is prohibited when legs share:

```text
same game
same player
same team
same pitcher or goalie
same lineup assumption
same injury assumption
same game script
same pace environment
same weather system
same settlement dependency
```

Use a joint simulation, conditional event tree, multivariate model, or conservative fail-closed treatment.

## Hard Gates

```text
card_size > 3
=> NO_BAD_STRUCTURE

joint_probability_unavailable
=> NO_DATA_QUALITY

correlation_unresolved
=> NO_CORRELATION

requested_leg_count_requires_filler
=> NO_BAD_STRUCTURE

no_qualifying_replacement
=> SHRINK_CARD
```

---

# PATCH-015 — Discrete Low-Count Prop Fragility Audit

## Status

```text
ACTIVE
priority=CRITICAL
```

## Scope

Apply to any prop where a one-event difference dominates the result, including:

```text
MORE 0.5 strikeouts
MORE 1.5 assists
MORE 1.5 rebounds
MORE 1.5 shots
LESS 0.5 or LESS 1.5 counting-stat lines
other low-count thresholds
```

## Required Distribution

Do not use a continuous normal approximation as the controlling model.

Required outputs:

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
zero_event_risk
```

For a threshold `t`:

```text
minimum_winning_count = smallest integer that wins
minimum_clearance_dependence_share
=
P(exactly minimum winning count) / P(win)
```

## Default Gates

```text
projected_median == minimum_winning_count
=> POWER_PROHIBITED

minimum_clearance_dependence_share >= 0.40
=> FRAGILE

role_or_minutes_floor_unresolved
=> NO_ROLE_OR_STATUS

low_count_promotional_lower_bound < 0.75
=> NO_REMOVE_FROM_POWER

continuous_model_only
=> NO_DATA_QUALITY
```

Promotional line value cannot override discrete-event fragility.

---

# PATCH-016 — Cross-Slip Daily Exposure Governor

## Status

```text
ACTIVE
priority=HIGH
```

## Purpose

Prevent the same thesis from being repeated across multiple slips and falsely appearing diversified.

## Exposure Identity

```text
exposure_key =
player_or_team
+ event
+ stat_or_market
+ exact_line
+ side
```

Also build a broader thesis key:

```text
thesis_key =
participant
+ event
+ underlying_stat_distribution
```

## Required Daily Registry

```text
research_run_id
slip_id
exposure_key
thesis_key
published_at
status
open_or_settled
duplicate_count
shared_failure_factors
```

## Hard Gates

```text
same exposure_key on second active card
=> NO_DUPLICATE_THESIS

same thesis_key with alternate threshold on second active card
=> NO_CORRELATION unless explicitly modeled

duplicate leg may not count as an independent model success

one player/stat/event distribution per daily portfolio by default
```

## Required Output

```text
daily_duplicate_count
cross_slip_overlap
shared_failure_path
portfolio_unique_theses
duplicate_adjustment_applied
```

---

# PATCH-017 — Slip Outcome and Calibration Ledger

## Status

```text
ACTIVE
priority=HIGH
```

## Purpose

Measure the engine by economic result, calibration, process quality, and duplicate-adjusted accuracy.

A platform green badge is not automatically a profitable result.

## Required Slip-Level Fields

```text
slip_id
platform
slip_type
leg_count
entry_amount
gross_return
net_profit
platform_result_label
full_card_hit
positive_net_return
displayed_multiplier
predicted_joint_probability
joint_probability_lower_bound
actual_result
weakest_leg
critical_leg_index
slip_fragility_score
process_pass_or_fail
```

## Required Leg-Level Fields

```text
leg_id
exposure_key
sport
market_type
line
side
offer_type
predicted_probability
calibrated_lower_bound
actual_result
actual_stat
margin_to_line
minimum_clearance_hit
failure_path_score_if_applicable
observed_failure_category
source_quality
duplicate_adjusted_weight
```

## Required Metrics

```text
raw_leg_hit_rate
duplicate_adjusted_leg_hit_rate
full_card_hit_rate
positive_net_return_rate
net_roi
brier_score
log_loss
calibration_by_probability_bucket
performance_by_sport
performance_by_market_type
performance_by_offer_type
performance_by_threshold_family
performance_by_card_size
```

## Process Labels

```text
PROCESS_PASS_WIN
PROCESS_PASS_VARIANCE_LOSS
PROCESS_FAIL_AVOIDABLE_LOSS
PROCESS_FAIL_STRUCTURE
PROCESS_FAIL_DATA_GAP
PROCESS_FAIL_GOVERNANCE
UNRESOLVED
```

Do not call a loss variance until all required entry-time gates are documented as passed.

---

# PATCH-018 — Composite LESS Upper-Tail Gate

## Status

```text
ACTIVE
priority=HIGH
```

## Scope

Apply to composite unders for high-usage or high-minutes players, including:

```text
PRA LESS
Pts+Rebs LESS
Pts+Asts LESS
Rebs+Asts LESS
fantasy score LESS
```

## Required Model

```text
minutes_distribution
usage_distribution
component_covariance
close_game_minutes_tail
overtime_probability
teammate_absence_role_shift
P50
P75
P90
P95
P(LESS)
P(MORE)
```

## Hard Gates

```text
P90 within 10% of line
=> NO_POWER

close_game_minutes_ceiling_unresolved
=> NO_ROLE_OR_STATUS

component_covariance_unmodeled
=> NO_DATA_QUALITY

high_usage_composite_less_point_estimate_only
=> NO_DATA_QUALITY
```

---

# PATCH-019 — TEST_ONLY Lane Quarantine

## Status

```text
ACTIVE
priority=CRITICAL
```

## Rule

Any lane marked `TEST_ONLY` may appear only in:

```text
research_pool
backtest
forward_test
postmortem
```

It may not appear in:

```text
probability_qualified_final_card
Power recommendation
Flex recommendation
paid-card presentation
```

Current controlled application:

```text
MLB 1st Inning Pitches Thrown
```

Required output:

```text
lane_status=TEST_ONLY
final_card_eligible=false
maximum_ceiling=MODEL_QUALIFIED_HOLD
```

A prior win cannot override the lane ceiling.

---

# PATCH-020 — Binding Weakest-Leg Finalizer

## Status

```text
ACTIVE
priority=CRITICAL
```

## Rule

PATCH-011 weakest-leg elimination becomes binding at finalization.

The final card cannot publish until:

```text
weakest_leg_cycle=PASS
replacement_search_complete=true
fragility_status!=FRAGILE
duplicate_exposure_status=PASS
joint_probability_status=PASS
test_only_lane_status=PASS
```

## Finalizer Contract

```text
if weakest_leg_below_floor:
    replace_or_remove()

if no_verified_replacement:
    shrink_card()

if card_is_fragile:
    rerun_weakest_leg_cycle()

if requested_size_cannot_be_met_without_filler:
    return NO_BAD_STRUCTURE
```

No downstream formatter may restore a removed leg.

---

# Updated Workflow

```text
normalize
→ slate_purge
→ exact_board_verification
→ role_status_verification
→ exact_line_ledger
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
→ outcome_ledger_registration
→ QA
```

---

# Operating Configuration

```text
CORE_CARD_SIZE=2
MAX_CARD_SIZE=3
POWER_4_TO_5_LEGS=false
FLEX_MAX_LEGS=3
1IP_FINAL_CARD_ELIGIBLE=false
LOW_COUNT_DISCRETE_GATE=true
COMPOSITE_LESS_UPPER_TAIL_GATE=true
CROSS_SLIP_DUPLICATE_GATE=true
WORST_LEG_REMOVAL_BINDING=true
JOINT_PROBABILITY_REQUIRED=true
POSITIVE_NET_RETURN_PRIMARY_LEDGER_METRIC=true
can_execute=false
```

---

# Acceptance Tests

1. A four-pick Power card is rejected during the active freeze.
2. A five-pick Flex card is rejected or shrunk to three.
3. No filler leg is added to preserve requested card size.
4. A MORE 0.5 strikeout prop reports `P(0)`.
5. A MORE 1.5 assists prop reports `P(0)`, `P(1)`, and `P(2+)`.
6. A low-count prop with median equal to minimum winning count cannot enter Power.
7. A low-count promo lower bound below 75% is removed from Power.
8. A high-usage PRA LESS requires upper-tail modeling.
9. A composite LESS with unresolved covariance is rejected.
10. A TEST_ONLY 1IP leg cannot enter a final card.
11. A duplicate exposure on two active slips is rejected.
12. Duplicate legs do not count as independent model successes.
13. A green PrizePicks result with negative net return is logged as `positive_net_return=false`.
14. Every final card reports joint probability and fragility.
15. Every final card passes the binding weakest-leg finalizer.
16. No replacement means the card shrinks.
17. Correlated legs do not use independent multiplication.
18. Every settled slip and leg is written to the calibration ledger.
19. `can_execute=false` appears in every output.
20. No output exceeds `MODEL_QUALIFIED_HOLD`.
