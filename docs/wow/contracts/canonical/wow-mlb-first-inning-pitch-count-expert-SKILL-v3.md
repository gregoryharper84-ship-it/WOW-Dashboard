# Skill: wow.mlb-first-inning-pitch-count-expert

## Purpose

Model MLB first-inning pitches thrown as a batter-by-batter event-tree distribution. Find research-grade probability estimates while failing closed on incomplete lineups, health regimes, payout economics, market evidence, settlement identity, or calibration.

## Scope

Use only for pitcher **1st Inning Pitches Thrown** props. Do not use this skill for full-game pitches, outs, strikeouts, hits allowed, earned runs, or NRFI/YRFI except as contextual evidence.

## Non-negotiable governance

```text
lane_status = TEST_ONLY
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

No output from this skill can exceed `MODEL_QUALIFIED_HOLD` until the active patch explicitly changes the lane status.

## Inputs

### Board

- pitcher
- opponent
- game date/time
- exact line
- side
- offer type
- payout contribution
- capture timestamp
- settlement rule/source

### Pitcher

- raw L5/L10/current-season first-inning pitch counts
- prior-season first-inning pitch counts
- first-inning batters faced
- first-pitch strike rate
- zone rate
- walk rate
- strikeout rate
- P/PA allowed
- foul-extension profile
- velocity/command trend
- pitch mix and handedness
- catcher pairing
- health/rest/workload regime

### Top four hitters

For each projected hitter:

- confirmed batting-order position
- handedness
- P/PA
- first-pitch swing
- chase
- contact
- walk
- strikeout
- foul extension
- on-base probability versus pitcher profile
- timestamp

### Market and context

- exact or adjacent independent market
- sportsbook timestamp
- weather only if it materially affects control or plate appearances
- umpire only when sourced and shown to affect the modeled pathway
- platform payout and promotional adjustment

## Method

1. Validate governance and slate.
2. Validate board and settlement identity.
3. Build the raw exact first-inning ledger.
4. Lock the starter, catcher, and projected top four.
5. Classify the pitcher health/rest regime.
6. Construct posterior distributions for batters faced and pitches per plate appearance.
7. Simulate at least 25,000 first innings.
8. Score both MORE and LESS at the exact line.
9. Calculate fourth-batter dependency.
10. Apply uncertainty and model-error haircuts.
11. Compare against payout-adjusted required probability.
12. Apply market, promotional, slip, and QA ceilings.

## Model requirements

The model must produce:

```text
P(BF=3)
P(BF=4)
P(BF>=5)
P(MORE | BF=3)
P(MORE | BF>=4)
P(MORE)
P(LESS)
fourth_batter_dependence_share
projection mean/median/std
confidence interval
```

A recent mean, median, ERA, K%, or full-game pitch-count trend cannot stand in for these outputs.

## Fourth-batter dependency

Define:

```text
fourth_batter_dependence_share
=
P(MORE and BF>=4) / P(MORE)
```

If the denominator is zero, set the share to zero and preserve the near-zero MORE probability.

For a MORE at 13.5 or above, explicitly state the minimum P/PA required in a three-batter inning:

```text
required_three_batter_ppa = (line + smallest_scoring_increment) / 3
```

For half-point lines with integer pitch totals, `smallest_scoring_increment = 0.5` operationally means the next integer outcome. Example: MORE 13.5 requires at least 14 pitches, or 4.67 P/PA across exactly three hitters.

## Health-regime handling

Illness, post-IL, long layoff, post-break, velocity change, or workload restriction creates a separate regime. Use a matching sample when available. Otherwise:

- widen uncertainty;
- shrink toward broader baseline;
- cap at HOLD;
- emit `PITCHER_HEALTH_REGIME_UNRESOLVED` when material.

Do not automatically interpret uncertainty as MORE-positive or LESS-positive.

## Bidirectional rule

Always score both sides. Output:

```text
MORE probability
LESS probability
best modeled side
probability gap
no-edge determination
```

A failed MORE does not imply a valid LESS.

## Promotional rule

Demon, Goblin, or other adjusted lines require exact payout economics. The visible line advantage alone cannot qualify the candidate.

## Candidate decision logic

```text
Missing exact distribution → REJECT_DATA_QUALITY
Unconfirmed top four → MODEL_QUALIFIED_HOLD
Unresolved material health regime → MODEL_QUALIFIED_HOLD
Missing fourth-batter path → REJECT_DATA_QUALITY
Fourth-batter dependence >= 0.65 → MODEL_QUALIFIED_HOLD
Missing payout friction for promotional line → MODEL_QUALIFIED_HOLD
Missing market comparison → MODEL_QUALIFIED_HOLD
All gates pass during TEST_ONLY → MODEL_QUALIFIED_HOLD — MLB_1IP_TEST_ONLY
```

## Required output

```text
Pitcher / opponent / game
Board line / side / offer type / captured as of
Raw L5 and L10 first-inning pitch counts
Exact-line L5/L10 hit rates, both sides
Projected top four and confirmation status
Health/rest regime
P(BF=3), P(BF=4), P(BF>=5)
Projection mean / median / interval
P(MORE), P(LESS)
Fourth-batter dependence
Independent market evidence
Payout-adjusted required probability
Model-error haircut
Final label
All blockers
can_execute=false
```

## Prohibited shortcuts

- No approval from averages alone.
- No approval from strikeout upside alone.
- No assumption that high K% favors MORE.
- No assumption that low line favors MORE without payout analysis.
- No stale projected lineup presented as confirmed.
- No hidden health or post-break regime.
- No same-pitcher duplicate thresholds.
- No 1IP Power approval while lane is TEST_ONLY.

---

# v2 Patch Integration — Pitcher Failure-Path Prior

Before constructing the first-inning event tree, invoke:

```text
wow.mlb-pitcher-failure-path-expert
```

Use it only to supply:

```text
health/workload regime
velocity/command prior
catcher and starter confirmation
manager precaution risk
weather/delay risk
uncertainty haircut
```

The first-inning event-tree remains the controlling model for:

```text
P(BF=3)
P(BF=4)
P(BF>=5)
pitches per plate appearance
fourth-batter dependency
P(MORE)
P(LESS)
```

The failure-path skill must not replace batter-by-batter simulation.

Added required outputs:

```text
failure_path_prior_status
command_regime_probability
health/workload_regime_probability
environmental_disruption_probability
failure_path_uncertainty_haircut
```

If the failure-path prior is materially unresolved:

```text
final ceiling = MODEL_QUALIFIED_HOLD
label = PITCHER_FAILURE_PATH_PRIOR_UNRESOLVED
```

---

# v3 Patch Integration — Efficiency Gap and Directional Asymmetry

## Active patches

```text
WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE
WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY
```

## Mandatory pre-event-tree efficiency audit

For every first-inning pitch-count LESS candidate, calculate the Recent First-Inning Efficiency Deterioration Score before final calibration.

Recent window:

```text
last 3 starts
fallback to last 5 starts only when 3-start first-inning data is incomplete
baseline = current-season baseline
```

Tier 1 adverse triggers:

```text
P/BF recent >= baseline * 1.08
1IP pitches/start recent >= baseline * 1.10
1IP walk rate recent >= baseline + 3.0 percentage points
first-pitch strike rate recent <= baseline - 5.0 percentage points
zone rate recent <= baseline - 4.0 percentage points
overall BB rate recent >= baseline + 3.0 percentage points
CSW rate recent <= baseline - 4.0 percentage points
```

Weighted score:

```text
0.20 * P/BF deterioration
+ 0.20 * 1IP pitches/start deterioration
+ 0.15 * 1IP walk-rate deterioration
+ 0.15 * first-pitch-strike deterioration
+ 0.10 * zone-rate deterioration
+ 0.10 * overall-BB-rate deterioration
+ 0.10 * CSW deterioration
```

Each component is scored `0.0`, `0.5`, or `1.0`.

Tier 2 may add up to `0.10`:

```text
WHIP increase >= 15%
hard-hit rate increase >= 5 percentage points
chase rate decrease >= 4 percentage points
```

ERA and xERA are contextual only and receive no numerical weight.

Enforcement:

```text
score < 0.30
=> STABLE

