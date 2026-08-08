# Skill: wow.llp-moneyline-probability-expert

## Skill Name

**LLP Moneyline Probability Expert**

## Short Description

Find the most likely outright winners and the most credible underdog upsets across PrizePicks, Kalshi, sportsbooks, and supported sports feeds. Rank by calibrated win probability—not edge, payout, or price—and enforce dry-run-only, fail-closed verification.

---

## Purpose

Identify the highest-probability **moneyline-style winners** and **moneyline-style upsets** across all major sports.

This skill answers two separate questions with equal priority:

1. **Which eligible favorite is most likely to win outright?**
2. **Which eligible underdog is most likely to win outright?**

The skill does **not** optimize for:

- betting edge;
- expected value;
- payout;
- price efficiency;
- closing-line value;
- staking;
- bankroll allocation;
- execution.

A team may rank first even when its PrizePicks multiplier, Kalshi contract price, or sportsbook moneyline offers poor value. Probability and price are deliberately separated.

```text
optimization_mode = OUTRIGHT_WIN_PROBABILITY_ONLY
winner_lane_weight = 50%
upset_lane_weight = 50%
edge_optimization = false
price_optimization = false
ev_optimization = false
staking = false
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

---

## Core Principle

**Most likely does not mean best value.**

The skill may use market consensus as:

- a favorite/underdog classifier;
- a prior probability anchor;
- a contradiction detector;
- a stale-information detector;
- a source for removing vig before calibration.

It may not:

- rank candidates by model edge;
- favor a cheaper contract because it pays more;
- reject a likely winner merely because the payout is poor;
- call an underdog attractive because it is mispriced;
- produce stake sizing or execution instructions.

---

## Supported Platforms

The skill scans all available supported platforms and source paths, including:

```text
PrizePicks team winner selections
Kalshi sports winner contracts
Traditional sportsbook moneylines
Prediction-market winner contracts
Official league and team sources
Independent statistical and projection sources
```

Platform availability does not guarantee qualification. Every selection must pass identity, event, settlement, status, and probability requirements.

### PrizePicks

Use for verified team winner or equivalent outright-result selections shown on the current PrizePicks board.

Required:

- exact team;
- opponent;
- league;
- event date and time;
- visible multiplier or offer type;
- exact settlement meaning;
- board timestamp;
- current event status.

PrizePicks multiplier is recorded for context but is not part of the probability ranking.

### Kalshi

Use for verified sports contracts only when the Kalshi sports lane reports:

```text
signal = INVENTORY_READY
```

Hard stop:

```text
signal = INVENTORY_EMPTY
=> stop Kalshi sports scan
=> do not audit contracts
=> do not inject probabilities
=> label KALSHI_INVENTORY_EMPTY
```

Kalshi contract requirements:

- exact ticker or contract ID;
- exact title;
- event and participant identity;
- YES/NO side;
- unambiguous settlement language;
- official settlement source;
- market-open status;
- current event status;
- valid inventory response.

Price and orderbook information may be displayed for context, but they do not influence the probability-only ranking.

```text
can_execute = false
```

### Sportsbooks and Other Markets

Sportsbooks may supply:

- current favorite/underdog classification;
- consensus implied probability;
- line movement;
- injury or lineup contradiction signals;
- comparable market evidence.

Sportsbook price may be normalized into a no-vig prior, but no candidate is ranked by edge against that price.

---

## Supported Sports

Cover all major sports through sport-specific modules:

```text
MLB
NBA
WNBA
NFL
NCAAF
NCAAB
NHL
Soccer
Tennis
Golf
MMA
Boxing
Major international leagues and tournaments
```

A sport may be scanned but remain unqualified when the necessary data, rules, lineup, status, or model coverage is insufficient.

---

## Market Scope

Included:

- full-game moneyline;
- match winner;
- series winner when explicitly requested;
- game winner;
- bout winner;
- tournament head-to-head winner;
- outright event winner when the field model is supported;
- Kalshi YES/NO contracts that resolve directly to an outright sports winner;
- PrizePicks team winner selections.

Excluded by default:

- spreads;
- totals;
- player props;
- first-inning markets;
- quarter, half, period, or set winners;
- double chance;
- draw-no-bet;
- futures;
- exact score;
- multi-condition contracts;
- parlays or combos;
- live/in-play markets unless the user explicitly requests a live scan.

Derivative markets may not be substituted for an outright winner without clearly labeling the market change.

---

## Favorite and Upset Definitions

### Favorite

A participant is a favorite when the current consensus no-vig probability is greater than 50% in a two-outcome market.

For a three-outcome market such as soccer:

- use the highest individual win probability as the favorite;
- preserve draw as a separate outcome;
- do not convert “team or draw” into a team win.

### Underdog / Upset Candidate

A participant is an underdog when its current consensus no-vig **outright win probability** is below its opponent's or below 50% in a two-outcome market.

An upset occurs only when that underdog wins outright under the exact platform settlement rules.

The skill must never redefine a near-even favorite as an upset merely because one platform briefly lists a different price.

### Classification Source Priority

1. Vig-free consensus from multiple current sportsbooks
2. Reliable exchange or prediction-market consensus
3. Single reputable sportsbook
4. PrizePicks multiplier relationship
5. Kalshi contract price
6. Manual or screenshot classification

If sources disagree on favorite status:

```text
FAVORITE_STATUS_CONFLICT
```

The participant cannot be placed in the final upset leaderboard until the conflict is resolved.

---

## Equal Winner/Upset Optimization

The skill maintains two independent leaderboards:

```text
LEADERBOARD_A = highest_probability_favorites
LEADERBOARD_B = highest_probability_underdogs
```

Rules:

- Give each leaderboard equal research effort.
- Do not let high-probability favorites crowd underdogs out of the report.
- Do not compare a 72% favorite directly with a 44% underdog and call the underdog weak merely because its absolute probability is lower.
- Rank favorites only against favorites.
- Rank upsets only against underdogs.
- Never force an upset qualification when no underdog clears the required data and probability standards.
- Fewer results are preferred over unsupported results.

Default output target:

```text
Top 3 qualified winners
Top 3 qualified upset candidates
```

The user may request a different quantity.

---

## Probability Tiers

### Favorite Winner Tiers

```text
ELITE_WINNER
calibrated_probability_lower_bound >= 70%

STRONG_WINNER
calibrated_probability_lower_bound >= 65% and < 70%

QUALIFIED_WINNER
calibrated_probability_lower_bound >= 60% and < 65%

WINNER_WATCH
calibrated_probability_lower_bound >= 55% and < 60%

WINNER_REJECT
calibrated_probability_lower_bound < 55%
```

### Upset Tiers

Underdogs naturally carry lower absolute probability. They must be evaluated on an underdog-specific scale without using edge.

```text
ELITE_UPSET_PROFILE
calibrated_probability_lower_bound >= 47% and verified underdog

