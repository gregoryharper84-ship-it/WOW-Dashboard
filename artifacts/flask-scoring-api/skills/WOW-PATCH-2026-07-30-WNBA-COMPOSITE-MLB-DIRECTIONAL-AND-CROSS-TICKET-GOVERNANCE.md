# WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE

## Status

```text
ACTIVE
patch_priority=CRITICAL
framework=WOW_v16_CLEAN_CORE
activation_date=2026-07-30
```

## Purpose

This patch responds to the July 28-29 PrizePicks postmortem.

The supplied settled slips showed:

```text
WNBA composite and component exposure performed strongly
MLB July 29 pitcher exposure failed across every unique submitted pitcher thesis
Matthew Boyd and Grayson Rodriguez were repeated across multiple losing cards
multiple thresholds on the same player-game were displayed as separate wins
a DNP appeared on a settled winning card
```

The sample is too small to prove a permanent WNBA PRA edge. It is sufficient to justify:

1. A dedicated WNBA joint component/composite model.
2. A cross-ticket exposure governor.
3. A temporary MLB directional regression firewall.
4. A mandatory unique-observation calibration ledger.

All incident figures are operator-supplied from the uploaded screenshots and must be reconciled in the settled ledger.

---

## Non-Negotiable Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
can_execute=false
capital_allocation=false
```

No patch creates live execution authority.

---

# PATCH-014 — Cross-Ticket Thesis Deduplication

**Status:** ACTIVE  
**Priority:** CRITICAL  
**Owner:** `wow.cross-ticket-exposure-governor`

## Problem

Card-level optimization does not prevent the same weak or fragile thesis from appearing on several separate cards.

Examples from the supplied postmortem:

```text
same Matthew Boyd strikeout LESS repeated
same Grayson Rodriguez strikeout LESS repeated
same WNBA player-game repeated at alternate PRA thresholds
Power structure copied from an overlapping Flex structure
```

## Required Keys

```text
exact_leg_key
player_event_key
latent_distribution_key
pitcher_thesis_key
event_script_key
```

## Hard Rules

```text
same exact leg on multiple cards
=> keep at most one occurrence

same player-event-stat-direction at multiple thresholds
=> one underlying thesis

same pitcher-directional thesis on multiple cards
=> one underlying thesis

weakest leg repeated on another card
=> second occurrence prohibited

Power card materially copied from Flex card
=> REJECT_DUPLICATE_STRUCTURE
```

## Calibration Rule

Repeated thresholds and repeated exact legs count once in model calibration.

Financial exposure may be logged separately when actual entries are supplied, but model accuracy is graded at the unique underlying-thesis level.

---

# PATCH-015 — MLB Directional Regression Firewall

**Status:** ACTIVE  
**Priority:** CRITICAL  
**Targets:** MLB pitcher strikeouts and pitching outs.

## Initial State

```text
MLB_K_LESS=WATCH_ONLY
MLB_OUTS_MORE=MODEL_QUALIFIED_HOLD ceiling
```

This is a temporary research ceiling, not a claim that either side is inherently bad.

## Required Directional Lanes

Track separately:

```text
K_MORE
K_LESS
OUTS_MORE
OUTS_LESS
PITCH_COUNT_MORE
PITCH_COUNT_LESS
BATTERS_FACED_MORE
BATTERS_FACED_LESS
```

Do not combine all pitcher props into one hit-rate bucket.

## Strikeout LESS Support Decomposition

Every K LESS model must report:

```text
support_from_low_K_rate
support_from_opponent_contact
support_from_short_outing
support_from_workload_restriction
support_from_market
```

Define:

```text
short_outing_support_share
=
probability mass where LESS wins primarily because the pitcher
fails to reach the normal batters-faced pathway
/
total modeled LESS win probability
```

Hard rule:

```text
short_outing_support_share > 0.50
=> HIGH confidence prohibited
=> maximum label MLB_K_LESS_WATCH
```

A failure-path model may not treat early-exit uncertainty as automatic support for LESS.

## Outs MORE Workload-Survival Gate

Every pitching-outs MORE must report:

```text
required_outs
required_innings_equivalent
P(reach_required_outs)
P(exit_before_required_innings)
manager_hook_probability
bullpen_pressure
pitch_count_cap_probability
```

Hard rules:

```text
P(reach_required_outs) lower bound < active floor
=> NO_LOW_PROBABILITY

material workload restriction unresolved
=> MODEL_QUALIFIED_HOLD ceiling