0.30 <= score < 0.50
=> MILD_DETERIORATION
=> subtract 0.02 from calibrated LESS probability

0.50 <= score < 0.70
=> MATERIAL_DETERIORATION
=> downgrade LESS one tier
=> top-confidence LESS prohibited

score >= 0.70
=> SEVERE_DETERIORATION
=> maximum LESS label = WATCH
=> exclude from probability-qualified final card
```

Missing data:

```text
fewer than 4 of 7 Tier 1 metrics available
=> EFFICIENCY_SCORE_INCOMPLETE
=> maximum LESS label = MODEL_QUALIFIED_HOLD
```

## Mandatory directional asymmetry audit

After the event-tree simulation produces raw and calibrated probabilities, calculate:

```text
three_batter_less_dependence =
P(LESS and BF=3) / P(LESS)

extended_inning_loss_rate =
P(MORE | BF>=4)

right_tail_mass =
P(pitches >= line + 3)

probability_uncertainty_gap =
raw_P_LESS - calibrated_lower_bound_LESS

normalized_uncertainty_gap =
min(1, probability_uncertainty_gap / 0.10)
```

Then:

```text
directional_fragility_score =
0.35 * three_batter_less_dependence
+ 0.30 * extended_inning_loss_rate
+ 0.20 * right_tail_mass
+ 0.15 * normalized_uncertainty_gap
```

Enforcement:

```text
DFS < 0.55
=> LOW_DIRECTIONAL_FRAGILITY

