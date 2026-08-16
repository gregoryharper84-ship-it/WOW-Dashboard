# Skill: wow.wnba-composite-prop-expert

## Skill Name

**WOW WNBA Composite Prop Expert**

## Short Description

Model WNBA points, rebounds, assists, points+rebounds, points+assists, rebounds+assists, and PRA as a role-conditioned joint distribution. Compare every available component and composite market for the same player, identify whether the threshold has multiple reliable paths to clear, and fail closed on unstable minutes, teammate-status uncertainty, outlier contamination, stale sources, or duplicate exposure.

---

## Purpose

This skill answers:

```text
Which available WNBA stat family gives this player the cleanest
high-hit-probability pathway at the exact current board line?
```

It is designed to prevent two common errors:

1. Choosing PRA merely because a player is productive overall.
2. Choosing a single component when a composite line provides a materially safer role-valid pathway.

The skill must compare all verified current-board alternatives for the player:

```text
Points
Rebounds
Assists
Points + Rebounds
Points + Assists
Rebounds + Assists
Points + Rebounds + Assists
```

The selected market must be the strongest probability profile after role, minutes, matchup, game script, component dependence, offer type, and uncertainty are modeled.

---

## Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
lane_status=RESEARCH_ONLY_FORWARD_TEST
can_execute=false
stake=0
money_label_allowed=false
final_approval_allowed=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

Maximum standalone ceiling:

```text
MODEL_QUALIFIED_HOLD
```

Until the forward-test milestone is met, no output may exceed:

```text
WNBA_COMPOSITE_WATCH
```

for a new unvalidated market family or unsupported promotional structure.

This skill may not emit:

```text
MONEY_QUALIFIED
FINAL_APPROVED
LOCK
BEST_BET
STAKE_READY
```

---

## Supported Markets

Primary scope:

```text
Full-game points
Full-game rebounds
Full-game assists
Full-game points + rebounds
Full-game points + assists
Full-game rebounds + assists
Full-game PRA
```

Supported offer types:

```text
STANDARD
GOBLIN_OR_DISCOUNTED
DEMON_OR_ELEVATED
PROMOTIONAL
```

Promotional lines require exact board verification and payout context. A discounted line improves the threshold but does not repair role, source, or data-quality defects.

Excluded unless a separate dedicated model is loaded:

```text
First quarter
First half
Fantasy score
Three-pointers
Blocks
Steals
Turnovers
Defensive rebounds
Offensive rebounds
Double-doubles
Combo outcomes with nonstandard settlement
```

A defensive-rebound or offensive-rebound market may be reviewed only through a dedicated rebound-opportunity extension. It may not be silently treated as total rebounds.

---

## Core Principle

WNBA composite stats are not independent.

Points, rebounds, and assists share common causes:

```text
minutes
role
usage
pace
game competitiveness
teammate availability
rotation
foul trouble
injury
opponent scheme
possession volume
```

The controlling model must therefore build a joint distribution:

```text
(P, R, A) ~ role-conditioned joint model
```

Then derive:

```text
P(Points > line)
P(Rebounds > line)
P(Assists > line)
P(Points + Rebounds > line)
P(Points + Assists > line)
P(Rebounds + Assists > line)
P(PRA > line)
```

Independent multiplication of component probabilities is prohibited.

---

## Required Inputs

### Board and settlement

```text
player
team
opponent
event_date
event_time
exact_stat_family
exact_line
direction
offer_type
board_timestamp
settlement_rule
DNP_rule
push_rule
overtime_rule
```

### Player reality

```text
official_active_status
expected_starting_status
projected_minutes
minutes_distribution
current_role
primary_position
recent_role_changes
foul-risk profile
return-from-injury status
minutes restriction
```

### Teammate and rotation context

```text
primary_ball_handler status
primary_scorer status
primary_rebound competitor status
starting lineup
bench rotation
replacement player
on_off role split
usage redistribution
assist redistribution
rebound redistribution
```

### Historical ledger

```text
current-season game log
L10 exact-line results
L5 exact-line results
role-matched L10
role-matched L5
minutes in each game
starter/bench state
teammate-context state
opponent
pace context
blowout or overtime marker
```

### Component data

```text
points per minute
rebounds per minute
assists per minute
usage rate
potential assists
assist chances
rebound chances
shot attempts
free-throw attempts
touches
time of possession when material
component standard deviations
component covariance or correlation
```

### Matchup and game context

```text
projected pace
projected possessions
opponent defensive profile
opponent rebounding profile
opponent assist suppression
position or role matchup
spread or projected margin as context
blowout risk
rest
travel
back-to-back status
venue
```

### Market and model evidence

```text
current board alternatives for same player
exact or adjacent sportsbook markets
two-way market when available
independent projection
source timestamps
source conflicts
```

