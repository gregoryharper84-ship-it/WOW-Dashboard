# Skill: wow.soccer-passes-attempted-expert

## Purpose
Model soccer player **Passes Attempted** props at the exact board line using a minutes-aware, team-volume-aware, role-aware match simulation. Accurate/completed passes, completion percentage, per-90 rates, recent hit rates, external projections, and sportsbook prices are evidence only and may never be relabeled as governed model probability.

## Governance
```text
runtime_generation=V17_ACTIVE
lane_status=FULL_MODEL_GOVERNED
controlling_specialist=wow.soccer-passes-attempted-expert
standalone_specialist_ceiling=MODEL_QUALIFIED_HOLD
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

This specialist owns only player **Passes Attempted** props. It does not own accurate/completed passes, passes received, team passing totals, assists, shots, or match winners.

## Exact Market Identity
Require:
```text
player
event_id
competition
event_date
event_time
team
opponent
period
stat_type=PASSES_ATTEMPTED
exact_line
side=MORE|LESS
boundary_operator
platform
settlement_source
settlement_rule
board_timestamp
```

Any stat-definition or settlement ambiguity fails closed.

## Prohibited Shortcut
The following is invalid:
```text
accurate_passes_per90 > exact_line => MORE
accurate_passes_per90 < exact_line => LESS
```

When only accurate passes and completion rate are available, an attempt-rate reconstruction may be calculated:
```text
reconstructed_attempts_per90 = accurate_passes_per90 / completion_rate
source_status=RECONSTRUCTED
```
It is research evidence only. It cannot become `raw_model_probability`, `calibrated_probability`, or `calibrated_lower_bound`.

## Required Inputs
### Role and minutes
```text
starting_probability
lineup_status=PROJECTED|CONFIRMED
position
role
formation
set_piece_or_buildout_role_if_material
expected_minutes_distribution
P(minutes>=60)
P(minutes>=75)
P(minutes>=90)
substitution_hazard
injury_or_rotation_status
```

### Team passing environment
```text
team_pass_attempt_distribution
team_possession_distribution
team_buildout_style
formation_effect
home_away_neutral
competition_context
manager_style
```

### Player passing share
```text
player_pass_share_distribution
role_valid_recent_history
current_season_history
prior_season_history_if_needed
teammate_on_off_or_role_redistribution
accurate_passes_and_completion_rate_if_used_as_reconstruction
```

### Opponent and game state
```text
opponent_possession
opponent_pressing_intensity
opponent_block_height
opponent_turnover_pressure
opponent_game_state_behavior
score_state_transition_model
red_card_state_model
```

### Freshness
Every material input must retain source and timestamp. Projected lineups are not equivalent to confirmed lineups.

## Historical Evidence
Raw L5/L10 and per-90 rates are evidence only. Use role/opportunity comparability, weighted historical priors, and effective sample size where the Full Model Gatekeeper supplies them.

```text
RAW_L10_HIT_RATE != MODEL_PROBABILITY
PER90_ATTEMPT_RATE != MODEL_PROBABILITY
RECONSTRUCTED_ATTEMPT_RATE != MODEL_PROBABILITY
```

Prior-season rows must be downweighted when current team, manager, position, formation, role, or substitution pattern materially differs.

## Simulation Model
Minimum simulations:
```text
25000 standard
50000 when role/minutes uncertainty or tail probability is material
```

Each simulation must jointly draw:
```text
start_or_bench_state
minutes
team possession
team pass attempts
player role/share
match score state
opponent press/block state
substitution event
red-card/disruption event
player pass attempts
```

Do not independently multiply shared causes.

### Required Regimes
At minimum model:
```text
NORMAL_ROLE
EARLY_SUB
TACTICAL_ROLE_SHIFT
HIGH_PRESS_SUPPRESSION
LOW_BLOCK_POSSESSION_EXPANSION
LEADING_GAME_STATE
TRAILING_CHASE_STATE
OWN_TEAM_RED_CARD
OPPONENT_RED_CARD
INJURY_OR_ROTATION_RESTRICTION
```

Use the general failure-path identity:
```text
P(candidate)=sum_i P(regime_i)*P(candidate|regime_i)
```

## Required Numeric Outputs
```text
P(start)
expected_minutes
minutes_median
P(minutes>=60)
P(minutes>=75)
P(minutes>=90)
team_pass_attempts_mean
team_pass_attempts_interval
player_pass_share_mean
player_pass_share_interval
attempts_mean
attempts_median
attempts_std
attempts_p10
attempts_p25
attempts_p75
attempts_p90
P(MORE)
P(LESS)
raw_model_probability
unconditional_probability
failure_path_score
largest_failure_path
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
best_modeled_side
probability_gap
model_timestamp
```

Dynamic calibration must consume sample size, lineup certainty, role/minutes uncertainty, source conflict, model disagreement, and calibration history. Point probability is never a lower bound.

## Hard Gates
```text
wrong/missing exact stat identity => REJECT_DATA_QUALITY
missing exact settlement => REJECT_DATA_QUALITY
no expected-minutes distribution => REJECT_DATA_QUALITY
no team-pass/opponent-environment model => REJECT_DATA_QUALITY
only per90/reconstructed baseline available => RESEARCH_INTEREST ceiling
projected lineup near lock => confidence cap / HOLD as configured
material role conflict unresolved => MODEL_INPUTS_INSUFFICIENT
specialist invoked but fails => preserve typed scorer failure
valid probability package + specialist gates pass => MODEL_QUALIFIED_HOLD — SOCCER_PASS_ATTEMPTS_MODEL_PASSED
```

A failed MORE does not approve LESS. Both sides must be scored at the exact line.

## Full Model Handoff
After a specialist pass, return the row to V17 Full Model governance for dynamic calibration, probability validity, exact-line/settlement/market evidence where requested, correlation/exposure, weakest-leg handling, final refresh, immutable write, reconciliation, and terminal reduction.

Missing downstream market price may block market/value publication but must not erase the completed sporting probability.

## Final Refresh
Immediately before publication recheck:
```text
match_status
confirmed_lineup when available
player_status
role/formation changes
exact board line
settlement identity
critical source conflicts
```
Pregame probability is invalid after kickoff unless a dedicated live model reruns.

## Acceptance Tests
1. 80 accurate passes/90 cannot automatically qualify MORE 75.5 Passes Attempted.
2. A reconstructed 90 attempts/90 value remains evidence only.
3. A 52 attempts/90 player expected for 70 minutes is not scored as 52 attempts.
4. Confirmed 90-minute center-back and 65-minute winger use different minutes distributions.
5. Low-block opponent can expand team volume numerically; prose alone is insufficient.
6. High press can suppress or redistribute passing through the simulation.
7. Red-card states alter team and player distributions.
8. Previous-season data cannot silently override a changed current role.
9. MORE and LESS are both scored.
10. `can_execute=false` remains invariant.

## One-Line Definition
**WOW Soccer Passes Attempted Expert is a minutes-, role-, possession-, opponent-, and game-state-aware exact-line simulation specialist for soccer Passes Attempted props under V17 governance.**