0.55 <= DFS < 0.70
=> MODERATE_DIRECTIONAL_FRAGILITY
=> subtract 0.02 from calibrated LESS lower bound

0.70 <= DFS < 0.80
=> HIGH_DIRECTIONAL_FRAGILITY
=> top-confidence LESS prohibited
=> maximum label = MODEL_QUALIFIED_HOLD

DFS >= 0.80
=> SEVERE_DIRECTIONAL_FRAGILITY
=> maximum label = WATCH
=> exclude from probability-qualified final card
```

Hard override:

```text
three_batter_less_dependence >= 0.80
and
P(MORE | BF>=4) >= 0.70

=> SEVERE_DIRECTIONAL_FRAGILITY
=> maximum LESS label = WATCH
```

## Added required outputs

```text
recent_efficiency_window
efficiency_metrics_available
efficiency_metric_flags
tier_1_efficiency_score
tier_2_efficiency_modifier
final_efficiency_deterioration_score
efficiency_band
efficiency_probability_haircut
efficiency_ceiling

P(LESS | BF=3)
P(LESS | BF>=4)
three_batter_less_dependence
extended_inning_loss_rate
right_tail_mass
raw_P_LESS
calibrated_lower_bound_LESS
probability_uncertainty_gap
directional_fragility_score
directional_ceiling
```

## Updated 1IP gate order

```text
governance and slate
→ board and settlement identity
→ starter/catcher/top-four confirmation
→ pitcher failure-path prior
→ recent efficiency-gap audit
→ batter-by-batter event tree
→ bidirectional scoring
→ directional asymmetry audit
→ market and payout sanity
→ lowest-ceiling final label
```

The efficiency and directional ceilings cannot be erased by downstream model or market evidence.

## Added acceptance tests

1. ERA alone cannot trigger the efficiency gate.
2. Fewer than four Tier 1 metrics caps LESS at HOLD.
3. A 0.50 efficiency score blocks top confidence.
4. A 0.70 efficiency score caps at WATCH.
5. A 0.70 DFS caps at HOLD.
6. A 0.80 DFS caps at WATCH.
7. The hard override caps at WATCH.
8. The batter-by-batter event tree remains controlling.
9. `can_execute=false` remains enforced.

---

# v4 Patch Integration — Baseball Savant Ledger Source Hierarchy

## Active patch

```text
WOW-PATCH-1IP-SAVANT-LEDGER-SOURCE
```

## Source hierarchy

Step 3 of the 1IP method ("Build the raw exact first-inning ledger") must use the following source hierarchy. No proxy substitution is permitted.

```text
1. Baseball Savant — controlling ledger source
2. FanGraphs       — role and start validation
3. Brooks Baseball — pitch-sequence cross-check (QA only)
```

### 1. Baseball Savant (controlling)

**Backend endpoint.** The WOW scoring server provides first-inning Statcast data
via `gate_engine/mlb/savant_1ip_ledger.py`. Call the backend — do not attempt
to scrape Baseball Savant directly.

Extraction logic (applied server-side):

```text
filter:  pitcher = MLBAM pitcher ID
         inning  = 1
         season  = current season through board_date (board_date itself excluded)
group:   game_pk × game_date
count:   pitch rows  → first_inning_pitches
         distinct at_bat_number → first_inning_batters_faced
derive:  events column → first_inning_hits, first_inning_walks, first_inning_hbp,
                          first_inning_errors