STRONG_UPSET_PROFILE
calibrated_probability_lower_bound >= 43% and < 47%

QUALIFIED_UPSET_PROFILE
calibrated_probability_lower_bound >= 40% and < 43%

UPSET_WATCH
calibrated_probability_lower_bound >= 35% and < 40%

UPSET_REJECT
calibrated_probability_lower_bound < 35%
```

These tiers indicate only modeled outright win probability.

They do not indicate:

- value;
- profitability;
- positive expected value;
- recommended stake;
- final betting approval.

---

## Allowed Final Labels

```text
ELITE_WINNER
STRONG_WINNER
QUALIFIED_WINNER
WINNER_WATCH
WINNER_REJECT

ELITE_UPSET_PROFILE
STRONG_UPSET_PROFILE
QUALIFIED_UPSET_PROFILE
UPSET_WATCH
UPSET_REJECT

NO_SOURCE_COVERAGE
NO_DATA_QUALITY
NO_LINEUP_CONFIRMATION
NO_STARTER_CONFIRMATION
NO_GOALIE_CONFIRMATION
NO_QUARTERBACK_CONFIRMATION
NO_EVENT_VERIFICATION
NO_SETTLEMENT_CLARITY
NO_MODEL_SUPPORT
NO_STATUS_VERIFICATION
FAVORITE_STATUS_CONFLICT
KALSHI_INVENTORY_EMPTY
KALSHI_DATA_UNOBTAINABLE
REJECT_BAD_STRUCTURE
```

No label from this skill may become:

```text
MONEY_QUALIFIED
FINAL_APPROVED
PLAYABLE
LOCK
BEST_BET
STAKE_READY
```

---

## Required Inputs Per Candidate

```text
platform
sport
league
event_id
event_date
event_time
participant
opponent_or_field
home_away_or_neutral
market_type
exact_selection
favorite_or_underdog_status
classification_source
board_or_market_timestamp
official_event_status
settlement_rule
official_settlement_source
model_probability
calibrated_probability_lower_bound
model_timestamp
```

Sport-specific inputs must also be acquired before qualification.

---

## Source Hierarchy

Use the strongest available source for each fact.

1. Official league, team, event, or governing-body source
2. Official injury, lineup, starter, goalie, or participant report
3. High-quality statistical database
4. Multiple current sportsbooks or exchanges
5. Independent projection model
6. Reputable beat reporting
7. Platform board or screenshot
8. Aggregator or secondary summary
9. Social-media claim only when verified by stronger evidence

Screenshots and third-party slips are menus, not proof.

---

## Acquisition Status

Every required source path receives one status:

```text
RETRIEVED
RECONSTRUCTED
PROXY_ONLY
SOURCE_CONFLICT
DATA_UNOBTAINABLE
INPUT_FAILURE
NOT_APPLICABLE
```

`NOT_CALLED` is prohibited in a final report.

For each candidate, record:

```text
event_status
participant_status
lineup_or_starter_status
historical_data_status
matchup_data_status
market_classification_status
projection_status
settlement_status
news_status
weather_status_if_material
model_timestamp
failure_path
```

---

## Core Workflow

### 1. Governance and Inventory Check

- Load the active WOW v16 Clean Core governance.
- Confirm `can_execute=false`.
- Confirm the scan date and timezone.
- Check Kalshi sports inventory before any Kalshi contract analysis.
- Stop the Kalshi lane on `INVENTORY_EMPTY`.

### 2. Build the Outright-Winner Slate

Collect only verified moneyline-style selections from:

- PrizePicks;
- Kalshi;
- current sportsbooks;
- supported prediction markets;
- user uploads.

Normalize:

- names;
- teams;
- leagues;
- dates;
- event IDs;
- market types;
- settlement definitions.

### 3. Pre-Analysis Slate Purge

Remove:

- completed events;
- postponed or canceled events;
- stale dates;
- duplicate events;
- wrong teams or participants;
- stale screenshots;
- derivative markets misidentified as full-game winners;
- contracts with unclear settlement.

### 4. Classify Favorites and Underdogs

Use current no-vig consensus only to determine market role.

Do not rank by price.

Record:

```text
market_role = FAVORITE | UNDERDOG | EVEN | CONFLICT
```

### 5. Lock Event Reality

Verify:

- event is scheduled;
- venue;
- home/away/neutral status;
- participant eligibility;
- starting lineup or expected starters;
- injuries, suspensions, rest, travel, and availability;
- sport-specific critical roles.

### 6. Build Independent Win Model

Produce an independent or blended win probability using sport-specific inputs.

Required:

```text
raw_model_probability
market_prior_probability
independent_component_probability
calibrated_probability
calibrated_probability_lower_bound
confidence_interval
model_disagreement
```

The market prior may stabilize the model but may not become the sole forecast.

### 7. Run Sport-Specific Model

Use the applicable module below.

### 8. Contradiction Audit

Check whether late information materially contradicts the model:

- unexpected lineup;
- starter scratch;
- goalie change;
- quarterback change;
- minutes or workload restriction;
- weather shift;
- travel or rest change;
- settlement mismatch;
- large unexplained market move.

A contradiction triggers a re-run or a fail-closed label.

### 9. Calibrate and Haircut

Apply:

- model calibration;
- sample-size uncertainty;
- lineup uncertainty;
- injury uncertainty;
- sport volatility;
- market liquidity uncertainty;
- late-news risk;
- model disagreement haircut.

The final ranking uses:

```text
calibrated_probability_lower_bound
```

not the optimistic point estimate.

### 10. Rank Two Separate Pools

Rank favorites by highest calibrated lower bound.

Rank underdogs by highest calibrated lower bound.

Use tie-breakers in this order:

1. lower uncertainty;
2. stronger lineup/status confirmation;
3. stronger independent model agreement;
4. lower sport-specific variance;
5. fresher data;
6. clearer settlement.

Do not use price, payout, or edge as a tie-breaker.

### 11. One Selection Per Event

Only one side of an event may appear in the final output.

Do not list both teams as separate probability picks.

### 12. Final QA

Confirm:

- every candidate is on the correct slate;
- every result is an outright winner market;
- favorite/underdog status is current;
- model probability is calibrated;
- lower bound is shown;
- no price or edge influenced ranking;
- no execution or staking language appears;
- Kalshi inventory rule was enforced;
- `can_execute=false`.

---

## Sport-Specific Modules

## MLB

Required factors:

- confirmed starting pitchers;
- pitcher quality and current health;
- handedness and projected lineup;
- bullpen quality, rest, and availability;
- park factor;
- weather and wind;
- defense and catcher quality;
- platoon matchup;
- travel and rest;
- lineup absences;
- run-distribution model;
- extra-inning home-field rules when relevant.

Required output:

```text
starter_edge
bullpen_edge
lineup_edge
park_weather_adjustment
projected_runs_team
projected_runs_opponent
win_probability
```

No MLB candidate qualifies without confirmed or strongly probable starting pitchers.

---

## NBA / WNBA / NCAAB

Required factors:

- confirmed active roster;
- expected starters;
- projected minutes;
- usage and on/off impact;
- rest and back-to-back status;
- travel;
- pace;
- offensive and defensive matchup;
- rebounding and turnover profile;
- three-point variance;
- foul and free-throw profile;
- late-game creation;
- blowout and rotation risk.

Required output:

```text
projected_margin
margin_distribution
win_probability
key_absence_adjustment
late_game_edge
```

A star absence must be modeled through role redistribution, not a flat narrative penalty.

---

## NFL / NCAAF

Required factors:

- starting quarterback;
- offensive-line health;
- defensive-front health;
- skill-position availability;
- EPA/play and success rate;
- explosive-play rate;
- early-down efficiency;
- pressure and sack profile;
- turnover regression;
- special teams;
- weather;
- travel, rest, and coaching;
- game-state and comeback ability.

Required output:

```text
projected_margin
quarterback_adjustment
trench_adjustment
weather_adjustment
win_probability
```

No candidate qualifies with unresolved starting-quarterback status.

---

## NHL

Required factors:

- confirmed or expected starting goalie;
- goalie form and underlying save metrics;
- five-on-five expected-goal profile;
- special teams;
- line combinations;
- injuries;
- rest and travel;
- shot quality;
- home-ice impact;
- overtime and shootout rules.

Required output:

```text
goalie_adjustment
expected_goals_for
expected_goals_against
regulation_probability
overtime_probability
full_game_win_probability
```

No NHL candidate qualifies without an adequately verified goalie assumption.

---

## Soccer

Required factors:

- competition and settlement format;
- confirmed or projected starting XI;
- goalkeeper;
- injuries and suspensions;
- expected goals;
- home/away/neutral setting;
- rest and fixture congestion;
- tactical matchup;
- set-piece strength;
- travel;
- motivation only when objectively supported;
- draw probability.

Required output:

```text
home_win_probability
draw_probability
away_win_probability
selected_team_outright_win_probability
```

A soccer moneyline win means the exact platform-defined outcome. Do not treat advancement, double chance, or draw-no-bet as the same market.

---

## Tennis

Required factors:

- surface;
- best-of format;
- hold and break rates;
- serve and return quality;
- handedness matchup;
- recent workload;
- injury;
- travel;
- altitude or indoor/outdoor conditions;
- tiebreak strength;
- retirement settlement rules.

Required output:

```text
serve_hold_projection
return_break_projection
straight_sets_probability
match_win_probability
retirement_rule_status
```

---

## Golf

Supported markets:

- head-to-head matchup winner;
- group winner;
- outright tournament winner when a field model exists.

Required factors:

- course fit;
- strokes-gained components;
- field strength;
- weather-wave advantage;
- recent health;
- cut rules;
- field size;
- starting position for in-progress events;
- dead-heat and withdrawal rules.

Required output:

```text
head_to_head_probability
group_win_probability
outright_win_probability
field_model_status
```

Outright probabilities must be calibrated to the full field and sum approximately to 100%.

---

## MMA / Boxing

Required factors:

- style matchup;
- age and physical profile;
- reach and stance;
- striking and grappling efficiency;
- takedown offense and defense;
- cardio;
- durability;
- recent damage;
- layoffs;
- camp changes;
- weight cut;
- judges and location only when materially sourced;
- bout format;
- settlement rules for draws and no contests.

Required output:

```text
decision_probability
finish_probability
fighter_a_win_probability
fighter_b_win_probability
draw_or_no_contest_probability
```

---

## Probability Construction

Preferred ensemble:

```text
independent_statistical_model
+ role/status adjustment
+ matchup model
+ venue/rest/travel adjustment
+ current market prior
+ calibration layer
+ uncertainty haircut
```

Example conceptual blend:

```text
base_probability =
0.45 * independent_statistical_model
+ 0.20 * lineup_and_status_model
+ 0.15 * matchup_model
+ 0.10 * venue_rest_travel_model
+ 0.10 * no_vig_market_prior
```

Weights must be sport-specific and calibrated. They are not universal constants.

### Market Independence Safeguard

The model must report:

```text
market_prior_weight
independent_model_weight
```

If the market prior supplies more than 50% of the final probability:

```text
MARKET_DEPENDENT_MODEL
```

The result may be shown as a watch item but cannot receive the highest confidence tier without an independent supporting model.

---

## Upset Analysis Rules

The upset lane must identify why the underdog can win outright.

Required upset-path decomposition:

```text
baseline_win_probability
lineup_or_participant_advantage
matchup_advantage
variance_path
late_game_or_finish_path
favorite_failure_path
upset_probability_lower_bound
```

Valid upset reasons include:

- starting-pitcher advantage;
- confirmed goalie advantage;
- quarterback or trench advantage;
- matchup-specific shot or possession advantage;
- surface or style advantage;
- rest/travel advantage;
- lineup edge hidden by team reputation;
- high-variance pathway supported by the sport model;
- superior closing or finishing profile.

Invalid upset reasons:

- “anything can happen”;
- large payout;
- public fade;
- revenge;
- vague momentum;
- recent single-game result;
- social-media popularity;
- edge alone;
- attractive contract price.

---

## Winner Analysis Rules

The favorite lane must explain why the favorite is likely to win and what can break the forecast.

Required:

```text
base_win_probability
primary structural advantage
secondary advantage
status confirmation
largest loss path
calibrated_probability_lower_bound
```

A favorite with a high point estimate but a wide uncertainty band may rank below a slightly lower favorite with a stronger lower bound.

---

## Cross-Platform Duplicate Handling

The same game may appear on multiple platforms.

Rules:

- Model the sporting event once.
- Reuse the same core win probability.
- Preserve platform-specific settlement differences.
- Do not count the same team/event as multiple independent picks.
- Show all verified platform appearances under one event record.
- Never allow platform price differences to alter probability rank.

Example:

```text
Event: Team A vs Team B
Model probability: Team A 68%
PrizePicks: Team A winner selection verified
Kalshi: Team A YES contract verified
Sportsbooks: Team A favorite
Final ranking entry: one event, multiple platform references
```

---

## Live and Pregame Handling

Default mode:

```text
PREGAME_ONLY
```

Live mode is allowed only when explicitly requested.

Live requirements:

- score;
- game clock or inning;
- possession or serve state;
- remaining participants;
- live lineup;
- live win-probability model;
- current event status;
- exact live settlement.

Pregame and live probabilities must never be mixed.

---

## Refresh Rules

Re-run a candidate when any of the following occurs:

- starting lineup posts;
- starting pitcher changes;
- goalie changes;
- quarterback status changes;
- injury status changes;
- weather changes materially;
- event is delayed;
- platform contract changes;
- market-role classification flips;
- data is older than the sport-specific freshness threshold.

Recommended freshness:

```text
lineups/status: current within 15 minutes of final scan when available
market classification: current within 10 minutes
weather: current within 30 minutes when material
model run: after latest material status update
```

These freshness rules support probability accuracy, not price execution.

---

## Required Output Format

```text
LLP MONEYLINE PROBABILITY SCAN