---

## Source Priority

Use the strongest available evidence for each field:

1. Official WNBA, team, or league status and game records.
2. Official or high-quality play-by-play and box-score data.
3. Current live game-log headers or current-season database queries.
4. Reliable injury and lineup reporting.
5. High-quality statistical databases.
6. Current sportsbook or market data.
7. Reputable projections.
8. Aggregators and narrative blurbs only as secondary context.

### Stale Source Rule

Narrative blurbs may not override current game-log data.

If two season averages or role figures differ by more than 15%:

```text
SOURCE_CONFLICT
final ceiling = WNBA_COMPOSITE_WATCH
```

Use the most recent role-valid game-log-derived figure only after documenting the conflict.

---

## Acquisition Status

Every required path must receive one status:

```text
RETRIEVED
RECONSTRUCTED
PROXY_ONLY
SOURCE_CONFLICT
DATA_UNOBTAINABLE
NOT_APPLICABLE
```

`NOT_CALLED` is prohibited in the final report.

---

## Zero-Gate: Pre-Analysis Slate Purge

Before any modeling:

1. Confirm the player's team has a game on the session date.
2. Confirm the event has not completed, postponed, or canceled.
3. Confirm the player belongs to the listed team.
4. Confirm the board line is tied to that event.
5. Remove stale screenshots and mismatched dates.

Failure label:

```text
WNBA_SLATE_PURGE
```

No probability may be produced for a purged row.

---

## Role and Minutes Gate

A composite market is highly sensitive to playing time. The model must estimate a minutes distribution, not only a point estimate.

Required:

```text
minutes_mean
minutes_median
minutes_floor_10th_percentile
minutes_ceiling_90th_percentile
P(minutes >= role_threshold)
```

The role threshold should reflect the minutes normally required for the exact market pathway.

### Role Stability Labels

```text
ROLE_STABLE
Confirmed role and a narrow minutes distribution.

ROLE_EXPANDED_CONFIRMED
A teammate absence or lineup change creates a verified expanded role.

ROLE_REDUCED_CONFIRMED
A returning teammate or rotation change reduces the role.

ROLE_VOLATILE
Starter/bench, rotation, foul, or workload uncertainty materially widens minutes.

ROLE_UNRESOLVED
Current role cannot be verified.
```

Hard rules:

```text
ROLE_UNRESOLVED
=> NO_ROLE_OR_STATUS

P(minutes >= role_threshold) < 0.70
=> HIGH confidence prohibited

material minutes restriction
=> WNBA_COMPOSITE_WATCH ceiling
```

---

## Primary Teammate Availability Gate

For every player, identify the teammate whose absence or return most affects:

```text
usage
ball handling
assist opportunity
rebound competition
minutes
```

Record:

```text
primary_teammate
status
role_effect_direction
historical matching-role sample
```

If the teammate is OUT, questionable, returning, or minutes-limited, the ledger must be split by matching role context.

Using an all-games average when today's role clearly matches a different sub-ledger is a data-validation failure.

Required label:

```text
ROLE_SPLIT_LEDGER_APPLIED
```

---

## Historical Ledger Rules

Build:

```text
raw_L10
raw_L5
role_matched_L10
role_matched_L5
```

Use L5 only as a trend modifier.

### Outlier Isolation

When the L10 mean differs from the L5 mean by more than 20%:

1. Identify whether one game drives the divergence.
2. Remove at most one clearly documented outlier.
3. Recalculate a role-valid L9.
4. Compare the verdict before and after exclusion.

Required outputs:

```text
outlier_detected
outlier_reason
L10_mean
L10_median
L9_mean_if_applicable
verdict_changed_by_outlier
```

If the verdict changes:

```text
OUTLIER_CONTAMINATED
high confidence prohibited
```

### Assist Volatility Rule

For a non-primary playmaker:

```text
assist_cv = assist_standard_deviation / assist_mean
```

Flag:

```text
HIGH_VOLATILITY_ASSIST_COMPONENT
```

when either is true:

```text
assist_std > assist_mean
single-game assists > 2 × role-matched assist median
```

For a high-volatility assist component, use a robust estimator such as the median or a shrinkage posterior rather than the raw mean.

---

## Joint Distribution Model

### Minimum Method

The model must simulate at least:

```text
25,000 player-game outcomes
```

Use at least:

```text
minutes distribution
role-conditioned per-minute rates
pace/possession adjustment
opponent adjustment
component covariance
game-script mixture
```

Recommended mixture regimes:

```text
NORMAL_COMPETITIVE
BLOWOUT_REDUCED_MINUTES
FOUL_TROUBLE
ROLE_EXPANSION
ROLE_REDUCTION
IN_GAME_INJURY_OR_LIMITATION
OVERTIME
```

