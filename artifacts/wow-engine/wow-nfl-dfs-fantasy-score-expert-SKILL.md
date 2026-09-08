# Skill: wow.nfl-dfs-fantasy-score-expert

## Purpose
Produce governed NFL player fantasy-score distributions for exact DFS/fantasy-score props using a verified platform scoring adapter and a joint team/player opportunity simulation.

This skill models fantasy points from the underlying football outcomes. It does not treat season fantasy points per game, external DFS projections, betting lines, recent hit rates, or another platform's scoring as the governed model probability.

## Governance
```text
runtime_generation=V17_ACTIVE
lane_status=FULL_MODEL_GOVERNED
controlling_specialist=wow.nfl-dfs-fantasy-score-expert
standalone_specialist_ceiling=MODEL_QUALIFIED_HOLD
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

Player DFS/fantasy-score props remain in the WOW prop lane. LLP team/event probability capability does not control this market.

## Supported Scope
Initial supported positions:
```text
QB
RB
WR
TE
```

Kicker, DST, IDP, return-specialist, showdown captain multipliers, and unusual bonus formats require an explicitly certified scoring adapter and model extension; otherwise fail closed.

## Exact Scoring Adapter — Mandatory
Never assume PrizePicks, DraftKings, FanDuel, Underdog, or another platform uses the same scoring.

Required adapter fields:
```text
scoring_profile_id
platform
market_name
passing_yards_points
passing_td_points
interception_points
rushing_yards_points
rushing_td_points
receiving_yards_points
reception_points
receiving_td_points
fumble_lost_points
two_point_conversion_points
return_yards_points_if_applicable
return_td_points_if_applicable
bonuses
negative_points
stat_provider
stat_correction_policy
overtime_treatment
settlement_rule
scoring_verified_at
```

No exact scoring adapter => `MODEL_INPUTS_INSUFFICIENT` for fantasy-score probability. Do not silently substitute generic PPR, half-PPR, standard, or a different site's scoring.

## Exact Candidate Identity
Require:
```text
player
team
opponent
official_event_id
event_date
event_time
position
market_type=FANTASY_SCORE|DFS_POINTS
exact_line
side=MORE|LESS
platform
scoring_profile_id
settlement_rule
board_timestamp
```

## Required Football Inputs
### Team environment
```text
team_play_distribution
pace
neutral_pass_rate_or_PROE
projected_dropbacks
projected_rush_attempts
red_zone_trip_distribution
team_scoring_distribution
game_state_transition_model
spread_total_context_if_used
venue
weather
```

Sportsbook spread/total may be used as a contextual prior or contradiction detector. It may not be relabeled as model probability.

### QB
```text
starter_status
dropback_share
pass_attempt_distribution
completion_model
aDOT
yards_per_attempt_distribution
pass_td_rate
interception_rate
pressure_rate
sack_rate
scramble_rate
designed_rush_share
red_zone_rush_share
```

### RB
```text
snap_share
carry_share
route_share
target_share
reception_model
rush_efficiency
receiving_efficiency
goal_line_share
red_zone_opportunity_share
pass_protection_role
script_sensitive_role
```

### WR / TE
```text
snap_share
route_participation
target_share
air_yard_share
aDOT
catch_rate_by_depth
YAC_distribution
red_zone_target_share
end_zone_target_share
coverage_matchup
man_zone_role_split_if material
```

### Context
```text
offensive_line_health
starting_QB
skill_player_inactives
role_redistribution
opponent_defensive_front
opponent_coverage
opponent_explosive_play_suppression
travel_rest
weather_wind_precipitation_temperature_if_material
```

## Historical Evidence
Raw fantasy points/game and L5/L10 hit rates are evidence only. Historical rows should be role-valid and opportunity-comparable when the Full Model Gatekeeper supplies discernment/ESS.

```text
FANTASY_PPG != MODEL_PROBABILITY
RAW_L10_HIT_RATE != MODEL_PROBABILITY
EXTERNAL_DFS_PROJECTION != GOVERNED_MODEL_PROBABILITY
```

## Joint Simulation
Minimum simulations:
```text
50000 standard
100000 for thin tails, promotional thresholds, or high role uncertainty
```

Simulate the game/team environment first, then player opportunity and outcomes. Preserve shared dependencies.

Each simulation should draw, as applicable:
```text
game state
team plays
dropbacks
rush attempts
red-zone opportunities
QB passing/rushing outcomes
RB carries/routes/targets
receiver routes/targets/air yards/receptions/YAC
TD allocation
turnovers/fumbles
weather/pressure effect
injury/role restriction regime if material
exact fantasy-score conversion
```

### Dependency Rules
Do not independently multiply:
```text
QB pass yards and WR receiving yards
QB pass TD and receiver TD
RB carries and trailing script
receiver target shares within one team
team touchdowns across players
QB change and pass-catcher efficiency
```

## Failure Regimes
At minimum model when applicable:
```text
NORMAL_ROLE
LIMITED_ROLE
SNAP_ROUTE_SUPPRESSION
QB_CHANGE_OR_LIMITATION
OL_PROTECTION_COLLAPSE
PACE_COLLAPSE
POSITIVE_BLOWOUT_SCRIPT
NEGATIVE_TRAILING_SCRIPT
WEATHER_SUPPRESSION
COVERAGE_OR_MATCHUP_SUPPRESSION
INJURY_OR_EARLY_EXIT
```

Required identity:
```text
P(candidate)=sum_i P(regime_i)*P(candidate|regime_i)
```

DNP/early-exit treatment must match the exact platform settlement rules.

## Required Numeric Outputs
### Fantasy score distribution
```text
fantasy_score_mean
fantasy_score_median
fantasy_score_std
fantasy_score_p10
fantasy_score_p25
fantasy_score_p75
fantasy_score_p90
```

### Component distributions
For applicable positions expose expected/interval values for the scoring components, including:
```text
pass_attempts
pass_yards
pass_TD
INT
rush_attempts
rush_yards
rush_TD
targets
receptions
receiving_yards
receiving_TD
fumbles_lost
```

### Exact-line probability package
```text
P(MORE)
P(LESS)
raw_model_probability
unconditional_probability
failure_path_score
largest_failure_path
largest_upside_path
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
best_modeled_side
probability_gap
model_timestamp
```

Dynamic calibration must react to sample size, role certainty, starter/QB certainty, injury uncertainty, source conflict, model disagreement, market-prior weight, freshness, and calibration history. Point probability is never a lower bound.

## Hard Gates
```text
missing exact scoring adapter => MODEL_INPUTS_INSUFFICIENT
starting QB unresolved when material => MODEL_INPUTS_INSUFFICIENT
player status/role unresolved => MODEL_INPUTS_INSUFFICIENT
no team play/opportunity distribution => REJECT_DATA_QUALITY
no position-specific opportunity model => REJECT_DATA_QUALITY
only external DFS projection or FPPG available => RESEARCH_INTEREST ceiling
scorer invoked but fails => preserve typed scorer failure
valid numeric package + specialist gates pass => MODEL_QUALIFIED_HOLD — NFL_DFS_FANTASY_SCORE_MODEL_PASSED
```

Failed MORE does not approve LESS. Both sides must be scored at the exact line.

## Full Model Handoff
After specialist pass, return to V17 Full Model governance for calibration/validity, exact-line/settlement/payout/no-vig where applicable, dependence/exposure, weakest-leg handling, final refresh, immutable prediction write, reconciliation, and terminal reduction.

A missing downstream market price may block value/market publication without erasing a completed sporting fantasy-score probability.

## Final Refresh
Immediately before publication recheck:
```text
event_status
starting_QB
player_active/inactive status
skill-player inactives
material OL changes
role/depth-chart news
weather if material
exact line
scoring profile/settlement identity
```
Pregame probabilities are removed after kickoff unless a dedicated live model reruns.

## Postmortem
Persist the immutable pregame score distribution and exact line, then after settlement record actual scoring components, official fantasy score, observed failure regime, probability error, calibration metrics, and process classification. Do not retroactively change the predicted line or direction.

## Acceptance Tests
1. A DraftKings projection cannot be used directly for a PrizePicks fantasy-score prop without a verified scoring adapter.
2. A generic PPR projection cannot silently substitute for half-PPR or platform-specific scoring.
3. QB and receiver outcomes preserve correlation.
4. RB workload changes numerically when game script changes.
5. Inactive teammate redistribution changes opportunity shares before scoring.
6. Starting-QB uncertainty lowers readiness/confidence rather than becoming prose only.
7. Weather materially changes distributions only when supported and modeled.
8. MORE and LESS are both scored at the exact line.
9. External projections and sportsbook totals remain evidence/prior inputs only.
10. `can_execute=false` remains invariant.

## One-Line Definition
**WOW NFL DFS Fantasy Score Expert is an exact-scoring, position-specific, opportunity- and game-state-aware joint simulation specialist for NFL player fantasy-score props under V17 governance.**