Mode: OUTRIGHT_WIN_PROBABILITY_ONLY
Sports scanned:
Platforms scanned:
As of:
Edge evaluated: false
Price used for ranking: false
can_execute=false
```

### Highest-Probability Winners

| Rank | Team/Participant | Sport | Opponent/Event | Model Probability | Calibrated Lower Bound | Tier | Primary Win Reason | Main Loss Path | Platforms Verified |
|---:|---|---|---|---:|---:|---|---|---|---|

### Highest-Probability Upsets

| Rank | Underdog | Sport | Opponent/Event | Model Probability | Calibrated Lower Bound | Tier | Upset Path | Main Failure Path | Platforms Verified |
|---:|---|---|---|---:|---:|---|---|---|---|

### Near Misses

| Candidate | Lane | Probability Lower Bound | Missing Requirement or Blocker |
|---|---|---:|---|

### Acquisition Audit

| Candidate | Event | Status/Lineup | Historical Data | Matchup | Model | Market Role | Settlement | News | Result |
|---|---|---|---|---|---|---|---|---|---|

### Final Summary

```text
MOST LIKELY WINNER:
HIGHEST-PROBABILITY UPSET:
WINNER CONFIDENCE:
UPSET CONFIDENCE:
PRICE/EDGE IMPACT ON RANKING: NONE
KALSHI INVENTORY STATUS:
UNRESOLVED BLOCKERS:
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