Do not force every regime to be material. Assign zero only with explanation.

### Simulation Sequence

For each simulation:

1. Draw a regime.
2. Draw minutes.
3. Draw team possessions.
4. Draw role and usage state.
5. Draw correlated points, rebounds, and assists.
6. Apply exact settlement boundary.
7. Record every component and composite result.

### Required Distribution Outputs

```text
points_mean
points_median
points_interval
rebounds_mean
rebounds_median
rebounds_interval
assists_mean
assists_median
assists_interval
PRA_mean
PRA_median
PRA_interval
component_correlation_matrix
```

---

## Multi-Path Coverage

A composite line should not be called robust merely because its mean is high.

For each winning simulation, identify the largest component contribution:

```text
dominant_component = argmax(points, rebounds, assists)
```

Calculate:

```text
points_dominant_win_share
rebounds_dominant_win_share
assists_dominant_win_share
largest_dominant_share
```

Default provisional classification:

```text
THREE_PATH_STABLE
all three dominant-win shares >= 0.15

TWO_PATH_STABLE
exactly two dominant-win shares >= 0.15

ONE_PATH_DEPENDENT
largest dominant-win share > 0.70

ROLE_FRAGILE
role or minutes uncertainty is the largest failure driver

LINE_TOO_EFFICIENT
calibrated lower bound does not clear the active floor
```

These thresholds are provisional and must be calibrated in the forward-test ledger.

`ONE_PATH_DEPENDENT` does not automatically reject a market, but it blocks the highest confidence tier unless the dominant component itself is independently strong.

---

## Stat-Family Comparison

For every verified current-board alternative, output:

```text
raw_probability
calibrated_probability
calibrated_probability_lower_bound
projection_to_line_gap
role_sensitivity
outlier_sensitivity
market_contradiction
offer_type
```

Rank by:

1. Calibrated probability lower bound.
2. Role stability.
3. Lower outlier sensitivity.
4. Lower game-script sensitivity.
5. Cleaner settlement and market support.
6. Lower duplicate exposure.

Do not default to PRA.

A component market may outrank PRA when:

```text
the component threshold is materially softer
the component has lower variance
the composite line is role- or game-script-sensitive
the component has cleaner market support
```

A composite may outrank a component when:

```text
multiple stable pathways exist
component covariance is modeled
the exact line has a favorable cushion
role and minutes are stable
```

---

## Bidirectional Rule

Always score both sides of every verified market.

A failed MORE does not imply LESS.

Required:

```text
P(MORE)
P(LESS)
probability_gap
best_modeled_side
no_edge_status
```

Whole-number lines require verified push handling.

---

## Promotional Offer Rule

For Goblin, Demon, discounted, or boosted lines:

```text
exact line verified
offer type verified
slip payout context documented
settlement unchanged
probability recalculated at exact line
```

A promotional threshold cannot upgrade:

```text
ROLE_UNRESOLVED
SOURCE_CONFLICT
DATA_UNOBTAINABLE
OUTLIER_CONTAMINATED
```

Track standard and promotional results separately in calibration.

---

## Duplicate Exposure Gate

Before final output, call:

```text
wow.cross-ticket-exposure-governor
```

The same player-game may not appear across multiple proposed cards as if it were independent evidence.

Examples of shared latent exposure:

```text
Points and PRA for the same player
PRA at 17.5, 18.5, and 19.0 for the same player
Assists and points+assists for the same player
Rebounds and points+rebounds for the same player
```

The governor decides the final cross-card ceiling.

---

## Calibration and Forward-Test Gate

Initial status:

```text
WNBA_COMPOSITE_FORWARD_TEST=ACTIVE
```

Required milestone:

```text
minimum_unique_graded_player_games=20
duplicates_counted_once=true
standard_and_promotional_separated=true
role_status_review=PASS
DNP_handling_review=PASS
calibration_review=PASS
Brier_score_reviewed=true
```

Until the milestone is met:

```text
highest label = MODEL_QUALIFIED_HOLD
```

A duplicate threshold on the same player-game is one calibration observation, not several.

DNP or void protection is a settlement outcome, not a projection hit.

---

## Allowed Labels

```text
WNBA_COMPOSITE_MODEL_READY
WNBA_COMPOSITE_WATCH
WNBA_COMPOSITE_SCOUT
YES_MODEL_QUALIFIED
YES_MODEL_QUALIFIED_MODIFIED
NO_LOW_PROBABILITY
NO_ROLE_OR_STATUS
NO_DATA_QUALITY
NO_MARKET_CONTRADICTION
NO_BAD_STRUCTURE
NO_DUPLICATE_EXPOSURE
WNBA_SLATE_PURGE
OUTLIER_CONTAMINATED
HIGH_VOLATILITY_ASSIST_COMPONENT
ROLE_SPLIT_LEDGER_APPLIED
```

