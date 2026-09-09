# WOW-PATCH-2026-09-07-POSTMORTEM-PRESERVE-REFINE-REGRESSION

## Status

```text
status=ACTIVE_BACKEND_CONTRACT_CANDIDATE
runtime_generation=V17_ACTIVE
scope=POSTMORTEM_RETROSPECTIVE_ONLY
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose

Make WOW retrospectives improve the engine without destroying behavior that is already working.

The binding sequence is:

```text
PRESERVE -> REFINE -> REGRESSION_CHECK
```

A postmortem must evaluate both successful and unsuccessful behavior. A loss is not permission to broadly tighten the model; a win is not permission to ignore fragility. The goal is to isolate the smallest load-bearing wrinkle and repair it while protecting validated strengths.

## Preserve First

Every retrospective must explicitly record successful behavior worth protecting, including as applicable:

```text
accurate specialist lanes
profitable or capital-efficient lanes
effective failure-path modeling
effective Flex/shrink/weakest-leg behavior
successful sporting-probability selection
good settlement/identity handling
good portfolio diversification
```

A proposed patch must identify `preserve_targets` before it can be considered complete.

## Targeted Refinement

Retrospective changes should be diagnostic or narrowly scoped by default.

Allowed examples:

```text
add missing observed-path attribution
add exact margin-to-line diagnostics
add lane-specific failure-path state
separate sporting success from payout efficiency
fix a specific model feature or regime only when evidence supports it
```

Prohibited automatic reactions include:

```text
universal probability haircut
global qualification-floor increase
broad lane suspension from one isolated miss
turning explanatory narrative into a second numerical penalty
changing sporting probability merely because a card lost
```

Any proposal with `broad_tightening=true` must return:

```text
status=REQUIRES_EXPLICIT_GOVERNANCE
```

It cannot be auto-implemented by the postmortem pipeline.

## Regression Check

Every proposed refinement must list regression checks proving that the repair does not damage successful behavior.

Minimum questions:

1. Does the change preserve the lane behavior that worked?
2. Does it avoid changing unrelated model probabilities?
3. Does it avoid changing qualification floors unless separately governed?
4. Does it preserve objective separation between sporting probability, market/value, settlement, and portfolio/card construction?
5. Does it preserve `can_execute=false`?

## Immutable Pregame Attribution

Postmortem outcomes may enter governed calibration only when they link to the exact immutable pregame record for the same:

```text
participant/event
market/stat
period
exact line or threshold
side/direction
settlement identity
```

If the exact immutable pregame record cannot be proven:

```text
capture_timing=POST_EVENT_RETROACTIVE
prediction_record_status=NO_MATCHED_IMMUTABLE_PREGAME_RECORD_FOUND
calibration_eligible=false
excluded_from_calibration=true
```

The postmortem must not backfill:

```text
raw_probability
calibrated_probability
lower_bound
upper_bound
failure_path_score
```

A settled screenshot can prove what happened and what was paid. It cannot prove what a governed model predicted before the event.

## Margin-to-Line Diagnostics

Scalar props should persist the signed margin from the selected side's perspective:

```text
MORE: actual - line
LESS: line - actual
```

Diagnostic buckets:

```text
NEAR_BOUNDARY
NARROW_CLEAR
COMFORTABLE_CLEAR
MISS
LARGE_MISS
```

These labels describe realized settlement margin only. They do not retroactively change the pregame probability.

## 1IP Tail Attribution

For MLB 1st-Inning Pitches Thrown, retrospective evidence may persist observed tail state including:

```text
observed_bf
outs_after_top3
top_order_reach_events
observed_bf_ge_5
observed_bf_ge_6
```

The active 1IP specialist currently publishes `P_BF_GE_5` but does not separately publish a fitted `P_BF_GE_6`. Therefore:

```text
observed_bf_ge_6 may be recorded after settlement
pregame_bf_ge_6_probability must remain null unless an immutable pregame model artifact explicitly produced it
P_BF_GE_6 must never be inferred, interpolated, or fabricated from P_BF_GE_5
```

Adding a future fitted BF>=6 state is a model change and requires its own validation. The September 6 Gerrit Cole miss is a diagnostic reason to inspect the tail, not authority to make the whole 1IP lane stricter.

## Sporting Probability vs Position Economics

Sporting selection quality and card economics are separate contracts.

Postmortems may calculate position-level diagnostics such as:

```text
entry_cost
gross_return
net_profit
ROI
capital_share
profit_contribution_share
gross_multiplier
all-or-nothing break-even joint probability
```

These economics fields must not rewrite or recalibrate sporting probabilities.

For Flex/non-all-or-nothing structures, a simple `1 / gross_multiplier` break-even formula is not valid unless the complete payout state distribution is modeled. The retrospective ledger therefore leaves `break_even_joint_probability` null for those structures.

## September 6 Regression Fixture

The September 6, 2026 retrospective is the initial acceptance fixture:

```text
legs=17
wins=16
losses=1
MLB_pitcher_strikeouts=8-0
MLB_1IP=2-1
winner_selections_shown=4-0
positions=4
profitable_positions=3
non_losing_positions=4
total_entry=103.50
total_return=150.68
net_profit=47.18
ROI=45.6%
```

Required preservation behavior:

```text
DO NOT suspend or broadly tighten the MLB K lane
DO NOT apply a universal probability haircut
DO NOT make the entire 1IP lane stricter because of the Cole miss
DO preserve Flex protection and weakest-leg/shrink logic
DO preserve winner sporting-probability selection while separately auditing payout efficiency
```

The Cole row is expected to record a large negative settlement margin and an observed inning-extension/tail path. If no exact immutable pregame probability row exists, every pregame probability field remains null.

## Backend Persistence

The canonical retrospective ledger consists of:

```text
wow_postmortem_runs
wow_postmortem_legs
wow_postmortem_positions
wow_postmortem_patch_candidates
```

Rows are immutable. The atomic writer must reconcile run, leg, position, and patch counts before returning PASS.

## Safety

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

The retrospective subsystem has no authority to place, route, modify, approve, or cancel a wager. It also has no authority to override V17_TERMINAL_REDUCER or any controlling specialist.

## One-Line Definition

**WOW V17 retros preserve strengths first, repair only evidence-supported wrinkles, and regression-check every repair so learning from losses never breaks what is already working.**