---

## User-Facing Response Rules

When the user asks:

### “Who is most likely to win?”

Return the top favorite by calibrated lower-bound win probability.

### “What is the best upset?”

Return the verified underdog with the highest calibrated lower-bound outright win probability.

### “Give me the top three of each.”

Return up to three qualified favorites and up to three qualified underdogs. Do not fill empty slots with weak candidates.

### “Ignore price and edge.”

Confirm in the output:

```text
price_used_for_ranking=false
edge_evaluated=false
```

### “Can I play these?”

Do not convert the scan into execution advice.

Return:

```text
This is a probability-only research ranking.
It does not evaluate value, stake size, or execution.
can_execute=false
```

---

## Failure Behavior

### No Current Slate

```text
DECISION: NO_SCAN
label=NO_EVENT_VERIFICATION
can_execute=false
```

### Kalshi Inventory Empty

```text
KALSHI LANE: STOPPED
label=KALSHI_INVENTORY_EMPTY
probability_injection=false
can_execute=false
```

### Missing Critical Starter or Lineup

```text
label=NO_LINEUP_CONFIRMATION
or
label=NO_STARTER_CONFIRMATION
candidate_removed=true
```

### Settlement Unclear

```text
label=NO_SETTLEMENT_CLARITY
candidate_removed=true
```

### No Qualified Upset

```text
HIGHEST-PROBABILITY UPSET: NONE QUALIFIED
reason=<data/probability/status blocker>
```

Never invent or force an upset.

### Source Conflict

```text
label=NO_DATA_QUALITY
conflict=<sources>
action=re-run after resolution
```

---

## QA Checklist

Before final output, verify:

```text
[ ] Active governance loaded
[ ] Scan date and timezone verified
[ ] All events are current
[ ] Moneyline-style settlement verified
[ ] Favorite/underdog classification is current
[ ] Kalshi inventory gate enforced
[ ] Critical starters/lineups/status verified
[ ] Sport-specific model completed
[ ] Market is not the sole probability source
[ ] Both point estimate and calibrated lower bound shown
[ ] Favorites and underdogs ranked separately
[ ] Price and edge did not affect ranking
[ ] One selection per event
[ ] No forced upset
[ ] No stake or execution language
[ ] can_execute=false
```

---

## Activation Prompt

> Activate LLP Moneyline Probability Expert. Scan all available supported platforms, including PrizePicks and Kalshi, across all major sports. Build separate favorite and underdog pools, verify event identity, settlement, lineups, starters, injuries, and sport-specific matchup inputs, then rank the highest-probability outright winner and highest-probability upset equally by calibrated probability lower bound. Ignore edge, EV, payout, and price for ranking. Enforce the Kalshi INVENTORY_READY gate, dry-run-only governance, and can_execute=false.

---

## One-Line Definition

**LLP Moneyline Probability Expert is a cross-platform, all-sports, probability-only skill that separately ranks the most likely outright favorite and the most credible outright underdog using verified event data, sport-specific models, calibration, and fail-closed WOW v16 governance.**

---

# 2026-08-01 Critical Integration — Slate Integrity, Market Normalization, Dynamic Calibration, Failure Paths, Final Refresh

This integration is mandatory under:

```text
WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH
```

## Updated Mandatory Workflow

Replace the prior workflow with:

```text
governance_sync
→ full_slate_discovery
→ wow.llp-slate-integrity-expert
→ exact_market_and_settlement_lock
→ critical_participant_lock
→ sport_specific_independent_model
→ wow.llp-market-normalization-expert
→ wow.llp-dynamic-calibration-expert
→ wow.llp-failure-path-expert
→ probability_leaderboard
→ edge_leaderboard
→ wow.llp-final-refresh-governor
→ final_QA
```

## Mandatory Skill Calls

Every candidate reaching model scoring must call:

```text
wow.llp-slate-integrity-expert
wow.llp-market-normalization-expert
wow.llp-dynamic-calibration-expert
wow.llp-failure-path-expert
```

Every candidate reaching final presentation must call:

```text
wow.llp-final-refresh-governor
```

## Discovery Lane

The engine may scan broadly and quickly, but discovery labels are never qualified labels:

```text
DISCOVERY_FAVORITE
DISCOVERY_UPSET
DISCOVERY_ALT_LINE
DISCOVERY_WATCH
```

## Slate Integrity Requirements

Add per candidate:

```text
official_event_id
official_start_utc
official_schedule_source
event_status
status_timestamp
slate_date_match
wrong_year_check
duplicate_team_check
```

Any wrong-date, wrong-year, started, final, postponed, canceled, unresolved, or impossible duplicate event is removed before probability modeling.

## Market Normalization Requirements