keep:    most recent 10 eligible starts
```

The backend returns a structured ledger with these fields per start:

```text
game_date
game_pk                  (Savant game_pk — the canonical source_game_id)
opponent
starter_confirmed        (LIKELY / UNLIKELY / UNKNOWN from max inning in Statcast)
first_inning_pitches     (primary ledger count)
first_inning_batters_faced
first_inning_hits
first_inning_walks
first_inning_hbp
first_inning_errors
source                   "Baseball Savant (statcast_pitcher)"
source_game_id           game_pk as string
```

It also returns:

```text
bf_distribution          P(BF=3), P(BF=4), P(BF>=5) — computed from ledger rows
l5_pitch_mean
l10_pitch_mean
l5_hit_rate              (when a line and side are supplied)
l10_hit_rate
gaps                     list of any missing rows and their reasons
```

### 2. FanGraphs (role and start validation — Claude web access)

FanGraphs is blocked from the WOW server. Claude must access it directly.

Use FanGraphs game logs to verify per start:

```text
start_date
opponent
starter designation
innings_pitched (total)
total_batters_faced
strikeouts, walks, full-game workload
```

FanGraphs does not provide first-inning pitch totals. It supplements the
Baseball Savant ledger for starter confirmation only.

If a Savant row has `starter_confirmed = UNLIKELY` (max inning in Statcast ≤ 2),
cross-check via FanGraphs before including the row in the ledger.
If FanGraphs also does not confirm a start, mark the row as a gap.

### 3. Brooks Baseball (pitch-sequence QA — Claude web access)

Brooks Baseball is blocked from the WOW server. Claude must access it directly.

Use Brooks Baseball only for cross-checking individual starts that are
ambiguous or missing in Baseball Savant — for example, a known start date
where the Savant `game_pk` group returned an unusually low or high pitch count.

Brooks Baseball does not replace Baseball Savant as the primary count source.
One Savant row that has been verified by Brooks is still labelled:

```text
source: "Baseball Savant (statcast_pitcher)"
source_game_id: <game_pk>
```

Add a note field: `"brooks_verified": true` when a cross-check was performed.

## Acquisition sequence (per pitcher)

```text
1.  Resolve MLBAM pitcher ID from name via the backend or pybaseball lookup.
2.  Call backend to retrieve Baseball Savant first-inning ledger (all 2026 starts
    through board_date).
3.  Filter: keep inning = 1 groups only (already filtered by backend).
4.  Group by game_pk — one row per start (already grouped by backend).
5.  Count pitches (first_inning_pitches from backend).
6.  Count distinct plate appearances (first_inning_batters_faced from backend).
7.  Retrieve H, BB, HBP and error-extension paths (from backend events counts).
8.  Verify starter status via FanGraphs or MLB Stats API for any
    starter_confirmed = UNLIKELY rows.
9.  Cross-check questionable games through Brooks Baseball when Savant
    has an ambiguous or outlier pitch count for that game.
10. Keep the most recent 10 eligible MLB starts.
```

## Field-level source attribution

```text
Date and opponent          Baseball Savant (game_date, home_team/away_team)
Starter confirmed          FanGraphs or MLB Stats API (step 8)
First-inning pitches       Baseball Savant (row count per game_pk × inning=1)
Batters faced              Baseball Savant (distinct at_bat_number)
Hits, walks, HBP           Baseball Savant (events column)
Error extension            Baseball Savant (field_error / fielders_choice_error)
P(BF=3/4/5+)              Computed from Baseball Savant ledger rows
Exact-line result          Calculated from first_inning_pitches vs board line
Source game ID             Savant game_pk
```

## Gaps and data-quality rules

A gap is any start that cannot be confirmed by Baseball Savant. Gaps are:

```text
— marked explicitly in the ledger (first_inning_pitches = GAP)
— counted against the sample size (L8, not L10, if 2 rows missing)
— not filled by web search, Wikipedia, or estimated averages
— not filled by Brooks Baseball alone (Brooks supplements, it does not
  replace a missing Savant game_pk group)
```

If the backend `gaps` list is non-empty, include each gap reason verbatim
in the ledger output.

## Lane ceiling

```text
Maximum label:   MODEL_QUALIFIED_HOLD — MLB_1IP_TEST_ONLY
can_execute:     false
proxy substitution required: no
```

This ceiling cannot be lifted by the Baseball Savant source upgrade alone.
The lane ceiling is controlled by the v3 efficiency and directional patches.
