# Skill: wow.mlb-pitcher-failure-path-expert

## Skill Name

**WOW MLB Pitcher Failure-Path Expert**

## Purpose

Estimate how likely an MLB starting pitcher is to fail the workload or performance pathway required by an exact prop. The skill supplements standard projection models by explicitly modeling early hooks, command collapse, pitch inefficiency, health/workload regimes, bullpen pressure, and game disruption.

The skill answers:

```text
What can prevent this pitcher from reaching the workload required for the prop?
How likely is each failure path?
What is the prop probability after those paths are included?
```

It is not an execution or staking tool.

---

## Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
lane_status=RESEARCH_ONLY
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

Maximum standalone ceiling:

```text
MODEL_QUALIFIED_HOLD
```

This skill cannot emit:

```text
MONEY_QUALIFIED
FINAL_APPROVED
PLAYABLE
LOCK
STAKE_READY
```

---

## Supported Markets

Primary scope:

```text
Pitcher strikeouts
Pitching outs recorded
Pitcher walks allowed
Hits allowed
Earned runs allowed
Total runs allowed
Pitch count
Batters faced
Fantasy score when pitcher workload is a material component
First-inning pitches thrown, as a supporting prior only
```

For first-inning pitches thrown, the dedicated batter-by-batter 1IP skill remains controlling.

Excluded unless a separate sport-specific model is loaded:

```text
NRFI/YRFI
team totals
full-game moneyline
batter props
bullpen pitcher props
```

---

## Core Principle

A normal-outing projection is conditional on the pitcher surviving long enough to produce it.

Therefore, always separate:

```text
P(prop | normal workload)
P(normal workload)
P(prop | each failure regime)
P(prop unconditional)
```

Required identity:

```text
P(prop unconditional)
=
Σ P(regime_i) × P(prop | regime_i)
```

No model may report a conditional normal-outing probability as the final probability.

---

## Required Inputs

### Board and settlement

```text
pitcher
team
opponent
game date/time
market type
exact line
side
offer type
board timestamp
settlement rule
starter action requirement
DNP/void/push rule
```

### Pitcher baseline

```text
season and recent innings
pitches per start
batters faced
pitches per plate appearance
strike rate
first-pitch strike rate
zone rate
walk rate
strikeout rate
contact rate
called-strike-plus-whiff rate
hard-hit profile
velocity and movement trend
recent pitch-count progression
rest days
injury or illness status
post-IL/post-break regime
catcher pairing
```

### Manager and bullpen

```text
manager hook tendencies
third-time-through handling
recent bullpen workload
bullpen availability
team leverage context
starter length expectations
```

### Opponent and lineup

```text
confirmed or projected lineup
handedness split
opponent K%
opponent BB%
opponent chase
opponent contact
opponent first-pitch swing
opponent P/PA
lineup depth
injury substitutions
```

### Environment

```text
park
weather
rain-delay risk
wind or temperature when material
umpire only when reliably sourced and material
travel/rest context
```

### Market and model evidence

```text
exact or adjacent reference market
independent projection
source timestamps
source conflicts
model uncertainty
```

---

## Acquisition Status

Every required source path must receive one status:

```text
RETRIEVED
RECONSTRUCTED
PROXY_ONLY
SOURCE_CONFLICT
DATA_UNOBTAINABLE
NOT_APPLICABLE
```

`NOT_CALLED` is not a valid final status.

---

## Failure Regimes

At minimum, model these regimes.

### `NORMAL_EFFECTIVE_OUTING`

Pitcher has typical command, efficiency, health, and leash.

### `INEFFICIENT_SURVIVING_OUTING`

Pitcher remains in the game but elevated pitches per batter reduce innings and downstream volume.

### `EARLY_PERFORMANCE_HOOK`

Pitcher is removed early because of runs, traffic, command, pitch count, or manager decision.

### `COMMAND_COLLAPSE`

Walks, deep counts, and noncompetitive pitches materially alter the prop distribution.

### `HEALTH_OR_WORKLOAD_RESTRICTION`

Illness, post-IL, post-break, velocity concern, pitch cap, or precautionary handling changes the expected outing.

### `ENVIRONMENTAL_DISRUPTION`

Delay, weather, or game interruption changes workload or starter continuation.

### `OPPONENT_EXTENSION`