Add:

```text
raw_implied_probability_each_outcome
market_hold
no_vig_probability_each_outcome
normalization_sum
selected_outcome_no_vig
```

Soccer full-time moneyline requires HOME, DRAW, and AWAY prices. Two-price soccer normalization is prohibited.

## Dynamic Calibration Requirements

Replace generic haircut language with:

```text
calibration_method
calibration_sample_size
calibrated_point_probability
calibrated_probability_lower_bound
calibrated_probability_upper_bound
confidence_interval_level
uncertainty_drivers
```

A universal fixed 5% haircut may be shown as sensitivity only. It cannot independently produce a qualifying lower bound.

## Failure-Path Requirements

Add:

```text
regime_probabilities
conditional_win_probability_each_regime
unconditional_win_probability
largest_failure_path
largest_failure_probability
failure_path_score
market_alignment_status
```

The final probability must be unconditional after failure paths. Moneyline analysis may not use spread-only failure language.

## Probability and Edge Separation

The probability leaderboard continues to rank by:

```text
calibrated_probability_lower_bound
```

Add a separate edge leaderboard using:

```text
point_edge = calibrated_point_probability - no_vig_probability
lower_bound_edge = calibrated_probability_lower_bound - no_vig_probability - friction_buffer
```

Price may not affect probability ranking. Probability may not erase a negative lower-bound edge in the edge lane.

## Final Refresh Requirements

Before final output, verify:

```text
event_not_started
market_open
price_age_minutes<=10
critical_status_current
no material lineup/starter/goalie/QB change
market role unchanged
settlement unchanged
```

Any failure removes the row. A critical participant change requires a complete rerun and cannot remain qualified in the same output.

## Added Labels

```text
SLATE_IDENTITY_PASS
SLATE_DATA_UNOBTAINABLE
MARKET_NORMALIZATION_FAILURE
UNCALIBRATED_MODEL
MARKET_DEPENDENT_MODEL
MARKET_MISMATCHED_FAILURE_PATH
MODEL_RERUN_REQUIRED
FINAL_REFRESH_PASS
REMOVE_FROM_FINAL_OUTPUT
```

## Updated Required Tables

### Highest-Probability Favorites

| Rank | Team | Point Probability | Lower Bound | No-Vig | Point Edge | Lower-Bound Edge | Tier | Final Refresh |
|---:|---|---:|---:|---:|---:|---:|---|---|

### Highest-Probability Upsets

| Rank | Underdog | Point Probability | Lower Bound | No-Vig | Point Edge | Lower-Bound Edge | Upset Path | Final Refresh |
|---:|---|---:|---:|---:|---:|---:|---|---|

### Rejected After Final Refresh

| Candidate | Prior Status | Final Blocker | Action |
|---|---|---|---|

## Updated Audit Footer

```text
events_discovered=
events_identity_verified=
events_removed_wrong_date=
events_removed_wrong_year=
events_removed_started=
events_removed_finished=
events_removed_duplicate=
two_way_markets_normalized=
three_way_markets_normalized=
normalization_failures=
calibration_method=
uncalibrated_rows=
failure_paths_modeled=
final_refresh_timestamp=
rows_removed_final_refresh=
price_used_for_probability_rank=false
price_used_for_edge_rank=true
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

---

# 2026-08-04 Enforcement Patch — Per-Row Publication Gate

## Active patch

```text
WOW-PATCH-2026-08-04-LLP-PER-ROW-PUBLICATION-GATE
```

## Problem this patch fixes

The prior workflow described the correct checks but did not block publication when
they were skipped. A report could publish with:

- 16 of 17 rows missing injury/lineup status
- prices quoted with no timestamps
- no line-drift grade on any row
- no retirement-settlement re-check for tennis when a retirement had already
  occurred on the same slate
- no correlation pass before the shortlist was written
- no calibration ledger rows produced

These are not "near misses" — they are silent omissions that make every probability
estimate in the report untrustworthy.

## Rule: every row must pass the per-row gate before entering any shortlist

A row may not appear in the Highest-Probability Winners table, the
Highest-Probability Upsets table, or any Near Misses table until all seven
per-row gate items below are either **completed** or carry an explicit
**`NOT_RETRIEVED`** declaration with a stated reason.

Silence is not allowed. `NOT_CALLED` is prohibited. Omitting a field from the
output is treated as a silent `NOT_RETRIEVED` and the row is removed.

---

## Per-Row Gate — seven mandatory items

### 1. Line movement and drift grade (Step 3)

For every row, record:

```text
opening_price         — first available market price for this event
current_price         — price at time of this analysis
price_direction       — MOVED_TOWARD_FAVORITE | MOVED_TOWARD_UNDERDOG | STABLE
price_drift_magnitude — price change in American odds points
drift_classification  — ALIGNED | MILD | STRONG | SEVERE
drift_source          — source(s) consulted
```

If the spread across books is wider than 50 American-odds points, add:

```text
SOURCE_CONFLICT — wide book spread: <low> to <high>
```

A wide spread (e.g., -820 to -1020 across books) is not "market noise" — it
is an unresolved data-quality signal that must be labeled and addressed before
the row qualifies.

If opening price is genuinely unobtainable:

```text
opening_price: NOT_RETRIEVED — <reason>
drift_classification: NOT_RETRIEVED
```

This is acceptable. What is not acceptable: omitting these fields entirely.

---

### 2. Price timestamps and freshness score (Step 11)

Every quoted price in this report must carry a capture timestamp.

```text
price_capture_timestamp  — ISO datetime of when this price was recorded
minutes_to_event_start   — calculated at time of analysis
freshness_status         — FRESH | STALE | UNKNOWN
```

Freshness thresholds:

```text
< 10 min old   → FRESH (any sport)
10–30 min old  → FRESH for most sports; STALE for live-market-only plays
> 30 min old   → STALE
> 2 hours old  → STALE_HIGH_RISK — row capped at WATCH tier
unknown age    → UNKNOWN — row capped at WATCH tier
```

A price range like "-820 to -1020" presented without book-specific timestamps
is UNKNOWN and must be labeled as such. It may not be presented as if it is a
single current market price.

---

### 3. Injury and lineup status — every row (Step 8)

Injury/lineup checks are mandatory for every candidate without exception.

```text
injury_lineup_check_status  — RETRIEVED | NOT_RETRIEVED | NOT_APPLICABLE
injury_lineup_source        — source name and timestamp
key_absences                — list any confirmed absences; empty list if none found
material_injury_flag        — true | false
```

Sport-specific requirements:

```text
MLB    → confirmed starting pitcher + lineup absences
NBA    → injury report timestamp + key rotations
WNBA   → injury report + minutes restrictions
NFL    → QB status + O-line health
NHL    → goalie confirmation
Soccer → confirmed XI or expected XI with source
Tennis → retirement/injury history for this event (see item 5 below)
```

A check that found no injuries is still a completed check — record
`key_absences: []` and `material_injury_flag: false`.

A check that was not performed is `NOT_RETRIEVED`. The row is capped at
WATCH tier until status is confirmed. It may not appear in a top-3 shortlist.

---

### 4. MLB: bullpen quality and weather/park (Step 6 MLB module)

For any MLB candidate, the sport-specific model is not complete if it covers
only starter ERA/WHIP. The following are also mandatory per row:

```text
bullpen_quality           — recent bullpen ERA/xFIP or role-availability note
bullpen_availability      — any key relievers unavailable
park_factor               — run-environment adjustment
weather_wind_status       — wind speed/direction if material to run scoring;
                            or explicit NOT_MATERIAL with stated reason