Maximum standalone ceiling:

```text
MODEL_QUALIFIED_HOLD
```

---

## Decision Logic

```text
if slate invalid:
    WNBA_SLATE_PURGE

elif exact board line unresolved:
    NO_DATA_QUALITY

elif player or role unresolved:
    NO_ROLE_OR_STATUS

elif source conflict material:
    WNBA_COMPOSITE_WATCH

elif outlier changes verdict:
    WNBA_COMPOSITE_WATCH

elif joint distribution unavailable:
    NO_DATA_QUALITY

elif calibrated lower bound < active floor:
    NO_LOW_PROBABILITY

elif market contradiction unresolved:
    NO_MARKET_CONTRADICTION

elif cross-ticket duplicate governor rejects:
    NO_DUPLICATE_EXPOSURE

else:
    YES_MODEL_QUALIFIED
    ceiling=MODEL_QUALIFIED_HOLD
```

---

## Required Output Format

```text
WOW WNBA COMPOSITE PROP AUDIT

Mode: RESEARCH_ONLY_FORWARD_TEST
Player:
Team / opponent:
Event:
As of:
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

### Reality and Role

```text
Active status:
Starting status:
Expected minutes:
Minutes interval:
Role label:
Primary teammate:
Teammate status:
Role-split ledger:
DNP/push rule:
```

### Component Model

| Component | Mean | Median | Interval | Exact Board Line | P(MORE) | P(LESS) | Lower Bound |
|---|---:|---:|---|---:|---:|---:|---:|
| Points | | | | | | | |
| Rebounds | | | | | | | |
| Assists | | | | | | | |
| P+R | | | | | | | |
| P+A | | | | | | | |
| R+A | | | | | | | |
| PRA | | | | | | | |

### Robustness Audit

```text
Component covariance:
Points-dominant win share:
Rebounds-dominant win share:
Assists-dominant win share:
Multi-path class:
Outlier status:
Assist-volatility status:
Blowout sensitivity:
Role sensitivity:
Primary failure path:
```

### Decision

```text
Best available stat family:
Exact side and line:
Raw probability:
Calibrated probability:
Calibrated lower bound:
Final label:
Cross-ticket status:
Forward-test status:
Blockers:
can_execute=false
```

---

## Postmortem Ledger

For every settled unique player-game, record:

```text
player
event
role at projection
projected minutes distribution
actual minutes
projected P/R/A distribution
actual P/R/A
selected stat family
exact line
direction
offer type
predicted probability
calibrated lower bound
settled result
DNP_or_void
dominant predicted success path
observed success_or_failure path
source quality
duplicate_group_id
calibration error
```

Do not count multiple thresholds for the same player-game as independent calibration rows.

---

## Acceptance Tests

1. PRA cannot qualify from an L5/L10 hit rate alone.
2. Points, rebounds, and assists are modeled jointly.
3. A role change requires a matching-role sub-ledger.
4. A material teammate status change triggers a rerun.
5. A one-game outlier that changes the verdict caps the row at WATCH.
6. A non-playmaker assist outlier is not allowed to inflate a composite mean.
7. A DNP is logged as DNP/void, not a projection hit.
8. PRA and assists for the same player are compared before selection.
9. Three alternate PRA thresholds for one player-game count as one calibration observation.
10. A discounted threshold cannot repair unresolved role status.
11. A composite line with one dominant success path is labeled ONE_PATH_DEPENDENT.
12. The same player-game cannot be duplicated across multiple proposed cards without governor review.
13. Every run shows raw, calibrated, and lower-bound probability.
14. No output exceeds MODEL_QUALIFIED_HOLD.
15. `can_execute=false` appears in every output.

---

## Activation Prompt

> Activate WOW WNBA Composite Prop Expert. Load WOW v16 Clean Core, verify the exact current WNBA board line and settlement, run the pre-analysis slate purge, confirm player and primary-teammate status, build role-matched L5/L10 ledgers with outlier isolation, construct a role-conditioned joint points/rebounds/assists simulation, compare every verified component and composite market for the player, classify multi-path coverage, call the cross-ticket exposure governor, apply the 20-unique-player-game forward-test gate, and return research-only output with can_execute=false.

---

## One-Line Definition

**WOW WNBA Composite Prop Expert is a role-conditioned joint P/R/A distribution and stat-family selection skill that finds the cleanest verified WNBA component or composite threshold while controlling minutes, teammate status, outliers, covariance, promotions, duplicates, and calibration under WOW v16 Clean Core.**
