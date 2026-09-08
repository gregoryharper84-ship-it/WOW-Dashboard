# Skill: wow.nfl-dfs-lineup-optimizer

## Purpose
Construct research-only NFL DFS lineups from governed player fantasy-score distributions after the controlling NFL DFS player model has completed. This is a downstream lineup-construction layer, not a player projection model.

## Governance
```text
runtime_generation=V17_ACTIVE
lane_status=DFS_LINEUP_CONSTRUCTION
controlling_projection_source=wow.nfl-dfs-fantasy-score-expert
can_execute=false
capital_allocation=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

This skill does not enter contests, submit lineups, set stakes, or alter player probabilities.

## Required Platform Adapter
Before optimization require the exact active contest structure:
```text
platform
contest_type
slate_id
slate_start
salary_cap
roster_slots
position_eligibility
salary_file_timestamp
late_swap_rules
max_players_per_team
captain_or_multiplier_rules_if_applicable
scoring_profile_id
```

Never assume DraftKings, FanDuel, Yahoo, PrizePicks, or another DFS product shares salary, roster, scoring, or eligibility rules.

## Required Player Pool
Every candidate player must include a governed projection package from `wow.nfl-dfs-fantasy-score-expert` or a certified equivalent:
```text
player_id
player
team
opponent
position
salary
eligibility
active_status
fantasy_score_mean
fantasy_score_median
fantasy_score_p10
fantasy_score_p25
fantasy_score_p75
fantasy_score_p90
calibrated_projection_status
role_uncertainty
failure_path_score
correlation_factors
model_timestamp
```

External site projections may be displayed as research comparison but cannot replace the governed projection package.

## Modes
### CASH
Objective priority:
```text
1 maximize robust floor / lower-quantile team score
2 maximize calibrated expected score
3 minimize role and failure-path fragility
4 preserve sensible correlation
5 avoid unnecessary concentration
```

### GPP
Objective priority:
```text
1 maximize calibrated ceiling distribution
2 preserve positive within-lineup correlation where structurally sound
3 diversify failure paths
4 incorporate ownership/leverage only when fresh, sourced, and explicitly available
5 avoid negative or redundant correlation unless modeled benefit exceeds cost
```

Ownership is optional evidence. Missing ownership must not be fabricated.

## Correlation Rules
Model lineup-level dependence. Do not simply sum independent player variances.

Examples:
```text
QB + own WR/TE => typically positive scoring correlation
QB + opposing pass catcher => bring-back can be positive in shootout paths
RB + own DST => may be positive in lead/run-out scripts when DST lane supported
high-volume RB + multiple own pass catchers => potential negative opportunity dependence
multiple pass catchers on same team => target-share competition plus shared QB ceiling
opposing RBs => often script-dependent negative correlation
```

All correlation statements are priors until quantified by the joint simulation/approved dependence model.

## Optimization Requirements
Use integer optimization, stochastic search, or equivalent constrained optimization over the verified slate.

For each proposed lineup calculate:
```text
total_salary
mean_score
median_score
floor_score
ceiling_score
joint_score_distribution_or_approved_approximation
lineup_failure_probability
largest_single_failure_contribution
team/game concentration
role_uncertainty_concentration
correlation_status
```

When building multiple lineups, enforce exposure caps only as research constraints supplied by the user or governed defaults; do not translate them into monetary allocation.

## Weakest-Player Cycle
Before final presentation:
```text
1 identify weakest player by marginal lineup utility and failure contribution
2 search verified salary-compatible replacements
3 rebuild/rescore lineup
4 repeat while a verified improvement exists
5 if roster legality cannot be preserved without filler, reject the lineup
```

Do not preserve a player solely to use all salary or reach a requested lineup count.

## Hard Gates
```text
stale/missing salary slate => DATA_UNOBTAINABLE
roster rules unresolved => REJECT_BAD_STRUCTURE
player lacks governed projection => player ineligible
player inactive or event started => remove player
unresolved duplicate identity => REJECT_DATA_QUALITY
lineup violates salary/eligibility/team rules => REJECT_BAD_STRUCTURE
joint dependence unavailable where material => PORTFOLIO/LINEUP_HOLD or REJECT_DATA_QUALITY
```

## Final Refresh
Immediately before output recheck:
```text
slate status
kickoff times
player active/inactive status
starting QB changes
late injury news
salary/eligibility changes
weather if material
projection timestamps
```

Started games are removed from a pregame build unless the platform's verified late-swap rules specifically preserve locked players and the user requested late-swap analysis.

## Output
```text
NFL DFS LINEUP OPTIMIZER
Mode: CASH | GPP
Platform:
Slate:
Scoring profile:
Salary cap:
Roster rules verified:
Projection model status:
can_execute=false
```

For each lineup:
| Slot | Player | Team | Opp | Salary | Mean | Floor | Ceiling | Failure Path | Status |

Then:
```text
total_salary
projected_mean
projected_floor
projected_ceiling
correlation_summary
largest_failure_path
weakest_player
lineup_status
unresolved_blockers
```

## Acceptance Tests
1. A player cannot enter from external projection alone.
2. Wrong platform salary/roster rules fail closed.
3. QB/receiver correlation is not treated as independence.
4. Multiple same-team pass catchers retain target-share dependence.
5. Cash and GPP objectives produce different optimization behavior.
6. Stale/inactive players are removed at final refresh.
7. Illegal filler lineups are rejected rather than forced.
8. No stake, entry fee, contest submission, or execution action is produced.
9. `can_execute=false` remains invariant.

## One-Line Definition
**WOW NFL DFS Lineup Optimizer is a downstream, platform-rule-aware lineup-construction layer that consumes governed NFL fantasy-score distributions and optimizes legal research-only cash or GPP lineups without altering player probabilities or executing entries.**