```

If bullpen data is unobtainable:

```text
bullpen_quality: NOT_RETRIEVED — <reason>
```

The row may still qualify, but the `uncertainty_drivers` field must include
`BULLPEN_DATA_MISSING` and the calibrated lower bound must be widened
accordingly.

---

### 5. Tennis: retirement and withdrawal settlement (per-slate rule)

Tennis matches settle differently under retirement or withdrawal depending on
platform and exact contract terms. This check is not optional.

For every tennis candidate:

```text
retirement_settlement_rule  — what the platform pays if the selected player
                              retires or withdraws before the match completes
withdrawal_settlement_rule  — same for pre-match withdrawal
retirement_check_source     — platform T&C page, contract terms, or explicit
                              platform communication
retirement_check_status     — RETRIEVED | NOT_RETRIEVED
```

**In-slate precedent rule:** if any match in the current slate has already
resolved via retirement, withdrawal, or walkover, then every remaining tennis
match in the same slate must confirm its retirement/withdrawal settlement terms
before it can appear in any shortlist — even if the original scan was completed
before the retirement occurred.

Retirement on the same slate is not incidental context. It is direct evidence
that the settlement assumption you made earlier is actively relevant and must
be re-verified.

---

### 6. Correlation guard (pre-shortlist)

Before any multi-row shortlist is written, `wow.correlation-guard` must run.

```text
correlation_guard_status  — PASSED | FAILED | NOT_RUN
correlated_pairs_found    — list of any pairs sharing game, pitcher, or script
correlation_adjustments   — any rows removed or downgraded
```

`NOT_RUN` blocks publication of any multi-selection output.

If two candidates share the same game (e.g., both sides of an MLB game, or a
pitcher and a team in the same game), only one may appear in the final shortlist.

If two candidates share a game script or common failure path (e.g., two NBA
home favorites on the same night who both lose if their star is scratched), the
combined exposure must be noted and the pair cannot both reach the top tier
without explicit modeling of the joint failure path.

---

### 7. Calibration ledger row

One calibration ledger row must be generated per candidate, regardless of
whether the candidate qualifies or is rejected.

```text
calibration_ledger_row:
  candidate_id
  event_date
  participant
  sport
  platform
  market_type
  model_probability_at_analysis
  no_vig_market_probability_at_analysis
  price_at_analysis
  analysis_timestamp
  final_label
  outcome              — PENDING (filled in at settlement)