normal-outing probability used as unconditional
=> MODEL_INVALID
```

## Forward-Test Exit

The temporary ceiling remains until:

```text
minimum_unique_K_LESS_rows=10
minimum_unique_OUTS_rows=10
all rows reconciled=true
starter_and_lineup_review=PASS
failure_path_review=PASS
directional_calibration_review=PASS
```

No duplicate pitcher-game threshold may count twice.

---

# PATCH-016 — Settled Pitcher Directional Regression Ledger

**Status:** ACTIVE  
**Priority:** HIGH

Every settled MLB pitcher prop must record:

```text
pitcher
event
market_type
direction
line
offer_type
starter_confirmation
lineup_confirmation
health_regime
predicted_innings
predicted_batters_faced
predicted_pitch_count
predicted_strikeouts
failure_path_score
short_outing_support_share
conditional_probability_given_normal_workload
unconditional_probability
calibrated_lower_bound
actual_innings
actual_batters_faced
actual_pitch_count
actual_strikeouts
settled_result
observed_failure_category
process_pass_or_fail
duplicate_group_id
```

Allowed failure categories:

```text
K_RATE_UNDERESTIMATION
BATTERS_FACED_UNDERESTIMATION
WORKLOAD_SURVIVAL_MISS
MANAGER_HOOK_MISS
HEALTH_REGIME_MISS
LINEUP_CONTACT_MISS
MARKET_CONTRADICTION_IGNORED
DUPLICATE_EXPOSURE
VARIANCE_WITH_PROCESS_PASS
DATA_UNOBTAINABLE
UNRESOLVED
```

Do not assign a failure category after the fact unless pregame evidence and settled game data support it.

---

# PATCH-017 — WNBA Composite Prop Expert and Forward-Test Gate

**Status:** ACTIVE  
**Priority:** HIGH  
**Owner:** `wow.wnba-composite-prop-expert`

## Objective

Determine whether a WNBA player's best available high-probability market is:

```text
Points
Rebounds
Assists
P+R
P+A
R+A
PRA
```

PRA is not the default.

## Required Model

```text
role-conditioned joint P/R/A distribution
minutes distribution
component covariance
game-script mixture
role-matched L5/L10
outlier isolation
primary teammate status
```

## Multi-Path Audit

Every composite market must be classified:

```text
THREE_PATH_STABLE
TWO_PATH_STABLE
ONE_PATH_DEPENDENT
ROLE_FRAGILE
LINE_TOO_EFFICIENT
```

## Forward-Test Gate

```text
WNBA_COMPOSITE_FORWARD_TEST=ACTIVE
minimum_unique_player_games=20
duplicate_thresholds_count_once=true
DNP_or_void_not_a_projection_hit=true
standard_and_promotional_separated=true
calibration_review_required=true
```

Maximum output before the milestone:

```text
MODEL_QUALIFIED_HOLD
```

---

## Updated Universal PrizePicks Call Order

```text
1. Governance and slate date
2. Board normalization
3. Event and player reality
4. Role, lineup, teammate, and minutes verification
5. Exact-line ledger
6. Sport-specific model
7. WNBA joint component/composite model when applicable
8. MLB failure-path and directional firewall when applicable
9. Market sanity
10. Bidirectional score
11. Weakest-leg elimination
12. Card fragility audit
13. Cross-ticket exposure governor
14. Final lowest-ceiling label
15. Settled-ledger write after grading
```

---

## Required Integration Hooks

### `wow.slip-probability-optimizer`

Add:

```text
For WNBA P/R/A component or composite rows, invoke
wow.wnba-composite-prop-expert before final candidate ranking.

When more than one card is proposed, invoke
wow.cross-ticket-exposure-governor after card-level fragility
and before final presentation.

Preserve the lowest ceiling from the WNBA forward-test gate,
MLB directional firewall, weakest-leg optimizer, and cross-ticket governor.
```

### `wow.mlb-pitcher-failure-path-expert`

Add:

```text
Track directional calibration independently.
For K LESS, publish short_outing_support_share.
For outs MORE, publish the required-out survival lower bound.
Apply MLB_K_LESS=WATCH_ONLY until the forward-test milestone passes.
```

### Postmortem Workflow

Add:

```text
Grade unique underlying theses separately from financial exposures.
Repeated exact legs count once for calibration but all entries remain
visible in the financial ledger.
DNP/void is not a projection hit.
```

---

## Required Output Additions

Every multi-card PrizePicks response must include:

```text
unique_underlying_theses
exact_duplicate_groups
alternate_threshold_groups
shared_latent_exposure_groups
duplicate_pitcher_thesis_groups
cross_card_fragility
cards_removed_or_shrunk
```

Every WNBA composite row must include:

```text
best_stat_family
multi_path_class
role_status
primary_teammate_status
component_covariance_status
calibrated_lower_bound
forward_test_status
```

Every MLB pitcher row must include:

```text
directional_lane
failure_path_score
short_outing_support_share_if_K_LESS
required_out_survival_if_OUTS_MORE
directional_forward_test_status
```

---

## Acceptance Tests

1. Repeated Boyd LESS 4.5 Ks across two cards is one thesis and one retained occurrence.
2. Repeated Rodriguez LESS 4 Ks across two cards is one thesis and one retained occurrence.
3. Morrow PRA 17.5, 18.5, and 19.0 count as one player-game calibration observation.
4. Collier PRA and Collier assists are compared; the model does not default to PRA.
5. Awa Fam defensive rebounds do not enter this skill as total rebounds.
6. A Malonga DNP is logged as DNP/void, not a projection hit.
7. K LESS cannot receive HIGH when more than half its support comes from short-outing assumptions.
8. Outs MORE uses unconditional workload survival.
9. Power copied from Flex is rejected as duplicate structure.
10. A smaller clean card is preferred over multiple overlapping cards.
11. WNBA composite results are separated by standard and promotional offers.
12. Every settled pitcher row writes to the directional regression ledger.
13. No output exceeds the active ceiling.
14. `can_execute=false` appears in every output.

---

## Activation Prompt

> Activate WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE. Load WOW v16 Clean Core, activate the WNBA composite forward-test lane, compare all verified P/R/A component and composite markets through a joint role-conditioned distribution, set MLB K LESS to WATCH_ONLY and MLB outs MORE to a workload-survival HOLD ceiling, deduplicate repeated player-game and pitcher theses across all cards, reject copied Power/Flex structures, grade unique underlying theses separately from financial exposure, write every settled MLB pitcher row to the directional regression ledger, and enforce can_execute=false.

---

## One-Line Definition

**This patch adds a WNBA joint composite-stat expert, stops cross-ticket duplicate exposure, freezes uncalibrated MLB K LESS confidence, strengthens pitching-outs workload survival, and forces unique-thesis calibration under WOW v16 Clean Core.**