Patient or contact-heavy lineup extends plate appearances and pitch count without necessarily producing outs or strikeouts.

The model may add regimes but may not delete a material regime without explanation.

---

## Failure Path Score

Define:

```text
failure_path_score
=
P(the pitcher fails to reach the workload needed for the exact prop pathway)
```

Because workload requirements differ by prop, the score must be market-specific.

Examples:

```text
MORE 5.5 strikeouts:
failure means insufficient batters faced/innings for the projected K pathway.

MORE 17.5 pitching outs:
failure means fewer than 18 outs, regardless of strikeout quality.

LESS 2.5 walks:
a command-collapse regime may be a direct losing path rather than only a workload failure.
```

Default tiers:

```text
< 8%      ELITE_FAILURE_RESILIENCE
8–12%     STRONG_FAILURE_RESILIENCE
12–18%    MODERATE_FAILURE_RISK
18–25%    HIGH_FAILURE_RISK
> 25%     REJECT_FAILURE_PATH
```

---

## Core Workflow

1. Load active WOW v16 governance.
2. Verify slate, starter, exact market, and settlement identity.
3. Build raw workload and exact-line ledgers.
4. Classify health, velocity, rest, and workload regime.
5. Verify projected lineup and opponent approach.
6. Model manager leash and bullpen pressure.
7. Assign regime probabilities.
8. Simulate at least 25,000 outings.
9. Score both MORE and LESS at the exact line.
10. Calculate the market-specific failure-path score.
11. Convert conditional probabilities into unconditional probabilities.
12. Apply calibration and uncertainty haircuts.
13. Compare against independent market evidence when available.
14. Return the lowest permitted label.
15. Preserve all blockers and `can_execute=false`.

---

## Simulation Requirements

Minimum simulation count:

```text
25,000 standard runs
50,000 for thin tails, promotional lines, or high regime uncertainty
```

Each simulation must draw:

```text
regime
innings/batters-faced pathway
pitch efficiency
outcomes per batter
manager hook event
health/workload cap when applicable
prop result
```

The model must preserve dependence between:

```text
command and pitch efficiency
traffic and manager hook
batters faced and strikeouts
weather delay and continuation
opponent patience and walks/pitch count
```

Independent multiplication is prohibited where shared causes exist.

---

## Required Outputs

```text
Pitcher / opponent / game
Exact prop / line / side / offer type
Board and source timestamps
Starter confirmation
Projected lineup confirmation
Health/rest/workload regime
Velocity/command status
Manager leash status
Bullpen pressure status

P(NORMAL_EFFECTIVE_OUTING)
P(INEFFICIENT_SURVIVING_OUTING)
P(EARLY_PERFORMANCE_HOOK)
P(COMMAND_COLLAPSE)
P(HEALTH_OR_WORKLOAD_RESTRICTION)
P(ENVIRONMENTAL_DISRUPTION)
P(OPPONENT_EXTENSION)

P(exit_before_3_IP)
P(exit_before_4_IP)
P(exit_before_5_IP)
Expected innings / median / interval
Expected batters faced / median / interval
Expected pitch count / median / interval

P(MORE | normal workload)
P(LESS | normal workload)
P(MORE unconditional)
P(LESS unconditional)
failure_path_score
calibrated probability lower bound
best modeled side
probability gap
model-error haircut
market comparison status
final label
all blockers
can_execute=false
```

---

## Prop-Specific Rules

### Strikeouts

Require a joint distribution of:

```text
batters faced
strikeout probability per batter
innings survival
pitch count
```

A high K% cannot override a high failure-path score.

### Pitching Outs

Require:

```text
out-threshold survival curve
manager hook distribution
third-time-through treatment
bullpen availability
```

### Walks

Command-collapse probability is a direct distribution component. A shorter outing does not automatically make LESS safe.

### Hits and Runs Allowed

Separate:

```text
short clean outing
short collapse outing
long effective outing
long inefficient outing
```

Do not infer the side from workload alone.

### First-Inning Pitches

Call the dedicated 1IP event-tree model. This skill provides:

```text
health prior
command prior
catcher prior
lineup confirmation
uncertainty haircut
```

It may not replace top-four batter modeling or fourth-batter dependency.

---

## Decision Logic

