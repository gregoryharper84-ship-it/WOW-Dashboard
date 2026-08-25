# Replit Build Handoff — 2026-08-01 Postmortem Patches

Implement the following under WOW v16 Clean Core.

## Active patch IDs

```text
WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD
WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE
WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY
```

## Required code targets

### 1. Session exposure persistence

Add or extend a persistent session/slate exposure ledger with:

```text
session_id
research_run_id
slate_date
slip_id
row_id
player_id
event_id
market_type
stat_type
line
side
distribution_family
proposed_stake
submission_status
settlement_status
created_at
updated_at
```

Required functions:

```text
get_current_slate_exposure(session_id, slate_date)
build_duplicate_groups(rows)
build_shared_distribution_groups(rows)
calculate_duplicate_leg_exposure_pct(group, portfolio_stake_base)
calculate_distribution_family_exposure_pct(group, portfolio_stake_base)
apply_cross_slip_exposure_ceiling(rows)
```

Exposure source precedence:

```text
session ledger
> open/unsettled ledger
> same-slate proposed rows
> workbook fallback
```

### 2. Slip finalizer integration

Call the exposure guard after weakest-leg and fragility audits but before final card output.

Required actions:

```text
TIER_0 => PASS
TIER_1 => PASS_WITH_DISCLOSURE
TIER_2 => HOLD_CONFIRMATION_REQUIRED
TIER_3 => HARD_STOP_CROSS_SLIP_OVEREXPOSURE
```

TIER_3 cannot be overridden by user confirmation.

### 3. 1IP recent-efficiency service

Add:

```text
calculate_recent_1ip_efficiency_score(pitcher_id, as_of)
```

Return:

```text
recent_window
baseline_window
metric_values
metric_flags
tier_1_score
tier_2_modifier
final_score
band
probability_haircut
ceiling
data_coverage_count
```

Do not use postgame data in a historical pregame regrade.

### 4. Directional Fragility Score

Add to the controlling 1IP event-tree output:

```text
p_less_and_bf3
p_less
p_more_given_bf4_plus
right_tail_mass_line_plus_3
raw_p_less
calibrated_lower_bound_less
three_batter_less_dependence
extended_inning_loss_rate
probability_uncertainty_gap
directional_fragility_score
directional_ceiling
```

Formula:

```text
DFS =
0.35 * three_batter_less_dependence
+ 0.30 * extended_inning_loss_rate
+ 0.20 * right_tail_mass
+ 0.15 * min(1, uncertainty_gap / 0.10)
```

### 5. Lowest-ceiling propagation

Required order:

```text
base event-tree label
efficiency ceiling
directional ceiling
market/payout ceiling
slip ceiling
cross-slip exposure ceiling
final lowest ceiling
```

No downstream pass may erase an upstream blocker.

## API / audit output

Extend invocation audit with:

```text
active_patch_ids
efficiency_gate_applied
efficiency_score
efficiency_band
efficiency_ceiling
directional_fragility_gate_applied
directional_fragility_score
directional_ceiling
cross_slip_exposure_gate_applied
portfolio_stake_base
duplicate_groups
shared_distribution_groups
exposure_tiers
lowest_ceiling
can_execute=false
```

## Regression tests

1. Exact duplicate >20% exposure hard-stops.
2. Nested same-direction thresholds are grouped.
3. Missing exposure denominator caps at HOLD.
4. Efficiency score 0.49 applies only mild haircut.
5. Efficiency score 0.50 blocks top confidence.
6. Efficiency score 0.70 caps at WATCH.
7. Fewer than four Tier 1 metrics caps at HOLD.
8. DFS 0.69 applies moderate treatment.
9. DFS 0.70 caps at HOLD.
10. DFS 0.80 caps at WATCH.
11. Hard override caps at WATCH.
12. Event-tree outputs remain controlling.
13. Lowest-ceiling propagation is preserved.
14. `can_execute=false` is always returned.