```

The row is written at analysis time. Settlement fills in `outcome` later.
Without this row, CLV and Brier scoring cannot happen. Without CLV and Brier
scoring, the model cannot be validated.

`NOT_PRODUCED` is not a valid state for this field. If the candidate is in
the report, the row exists.

---

## Publication gate enforcement

Before writing the final shortlist, verify the per-row gate for every
candidate. Use this checklist:

```text
[ ] line movement and drift grade completed or NOT_RETRIEVED stated
[ ] price timestamps present on every quoted price
[ ] freshness status computed for every row
[ ] injury/lineup check completed or NOT_RETRIEVED stated for every row
[ ] MLB rows: bullpen and weather/park explicitly addressed
[ ] tennis rows: retirement/withdrawal settlement terms confirmed
[ ] in-slate precedent rule applied if any retirement occurred in this slate
[ ] correlation guard run (status = PASSED)
[ ] calibration ledger row produced for every candidate
```

A row that fails any item is removed from the shortlist and placed in the
Rejected table with the specific gate item that failed.

A shortlist with more than two rows where any gate item is `NOT_RETRIEVED`
must include a header warning:

```text
DATA QUALITY WARNING: [N] rows have NOT_RETRIEVED fields.
These rows are capped at WATCH tier and excluded from top-3.
```

## Added audit footer fields

```text
rows_gate_passed=
rows_gate_failed=
rows_with_not_retrieved_drift=
rows_with_not_retrieved_injury_status=
rows_with_not_retrieved_timestamps=
rows_missing_bullpen_weather=
rows_missing_tennis_retirement_check=
in_slate_precedent_rule_applied=
correlation_guard_status=
calibration_ledger_rows_produced=
```

---

# 2026-08-04 Enforcement Patch B — Multi-Recommendation Concentration Gate

## Active patch

```text
WOW-PATCH-2026-08-04-LLP-MULTI-RECOMMENDATION-CONCENTRATION-GATE
```

## Problem this patch fixes

A research article or analysis piece containing multiple recommendations can
produce a bundled shortlist where all candidates share:

- the same game;
- the same injured player or roster change;
- the same starter's performance assumption;
- the same scoring-environment thesis.

These are not three independent opportunities. They are one thesis expressed
three ways. The prior workflow had no gate that detected this and applied a
shared ceiling before shortlist construction.

Example: a three-play card of (team ML + game total Under + starter strikeout Over)
built on the same game shares the causal assumptions:
- opposing lineup is weakened
- starter works efficiently and deeply
- run environment stays suppressed
- opponent bullpen does not create unexpected scoring

Each of those three props can win or lose somewhat independently, but their
probabilities are materially linked through the starter's performance and the
run environment. They must receive shared-event and shared-thesis tags and a
concentration ceiling before appearing in any shortlist together.

---

## Multi-Recommendation Evidence and Concentration Gate

For every input that contains multiple recommendations — whether from an article,
analysis piece, research note, screenshot, or user-supplied text — run this
nine-step gate before any shortlist is constructed.

### Step 1: Extract candidates separately

Every recommendation is a separate row. Do not aggregate them into a single
probability estimate. Do not carry a confidence label from the source through
to the model output.

### Step 2: Individual timestamped market evidence

Each candidate requires its own:

```text
named_sportsbook_price
price_timestamp
opposing_price
two_way_no_vig_probability
```

A price range without book-specific timestamps (e.g., "-142 to -150") is
`NOT_VERIFIED` for that candidate.

### Step 3: Individual status and lineup checks

Each candidate requires its own:

```text
event_status
participant_status
lineup_or_starter_status
injury_check_status
```

A status check performed for one candidate does not transfer to another, even
in the same game.

### Step 4: Independent probability model per market type

A moneyline model is not a total model. A total model is not a strikeout-prop
model. Each market type requires its own model:

```text
ML      → wow.mlb-moneyline-enrichment-contract or equivalent
Total   → full-game run distribution, park/weather, both bullpens
Prop    → exact-line ledger, workload distribution, failure paths
```

Deriving a player-prop edge from the game thesis is prohibited. "The starter
is favored in this game" does not establish a strikeout-prop probability.

### Step 5: Contradiction check per candidate

Run the contradiction audit for each candidate independently:

```text
market_contradiction_status
late_news_contradiction_status
starter_status_contradiction
```

A contradiction resolved for one candidate is not automatically resolved for
another sharing the same game.

### Step 6: Shared-event and shared-thesis tagging

Assign event tags and thesis tags to every candidate.

Event tags (examples):

```text
EVENT_CLE_NYM_2026_08_04
```

Thesis tags (examples):

```text
METS_OFFENSE_DOWNGRADE
CANTILLO_STARTER_SUCCESS
LOW_RUN_ENVIRONMENT
CLEVELAND_CONTROL_SCRIPT
```

Record:

```text
shared_event_count     — number of candidates sharing this event tag
shared_thesis_count    — number of candidates sharing >= 2 thesis tags
```

### Step 7: Correlated failure exposure

When two or more candidates share an event or thesis tag, estimate the
probability that they fail together:

```text
joint_failure_probability
correlated_failure_path
```

This is not optional. "They can each win independently" is an incomplete
analysis when their probabilities are materially linked.

### Step 8: Prohibit confidence language when gates are NOT RETRIEVED

Any candidate where any required gate item is `NOT_RETRIEVED` must not carry:

```text
"Strong Play"
"Analytical Lean"
"High Confidence"
"Best Bet"
"Lock"
"Approved Play"
```

When a confidence label is present in the source material but the calibrated
probability is missing, apply:

```text
UNSUPPORTED_CONFIDENCE_CLAIM
=> downgrade to LLP_SCOUT
```

### Step 9: Apply lowest shared ceiling to the bundled recommendation

```text
three_or_more_candidates_from_same_event
AND shared_thesis_count >= 2
AND correlation_not_quantified
=> MULTI_CANDIDATE_SHORTLIST_BLOCK
=> maximum ceiling = LLP_WATCH for all candidates in the bundle
```

The ceiling applies to the entire bundle, not just the weakest candidate. A
strong individual candidate inside a correlated bundle cannot receive a higher
label than the bundle ceiling.

---

## Required shortlist-gate output format

When a multi-recommendation input is processed, the output must show a
per-candidate gate result before any shortlist:

```text
Candidate N: [participant / market]
LLP label:              [label]
Fresh timestamped price: VERIFIED | NOT VERIFIED
Lineup/status check:     RETRIEVED | NOT RETRIEVED
Independent model:       RETRIEVED | NOT RETRIEVED
Contradiction review:    PASSED | NOT RUN
Shared event tags:       [list]
Shared thesis tags:      [list]
Correlated failure:      quantified | NOT QUANTIFIED
Shortlist eligible:      YES | NO
Blocker:                 [reason if NO]
```

A candidate with `Shortlist eligible: NO` appears in the Rejected table, not
the shortlist.

---

## Hard labels added by this patch

```text
MULTI_CANDIDATE_SHORTLIST_BLOCK
  — 3+ candidates from same event, shared thesis >= 2, correlation unquantified
  — ceiling: LLP_WATCH for all candidates in bundle

UNSUPPORTED_CONFIDENCE_CLAIM
  — confidence label present, calibrated probability missing
  — downgrade: LLP_SCOUT

COMMON_THESIS_CONCENTRATION
  — shared thesis count >= 2 across any two candidates
  — requires joint failure probability before shortlist entry

MARKET_PRICE_NOT_VERIFIED
  — price range without named source and timestamp
  — candidate capped at LLP_SCOUT