```text
Exact prop or settlement unresolved
=> REJECT_DATA_QUALITY

Starter not confirmed near lock
=> NO_STARTER_CONFIRMATION

Material health/workload regime unresolved
=> MODEL_QUALIFIED_HOLD ceiling

No regime distribution
=> REJECT_DATA_QUALITY

No workload survival curve for a workload-dependent MORE
=> REJECT_DATA_QUALITY

failure_path_score > 25%
=> REJECT_FAILURE_PATH

failure_path_score >= 18%
=> HIGH confidence prohibited

Market contradiction unresolved
=> MODEL_QUALIFIED_HOLD or REJECT_DATA_QUALITY

All research gates pass
=> MODEL_QUALIFIED_HOLD — FAILURE_PATH_AUDITED
```

---

## Prohibited Shortcuts

- No approval from season averages alone.
- No approval from L5/L10 hit rate alone.
- No assumption that an ace label guarantees workload.
- No assumption that a low line is safe.
- No assumption that a short outing always favors LESS.
- No assumption that a collapse always favors MORE.
- No stale projected lineup presented as confirmed.
- No hidden health, velocity, pitch-cap, or post-break regime.
- No normal-outing probability presented as unconditional.
- No invented umpire, weather, or manager effect.
- No independent multiplication of correlated failure events.
- No stake or execution language.

---

## Required Output Format

```text
WOW MLB PITCHER FAILURE-PATH AUDIT

Mode: RESEARCH_ONLY
Pitcher:
Opponent:
Game:
Exact prop:
As of:
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS

REALITY CHECK
- Starter:
- Lineup:
- Health/workload regime:
- Velocity/command:
- Manager leash:
- Bullpen pressure:
- Weather/delay:

REGIME DISTRIBUTION
- Normal effective outing:
- Inefficient surviving outing:
- Early performance hook:
- Command collapse:
- Health/workload restriction:
- Environmental disruption:
- Opponent extension:

WORKLOAD DISTRIBUTION
- P(exit before 3 IP):
- P(exit before 4 IP):
- P(exit before 5 IP):
- Innings projection:
- Batters faced projection:
- Pitch-count projection:

PROP MODEL
- P(MORE | normal workload):
- P(LESS | normal workload):
- P(MORE unconditional):
- P(LESS unconditional):
- Failure Path Score:
- Calibrated lower bound:
- Best modeled side:

DECISION
- Final label:
- Main failure path:
- Main supporting path:
- Blockers:
- can_execute=false
```

---

## Postmortem Ledger

After settlement, log:

```text
predicted regime probabilities
failure_path_score
predicted workload distribution
actual innings
actual batters faced
actual pitch count
actual prop result
observed failure category
whether pregame evidence supported that category
calibration error
```

Do not rewrite a surprising result as predictable after the fact.

---

## Acceptance Tests

1. A pitcher with excellent K rate but 22% workload-failure probability cannot receive HIGH confidence.
2. MORE strikeouts uses unconditional probability after early exits.
3. LESS walks does not automatically benefit from early-exit risk when command collapse is material.
4. Pitching-outs props include manager hook and bullpen pressure.
5. Health and post-IL regimes widen uncertainty when matching samples are unavailable.
6. Every output displays regime probabilities and failure-path score.
7. Conditional and unconditional probabilities are both shown.
8. First-inning pitch-count props remain controlled by the 1IP event-tree skill.
9. Missing exact settlement rules causes a fail-closed result.
10. `can_execute=false` appears in every response.

---

## Activation Prompt

> Activate WOW MLB Pitcher Failure-Path Expert. Verify the exact pitcher prop, starter, settlement rules, current lineup, health/workload regime, velocity and command, manager leash, bullpen pressure, opponent approach, and environmental risk. Build a multi-regime workload simulation, calculate conditional and unconditional MORE/LESS probabilities, publish the market-specific Failure Path Score, apply calibration and fail-closed WOW v16 governance, and return research-only output with can_execute=false.

---

## One-Line Definition

**WOW MLB Pitcher Failure-Path Expert is a multi-regime workload and early-exit risk model that converts normal-outing pitcher projections into unconditional, failure-aware probabilities under WOW v16 Clean Core.**


---

# v2 Controlling Patch — Directional Regression Firewall

This section supersedes any earlier confidence behavior when it is more restrictive.

## Directional Calibration Lanes

Every pitcher prop must be assigned to exactly one lane:

```text
K_MORE
K_LESS
OUTS_MORE
OUTS_LESS
WALKS_MORE
WALKS_LESS
HITS_MORE
HITS_LESS
RUNS_MORE
RUNS_LESS
PITCH_COUNT_MORE
PITCH_COUNT_LESS
BATTERS_FACED_MORE
BATTERS_FACED_LESS
```

Calibration, hit rate, Brier score, and failure categories must be maintained separately by lane.

A combined “MLB pitcher props” success rate may be shown only as a broad summary. It cannot promote a directional lane.

---

## Temporary Lane State

```text
MLB_K_LESS=WATCH_ONLY
MLB_OUTS_MORE=MODEL_QUALIFIED_HOLD ceiling
```

Exit requires:

```text
minimum_unique_K_LESS_rows=10
minimum_unique_OUTS_rows=10
all rows reconciled=true
directional_calibration_review=PASS
failure_path_review=PASS
```

Exact duplicates and alternate thresholds on the same pitcher-game count as one calibration observation.

---

## K LESS Support Decomposition

Every strikeout LESS model must report:

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
P(LESS and short-outing pathway is primary cause)
/
P(LESS)
```

If `P(LESS)=0`, set the share to zero and preserve the zero probability.

Hard rules:

```text
short_outing_support_share > 0.50
=> HIGH confidence prohibited
=> label=MLB_K_LESS_WATCH

normal-outing K projection above line
and LESS depends primarily on early exit
=> ROLE_OF_FAILURE_PATH_MATERIAL
=> WATCH_ONLY
```

The early-exit branch is only one pathway. It may not erase a normal or extended outing in which the pitcher clears the strikeout threshold.

---

## Required K Distribution

For strikeouts, model jointly:

```text
innings
batters faced
strikeout probability per batter
pitch count
manager hook
health/workload regime
opponent contact and chase
```

Required outputs:

```text
P(K = 0)
P(K = 1)
...
P(K >= relevant tail)
strikeout_mean
strikeout_median
strikeout_interval
P(MORE)
P(LESS)
P(PUSH if applicable)
```

Recent strikeout totals alone are insufficient.

---

## Outs MORE Workload Survival

For pitching-outs MORE, add:

```text
required_outs
required_innings_equivalent
P(reach_required_outs)
P(exit_before_required_innings)
P(third_time_through)
manager_hook_probability
bullpen_pressure_probability
pitch_count_cap_probability
```

The final probability must be unconditional across all regimes.

Hard rules:

```text
required_out_survival_lower_bound < active_floor
=> NO_LOW_PROBABILITY

health or pitch cap unresolved
=> MODEL_QUALIFIED_HOLD ceiling

P(MORE | normal workload) shown as final P(MORE)
=> MODEL_INVALID
```

---

## Cross-Ticket Requirement

Before any final card, call:

```text
wow.cross-ticket-exposure-governor
```

The same pitcher-game directional thesis may appear on at most one proposed card.

Examples:

```text
Boyd LESS 4.5 Ks on Flex and Power
=> exact duplicate

Pitcher X LESS 4.5 and LESS 5.0 Ks on two cards
=> alternate-threshold duplicate

Pitcher X MORE outs and MORE strikeouts
=> shared workload-survival exposure requiring dependence review
```

---

## Added Required Outputs

```text
directional_lane
directional_forward_test_status
short_outing_support_share_if_K_LESS
required_out_survival_lower_bound_if_OUTS_MORE
duplicate_group_id
cross_ticket_status
```

---

## Added Postmortem Fields

```text
predicted_strikeouts
actual_strikeouts
predicted_outs
actual_outs
short_outing_support_share
required_out_survival_lower_bound
directional_lane
directional_calibration_error
duplicate_group_id
```

Allowed v2 failure categories:

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
UNRESOLVED
```

---

## v2 Acceptance Tests

1. K LESS cannot receive HIGH when more than half its win probability comes from short-outing assumptions.
2. K LESS and K MORE maintain separate calibration ledgers.
3. Outs MORE reports required-out survival, not only expected innings.
4. Normal-workload conditional probability is never presented as unconditional.
5. Duplicate pitcher theses across cards are retained once.
6. Alternate thresholds count once in calibration.
7. Temporary WATCH_ONLY status remains until the forward-test milestone passes.
8. `can_execute=false` remains enforced.
