# WOW-PATCH-2026-09-08-SOCCER-PASSES-AND-NFL-DFS

## Status
```text
status=ACTIVE_PROJECT_CONTRACT_ON_PATCH_BRANCH
framework=V17_ACTIVE
activation_date=2026-09-08
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose
Fix two model-routing gaps:

1. Soccer Passes Attempted props were vulnerable to a research shortcut in which accurate/completed passes per 90, or a completion-rate reconstruction, could be treated too much like an exact-line model.
2. NFL DFS/fantasy-score props need a dedicated scoring-adapter and joint opportunity simulation instead of generic football prop reasoning or external DFS projections.

This patch changes model contracts and routing. It does not weaken V17 terminal governance.

# PATCH-A — Soccer Passes Attempted

## Controlling Specialist
```text
market_family=SOCCER_PASSES_ATTEMPTED
controlling_specialist=wow.soccer-passes-attempted-expert
hydration_profile=SOCCER_PASSES_ATTEMPTED
```

## Hard Regression Ban
```text
accurate_passes_per90 > board_line => governed MORE
accurate_passes_per90 < board_line => governed LESS
```

is prohibited.

If accurate passes and completion percentage are used:
```text
reconstructed_attempts_per90 = accurate_passes_per90 / completion_rate
source_status=RECONSTRUCTED
maximum_without_specialist=RESEARCH_INTEREST
```

The reconstruction is a research prior only.

## Required Model Form
At minimum the controlling model must jointly represent:
```text
P(start)
minutes distribution
team pass-attempt distribution
player share distribution conditional on role/formation
opponent possession/press/block environment
match score-state transitions
substitution hazard
red-card/disruption states
```

The exact publication target is:
```text
P(player_pass_attempts > exact_line)
P(player_pass_attempts < exact_line)
```
according to the exact platform boundary/settlement rules.

## Current/Prior Season Rule
Prior-season rates may support the model but cannot silently dominate when current team, manager, formation, position, role, or substitution pattern changes.

# PATCH-B — NFL DFS Fantasy Score

## Controlling Specialist
```text
market_family=NFL_DFS_FANTASY_SCORE
controlling_specialist=wow.nfl-dfs-fantasy-score-expert
hydration_profile=NFL_DFS_FANTASY_SCORE
```

## Exact Scoring Adapter
A verified scoring profile is mandatory. No generic PPR/half-PPR/standard assumption and no cross-platform scoring substitution is allowed.

```text
missing_scoring_profile => MODEL_INPUTS_INSUFFICIENT
```

## Required Model Form
Simulate the team/game environment and position-specific opportunity before converting outcomes to fantasy score.

Required dependencies include:
```text
team plays and pace
pass/rush split
red-zone/scoring opportunities
QB starter/status
offensive-line context
RB carry/route/target shares
WR/TE route/target/air-yard shares
TD allocation
game script
weather when material
injury/inactive role redistribution
QB-receiver and teammate opportunity dependence
```

External fantasy projections, FPPG, recent hit rates, or sportsbook totals are evidence/prior inputs only.

# PATCH-C — NFL DFS Lineup Construction

Lineup optimization is downstream of valid player projections:
```text
player model
→ calibration
→ final refresh
→ verified salary/roster/scoring adapter
→ wow.nfl-dfs-lineup-optimizer
```

The optimizer may build research-only CASH or GPP lineups, but it may not invent salaries, roster rules, ownership, projections, stake sizes, or contest entries.

Player probability/projection packages are immutable inputs to lineup construction. The optimizer cannot alter them to make a lineup fit.

# V17 Routing Changes

Add:
```text
Soccer Passes Attempted props
→ WOW prop lane
→ wow.soccer-passes-attempted-expert

NFL Fantasy Score / DFS Points player props
→ WOW prop lane
→ wow.nfl-dfs-fantasy-score-expert

NFL salary-cap lineup construction
→ downstream wow.nfl-dfs-lineup-optimizer after governed projection packages
```

LLP remains the controlling route for NFL team/event winners/upsets, not player DFS projections.

# Typed Failure Semantics

Use V17 semantics:
```text
controlling specialist capability missing => MODEL_UNAVAILABLE
required inputs missing => MODEL_INPUTS_INSUFFICIENT
specialist invoked and throws/times out => preserve scorer/completion failure
malformed probability package => MODEL_OUTPUT_INVALID
```

Do not rewrite all failures as MODEL_UNAVAILABLE.

# Probability Governance

For both new prop lanes:
```text
external_projection != governed_model_probability
recent_hit_rate != governed_model_probability
sportsbook_implied_probability != governed_model_probability
per90_rate != governed_model_probability
```

Ranked governed output requires the exact controlling specialist and a valid numeric probability package, including dynamic calibration/lower bound where governed.

# Market Separation

A completed sporting probability may be preserved even when exact downstream market/value evidence is missing. Missing market price may block market/value publication but must not erase a completed probability package.

# Final Refresh

Soccer rechecks:
```text
kickoff status
confirmed lineup/player availability
role/formation changes
exact line and settlement
```

NFL rechecks:
```text
kickoff status
starting QB
player active/inactive status
skill-player inactives
material OL changes
weather when material
exact fantasy line
verified scoring profile/settlement
```

Pregame probabilities cannot survive event start without a dedicated live model rerun.

# Immutable Prediction / Postmortem

Persist exact pregame identity, line, direction, scoring/settlement profile, raw probability, calibrated probability, lower/upper bounds, failure-path score, model timestamp, terminal label, and sources. Grade only the exact immutable prediction after settlement.

# Acceptance Tests

1. Accurate passes/90 cannot directly approve a Passes Attempted side.
2. Completion-rate reconstruction is `RECONSTRUCTED` evidence only.
3. Soccer minutes distribution is required.
4. Soccer opponent/team-volume/game-state effects are numerical.
5. NFL external DFS projection cannot become governed fantasy-score probability.
6. NFL exact scoring adapter is mandatory.
7. QB/receiver and teammate opportunity dependence is preserved.
8. NFL active/inactive and starting-QB changes trigger rerun/final-refresh removal.
9. Lineup optimizer cannot admit a player without a governed projection package.
10. Cash/GPP lineup objectives remain separate from player probability.
11. No downstream market gap erases a completed sporting probability.
12. `can_execute=false` remains invariant.

# Runtime Status Contract
Until backend implementation and deployment are independently verified, report separately:
```text
REPOSITORY_GOVERNANCE = patch/PR state
MODEL_CAPABILITY = specialist contract present; runtime scorer support requires verification
BACKEND_RUNTIME = do not infer deployment from repository changes
LIVE_GPT_EDITOR_SYNC = separate verification required
```

## One-Line Definition
**This patch replaces the soccer pass-per-90 shortcut with an exact Passes Attempted simulation contract and adds a governed, exact-scoring NFL DFS fantasy-score lane plus downstream lineup optimization without weakening V17 terminal governance.**