```

---

## Additional audit footer fields

```text
multi_recommendation_inputs=
candidates_extracted=
candidates_shortlist_eligible=
candidates_blocked_concentration=
shared_event_pairs=
shared_thesis_pairs=
unsupported_confidence_claims_downgraded=
multi_candidate_shortlist_blocks=
```

---

# 2026-08-08 TeamRankings Secondary Enrichment — Operator Schema

## Active patch

```text
WOW-PATCH-2026-08-08-TEAMRANKINGS-SECONDARY-ENRICHMENT
```

## Overview

TeamRankings.com has **no authorized public API** available to this application.
All TR data must be read by the GPT operator from TeamRankings.com and supplied
in `enrichment["teamrankings"]` when submitting a moneyline row to the backend.

If `enrichment["teamrankings"]` is absent, the backend returns
`source_status=DATA_UNOBTAINABLE` and the base model runs unchanged.

Supported sports: **NBA, WNBA, MLB, NFL, NCAAF, NCAAB**
Weight contribution: **7.5% default — hard ceiling 10%**

---

## Enrichment Schema

Supply `enrichment["teamrankings"]` as the following JSON object. Every field
is optional — supply what is available and omit the rest. The adapter never
fabricates missing values.

```json
{
  "teamrankings": {
    "source_status": "RETRIEVED",
    "source_url": "https://www.teamrankings.com/...",
    "retrieved_at": "2026-08-08T14:30:00Z",

    "matchup_win_prob_home": 0.62,

    "home": {
      "team_name": "Los Angeles Lakers",
      "league": "NBA",
      "sport": "NBA",
      "predictive_rating": 4.8,
      "predictive_rank": 7,
      "home_rating": 6.1,
      "home_rank": 5,
      "away_rating": 3.5,
      "away_rank": 11,
      "neutral_rating": 4.9,
      "strength_of_schedule": 0.502,
      "future_strength_of_schedule": 0.498,
      "last_5_rating": 5.3,
      "last_10_rating": 4.9,
      "consistency_rating": 0.71,
      "vs_top_25_pct": 0.48,
      "vs_bottom_25_pct": 0.74,
      "projected_win_pct": 0.58,
      "projected_playoff_prob": 0.82,
      "display_odds": -155,
      "source_url": "https://www.teamrankings.com/nba/ranking/predictive-by-other",
      "retrieved_at": "2026-08-08T14:30:00Z",
      "freshness_age_hours": 1.5,
      "source_status": "RETRIEVED"
    },

    "away": {
      "team_name": "Golden State Warriors",
      "league": "NBA",
      "sport": "NBA",
      "predictive_rating": 2.1,
      "predictive_rank": 14,
      "home_rating": 3.0,
      "home_rank": 13,
      "away_rating": 1.2,
      "away_rank": 18,
      "neutral_rating": 2.0,
      "strength_of_schedule": 0.495,
      "future_strength_of_schedule": 0.501,
      "last_5_rating": 1.8,
      "last_10_rating": 2.3,
      "consistency_rating": 0.58,
      "vs_top_25_pct": 0.34,
      "vs_bottom_25_pct": 0.60,
      "projected_win_pct": 0.44,
      "projected_playoff_prob": 0.55,
      "display_odds": 135,
      "source_url": "https://www.teamrankings.com/nba/ranking/predictive-by-other",
      "retrieved_at": "2026-08-08T14:30:00Z",
      "freshness_age_hours": 1.5,
      "source_status": "RETRIEVED"
    }
  }
}
```

---

## Field Definitions

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `source_status` | string | Acquisition status (see below). Overrides per-team statuses when present. |
| `source_url` | string | TeamRankings page URL where data was read. |
| `retrieved_at` | string | ISO 8601 UTC timestamp when the operator retrieved the data. |
| `matchup_win_prob_home` | float (0.01–0.99) | TR's direct matchup win-probability projection for the home team. **This is the only TR field that contributes to the probability model.** Required for non-zero weight. |

### Per-team fields (`home` and `away`)

| Field | Type | Description |
|---|---|---|
| `team_name` | string | Full team name as shown on TeamRankings. |
| `league` | string | League abbreviation, e.g. `"NBA"`. |
| `sport` | string | Sport, e.g. `"NBA"`. |
| `predictive_rating` | float | TR predictive power rating. |
| `predictive_rank` | int | Rank by predictive rating (1 = best). |
| `home_rating` | float | Performance rating at home. |
| `home_rank` | int | Home-rating rank. |
| `away_rating` | float | Performance rating on the road. |
| `away_rank` | int | Away-rating rank. |
| `neutral_rating` | float | Neutral-site rating. |
| `strength_of_schedule` | float | Season SOS (also accepted as `sos`). |
| `future_strength_of_schedule` | float | Remaining SOS (also accepted as `future_sos`). |
| `last_5_rating` | float | Rating over last 5 games (also accepted as `l5_rating`). |
| `last_10_rating` | float | Rating over last 10 games (also accepted as `l10_rating`). |
| `consistency_rating` | float | TR consistency metric. |
| `vs_top_25_pct` | float | Win % vs top-25% opponents. |
| `vs_bottom_25_pct` | float | Win % vs bottom-25% opponents. |
| `projected_win_pct` | float | Season win % projection. |
| `projected_playoff_prob` | float | TR playoff probability. |
| `display_odds` | int | American moneyline from TR — **market context ONLY; never fed to the sport model**. |
| `source_url` | string | Team-specific TR page URL. |
| `retrieved_at` | string | ISO 8601 UTC retrieval timestamp. |
| `freshness_age_hours` | float | Age of data in hours at submission time. Computed automatically if `retrieved_at` is supplied. |
| `source_status` | string | Per-team acquisition status. |

---

## Source Status Vocabulary

| Status | Weight | Meaning |
|---|---:|---|
| `RETRIEVED` | 7.5% (up to 10%) | Operator directly read TR data; `matchup_win_prob_home` present and fresh. |
| `PROXY_ONLY` | **0%** | Data was inferred or reconstructed, not directly retrieved. Zero weight per governance. |
| `DATA_UNOBTAINABLE` | **0%** | TR data was not available or not found. Base model unchanged. |
| `STALE` | **0%** | `freshness_age_hours > 4` or derived `retrieved_at` age > 4 h. Zero weight. |
| `SOURCE_CONFLICT` | **0%** | Internal conflict between home/away records. Zero weight. |
| `NOT_ATTEMPTED` | **0%** | Operator did not include TR data. Same as absent — base model unchanged. |
| `UNSUPPORTED_SPORT` | **0%** | Sport is not in the supported set. |

**Freshness rule:** TR data with `freshness_age_hours > 4.0` (or computed age from
`retrieved_at > 4 h`) is treated as `STALE` and receives zero weight regardless of
any other field. Refresh the TR data within 4 hours of game time.

---

## display_odds Hard Rule

`display_odds` is the American moneyline shown on TeamRankings. It is stored in
the team record for audit transparency but is **permanently excluded** from the
sport model:

```text
display_odds → MARKET_CONTEXT_ONLY
display_odds → NEVER passed to extract_no_vig_probability()
display_odds → NEVER included in the market prior computation
display_odds → NEVER copied into sportsbook_odds
```

Using `display_odds` as a model input or as a proxy for `sportsbook_odds`
corrupts the market prior and is a governance violation. The backend always
sets `display_odds_excluded_from_model=true` regardless of what the operator supplies.

---

## Weight Governance

```text
Default TR ensemble weight:  7.5%
Hard ceiling:               10.0%
Zero-weight triggers: STALE | DATA_UNOBTAINABLE | PROXY_ONLY | SOURCE_CONFLICT
                      NOT_ATTEMPTED | UNSUPPORTED_SPORT | matchup_win_prob_home absent
```

**Raw predictive ratings cannot be converted to a win probability** — no
calibrated logistic mapping exists in WOW governance. The only field that
activates a non-zero TR weight is `matchup_win_prob_home` (a direct TR matchup
projection, not a computed value).

---

## Contradiction Behavior

When TR weight is non-zero and `matchup_win_prob_home` is present:

| Condition | Agreement | contradiction_flag |
|---|---|---|
| TR and core model agree (delta < 8 pp, same winner) | `AGREE` | false |
| Same winner but delta ≥ 8 pp | `DISCREPANCY` | true |
| TR favors opposite winner (TR crosses 50% boundary) | `OPPOSITE_SIDE` | true |

A contradiction **lowers confidence and may reduce the label tier** but
**never flips the pick**. The contradiction is surfaced in `teamrankings_contradiction_reason`
and routes through the disagreement audit before final qualification.

---

## Minimal Submission Example

When you have only `matchup_win_prob_home` and no per-team detail:

```json
{
  "teamrankings": {
    "source_status": "RETRIEVED",
    "retrieved_at": "2026-08-08T18:00:00Z",
    "matchup_win_prob_home": 0.61,
    "home": { "team_name": "Cleveland Guardians" },
    "away": { "team_name": "New York Mets" }
  }
}
```

This is the minimum required to achieve non-zero TR weight.

---

## Supported Sports

```text
NBA     — full support
WNBA    — full support
MLB     — full support
NFL     — full support
NCAAF   — full support
NCAAB   — full support
```

All other sports (NHL, Soccer, Tennis, Golf, MMA, Boxing) return
`UNSUPPORTED_SPORT` and zero TR weight. The base model is unaffected.
