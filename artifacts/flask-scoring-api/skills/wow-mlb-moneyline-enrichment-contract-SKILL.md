# Skill: wow.mlb-moneyline-enrichment-contract

## Skill Name

**WOW MLB Moneyline Enrichment Contract**

## Short Description

Define and enforce the structured evidence packet required before any MLB moneyline
probability estimate can be published. Converts trade-deadline changes, confirmed
lineups, pitch-mix quality, available bullpen, platoon matchups, park and weather,
and game-script simulation into a single calibrated distribution. Narrative evidence
alone does not meet this contract.

---

## Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

---

## Core Principle

> **The narrative explains the model. The narrative is not the model.**

A claim such as "Cantillo increased curveball usage to 50%" may enter the probability
calculation only when:

1. It is verified from pitch-tracking data with a stated sample date range.
2. The opposing lineup's performance against that specific pitch is measured.
3. The change is large enough to survive regression toward the season baseline.

News, roster changes, matchup-specific stats, recent form, pitch characteristics,
and price thresholds must be assembled into structured fields before any probability
is produced. Loosely attaching news after the estimate is prohibited.

---

## Publication Condition

**No calibrated MLB moneyline probability may be published unless all 14 components
below are either RETRIEVED or carry an explicit NOT_RETRIEVED declaration with
a stated reason.**

A row missing any component is capped at `MODEL_QUALIFIED_HOLD`.

A row missing components 1, 2, 4, or 5 is blocked entirely:

```text
POST_DEADLINE_ROSTER_STATE_UNRESOLVED  => no calibrated probability; max HOLD
LINEUP_STRENGTH_UNRESOLVED             => no calibrated probability; max HOLD
STARTER_DISTRIBUTION_UNAVAILABLE       => no calibrated probability; max HOLD
BULLPEN_AVAILABILITY_UNRESOLVED        => no calibrated probability; max HOLD
```

---

## Component 1 — Post-Deadline Roster Delta

Required whenever the game is played within 14 days of the trade deadline,
or when a roster-significant trade, DFA, or call-up occurred within 7 days.

```text
roster_snapshot_timestamp
players_added                    (name, position, acquisition_date)
players_removed                  (name, position, transaction_date)
injured_players_unavailable      (name, position, expected_return)
projected_lineup_war_added
projected_lineup_war_lost
starter_quality_delta
bullpen_quality_delta
bench_depth_delta
defensive_quality_delta
baserunning_delta
roster_continuity_penalty
minor_league_replacement_count

current_roster_strength =
  season_baseline_strength
  + acquisition_adjustment
  - departure_adjustment
  - injury_adjustment
  - replacement_uncertainty
```

The model must explicitly answer, for any post-deadline game:
- Which pitchers were actually traded?
- Which relievers are available tonight?
- Which hitters are missing?
- Who replaced the departed players?
- How large is the uncertainty from recent call-ups?
- Are full-season statistics still representative of this roster?

**Hard block:**

```text
POST_DEADLINE_ROSTER_STATE_UNRESOLVED
=> no calibrated game probability
=> maximum MODEL_QUALIFIED_HOLD
```

---

## Component 2 — Confirmed Lineup Strength

```text
confirmed_lineup                        (ordered list of 9 hitters, confirmation source, timestamp)
missing_regulars                        (list with injury/rest reason)
replacement_hitters                     (list with role)
lineup_woba_vs_pitcher_hand
lineup_xwoba_vs_pitcher_hand
lineup_k_rate_vs_pitcher_hand
lineup_bb_rate_vs_pitcher_hand
lineup_iso_vs_pitcher_hand
lineup_contact_rate
lineup_chase_rate
lineup_speed_score
lineup_defensive_value
lineup_strength_vs_season_average
projected_lineup_quality_vs_LHP
projected_lineup_quality_vs_RHP
```

Statistics must be computed for **tonight's nine confirmed hitters**, not for
the team as a whole. When the lineup has materially changed from the season roster,
tonight's lineup stats outweigh generic team splits.

---

## Component 3 — Pitch Quality and Pitch-Mix Trend

For each starting pitcher:

```text
fastball_velocity
velocity_change_last_3_starts
pitch_mix_season                         (% usage by pitch type)
pitch_mix_last_3_starts                  (% usage by pitch type)
pitch_usage_change                       (direction and magnitude)
whiff_rate_by_pitch
csw_rate_by_pitch
opponent_run_value_vs_pitch
opponent_whiff_rate_vs_pitch
hard_hit_rate
barrel_rate
first_pitch_strike_rate
zone_rate
walk_rate
pitches_per_plate_appearance
times_through_order_penalty
```

**Pitch-mix matchup score:**

```text
pitch_mix_matchup_score =
  Σ (
    pitch_usage
    × pitcher_pitch_quality
    × opponent_vulnerability_to_pitch
  )
```

A pitch-mix change (e.g., curveball usage rising to 50%) affects the probability
only when: verified from pitch-tracking data, sample dates recorded, opposing
lineup's performance against that pitch measured, and the change is large enough
to survive regression.

---

## Component 4 — Starting Pitcher Pathway Distribution

Do not compare starters using ERA alone.

```text
starter_expected_innings
starter_run_distribution
starter_strikeout_distribution
starter_walk_distribution
starter_baserunner_distribution
starter_home_run_distribution
probability_exit_before_4_IP
probability_exit_before_5_IP
probability_quality_start
probability_allow_0_to_2_runs
probability_allow_3_plus_runs
```

Required conditional win probability outputs:

```text
P(team wins | normal starter outing)
P(team wins | early starter failure)
P(team wins | starter dominates)
```

WOW's pitcher failure-path rules already prohibit substituting a normal-outing
projection for the unconditional probability. The moneyline model applies the same
constraint: `P(win)` must be the unconditional probability after failure paths,
not the conditional probability assuming the starter succeeds.

---

## Component 5 — Daily Bullpen Availability

**Season bullpen ERA is not tonight's bullpen.** Distinguish explicitly:

```text
season_bullpen_era
current_active_bullpen_talent
relievers_available_tonight
performance_after_early_starter_exit
```

Per reliever:

```text
reliever
role
pitches_yesterday
pitches_last_2_days
appearances_last_3_days
days_rest
availability_status              (AVAILABLE | LIMITED | UNAVAILABLE | UNKNOWN)
velocity_change
recent_command
leverage_role
projected_availability_probability
```

Aggregate outputs:

```text
available_bullpen_quality
high_leverage_bullpen_quality
middle_relief_quality
bullpen_fatigue_penalty
bullpen_depth_after_early_hook
```

For a recently dismantled or reconstructed bullpen, `BULLPEN_AVAILABILITY_UNRESOLVED`
blocks the row.

---

## Component 6 — Current Offensive State

Rolling windows with small-sample safeguards:

```text
offense_last_7_days
offense_last_14_days
offense_last_30_days
offense_since_roster_change
offense_vs_pitcher_hand
offense_home_or_road_split
```

Metrics per window:

```text
wOBA
xwOBA
OPS
K%
BB%
ISO
hard_hit_rate
barrel_rate
runs_per_game
baserunners_per_game
clutch_sequencing_residual
```

Decomposed into sustainable and noisy components:

```text
offensive_form_adjustment
batted_ball_quality_adjustment
sequencing_regression_adjustment
```

Scoring 11 runs in 6 games does not automatically trigger a large upgrade if
expected metrics were weak. Conversely, strong expected contact supports a real
upgrade even with depressed scoring. The model must separate luck from skill.

---

## Component 7 — Opponent-Strength Normalization

Raw win-loss record is not enough.

```text
strength_of_schedule
opponent_adjusted_run_differential
opponent_adjusted_offense
opponent_adjusted_pitching
park_adjusted_performance
record_vs_top_half_teams
record_vs_bottom_half_teams
expected_record
base_runs_record
pythagorean_record

current_team_strength_rating =
  present_roster
  + opponent_adjusted_run_production
  + opponent_adjusted_run_prevention
  + starter_quality
  + available_bullpen
  + defense
  + park_and_weather
```

This must be timestamped. Opponent quality is a quantified feature, not a narrative
characterization such as "the Guardians have a strong pitching staff."

---

## Component 8 — Handedness and Platoon Matchup

Required when either starter is left-handed, when the lineup has significant
platoon splits, or when handedness materially affects the model.

```text
team_woba_vs_LHP
team_xwoba_vs_LHP
team_k_rate_vs_LHP
team_bb_rate_vs_LHP
team_iso_vs_LHP
projected_lineup_woba_vs_LHP
projected_lineup_sample_size
platoon_adjustment_reliability
```

When both starters are left-handed (or both right-handed), this becomes a
**primary model input**, not an optional annotation.

When the lineup has materially changed, tonight's lineup splits outweigh
generic team splits. State the sample sizes for both.

---

## Component 9 — Baserunning and Catcher-Control

Usually a small adjustment, but material in low-total games.

```text
team_stolen_base_attempt_rate
team_stolen_base_success_rate
pitcher_stolen_base_allowed
pitcher_pickoff_quality
catcher_pop_time
catcher_caught_stealing_rate
runner_advancement_value

expected_baserunning_runs
```

---

## Component 10 — Park and Weather Run Environment

Weather must modify the scoring distribution — not merely appear in a note.

Required inputs:

```text
temperature
humidity
wind_speed
wind_direction
precipitation_probability
delay_probability
roof_status
park_factor
air_density
```

Required outputs:

```text
run_environment_adjustment
home_run_environment_adjustment
starter_delay_exit_probability
bullpen_usage_weather_adjustment
```

Determine whether weather meaningfully changes:
- run scoring
- home-run carry
- pitcher grip and control
- rain-delay risk
- probability of early starter removal

`NOT_MATERIAL` is an acceptable status only when stated explicitly with a reason.

---

## Component 11 — Game-Script Simulation

Simulate at minimum the following branches, with probabilities:

```text
favorite_early_lead
underdog_early_lead
one_run_game_through_six
starter_exits_early
both_starters_pitch_well
high_scoring_game
low_scoring_game
extra_innings
bullpen_fatigue_event
key_reliever_unavailable
```

Required conditional outputs:

```text
P(team wins | starter quality start)
P(team wins | starter exits before fifth)
P(team wins | game tied after six)
P(team wins | leading after six)
P(team wins | trailing after six)
P(team wins | total runs <= line)
P(team wins | total runs > line)
```

If win probability is competitive only in one narrow scenario:

```text
GAME_SCRIPT_FRAGILITY = HIGH
```

A high-fragility candidate may not receive a top-tier probability label without
explicitly modeling the fragility path.

---

## Component 12 — News-to-Model Translation

Every news item must enter the model in this structured format:

```text
news_event
source
published_at
retrieved_at
affected_player_or_unit
affected_model_field
direction                    (positive | negative | neutral)
estimated_magnitude
confidence                   (high | medium | low | speculative)
already_reflected_in_market  (yes | partially | no | unknown)
```

News may not directly add or subtract arbitrary win-probability points.
A news item's direction and magnitude must flow through the affected model field
(e.g., `projected_lineup_wOBA`, `bullpen_quality`) before reaching the probability.

---

## Component 13 — Market Decomposition and Fair-Price Output

Required outputs:

```text
market_price                     (per named sportsbook with timestamp)
raw_implied_probability
opposing_price                   (per named sportsbook with timestamp)
market_hold
no_vig_probability
model_probability
calibrated_lower_bound
fair_moneyline_midpoint
fair_moneyline_lower_bound
minimum_acceptable_price
market_news_absorption_score     (what portion of news is already in the price)
```

**Prohibited:**

- Combining starter ERA and bullpen ERA into a single "team ERA" figure. Label
  each separately: `starter_era`, `bullpen_era`, `available_bullpen_projected_era`.
- Double-counting market information: if roster or lineup news has already moved
  the line, the model must not apply the full penalty again without checking
  `market_news_absorption_score`.
- Inferring a player-prop edge from the game thesis: "Cantillo is favored" does
  not prove Over 5.5 strikeouts. That prop requires its own ledger and model.

---

## Component 14 — Probability Bridge

The model must show exactly how it moved from the market prior to the final estimate.
Every line in the bridge must come from the model — not be manually assigned.

Required format:

```text
No-vig market prior                   [%]
Starting-pitcher adjustment           [%]
Confirmed-lineup adjustment           [%]
Post-deadline roster adjustment       [%]
Bullpen availability adjustment       [%]
Platoon adjustment                    [%]
Park/weather adjustment               [%]
Opponent-strength adjustment          [%]
Model shrinkage toward market         [%]
-----------------------------------------------
Raw model probability                 [%]
Calibration / uncertainty haircut     [%]
Calibrated lower bound                [%]
```

If `independent_model_probability` cannot be retrieved, the bridge cannot be
completed and the row must show:

```text
Independent model probability: NOT RETRIEVED
Calibrated probability: NOT AVAILABLE
Fair-price threshold: NOT VERIFIED
```

A "38–40% probability" derived from market price alone is a narrative estimate.
It does not pass the probability bridge gate.

---

## Minimum Enrichment Contract (JSON)

A moneyline row submitted to the backend must carry:

```json
{
  "roster_state": {
    "players_added": [],
    "players_removed": [],
    "injured_unavailable": [],
    "roster_snapshot_timestamp": ""
  },
  "lineups": {
    "away_confirmed": [],
    "home_confirmed": [],
    "confirmation_timestamp": ""
  },
  "starters": {
    "away": {},
    "home": {}
  },
  "bullpen": {
    "away_availability": [],
    "home_availability": [],
    "timestamp": ""
  },
  "team_context": {
    "strength_of_schedule": {},
    "opponent_adjusted_ratings": {},
    "current_roster_ratings": {}
  },
  "splits": {
    "projected_lineup_vs_LHP": {},
    "projected_lineup_vs_RHP": {}
  },
  "environment": {
    "park": "",
    "weather": {},
    "delay_risk": 0
  },
  "market": {
    "away_moneyline": 0,
    "home_moneyline": 0,
    "timestamp": ""
  }
}
```

Any field with an empty value is `NOT_RETRIEVED` and must be declared as such.
An empty JSON object `{}` without a `NOT_RETRIEVED` tag is treated as a silent
omission and blocks the row.

---

## Required Output Format

```text
1. Market and fair-price summary
2. Current-roster comparison
3. Confirmed lineup and platoon matchup
4. Starter distribution and pitch-mix matchup
5. Bullpen availability
6. Opponent-adjusted team strength
7. Weather and park
8. Game-script tree
9. Probability bridge
10. Failure paths
11. Fair price and minimum acceptable price
12. Terminal label and blockers
```

---

## Component 15 — TeamRankings Secondary Enrichment (Optional)

TeamRankings data is a **secondary enrichment source** — its absence does not
block or hold any MLB moneyline row. The base model runs with full force if
`enrichment["teamrankings"]` is not supplied.

### How to supply

Read the relevant matchup page on TeamRankings.com and include the following
block inside the row's enrichment object when submitting to the backend:

```json
{
  "teamrankings": {
    "source_status": "RETRIEVED",
    "source_url": "https://www.teamrankings.com/mlb/ranking/predictive-by-other",
    "retrieved_at": "2026-08-08T18:00:00Z",

    "matchup_win_prob_home": 0.57,

    "home": {
      "team_name": "Cleveland Guardians",
      "sport": "MLB",
      "predictive_rating": 3.2,
      "predictive_rank": 9,
      "home_rating": 4.1,
      "away_rating": 2.3,
      "strength_of_schedule": 0.503,
      "last_5_rating": 3.8,
      "last_10_rating": 3.1,
      "display_odds": -145,
      "retrieved_at": "2026-08-08T18:00:00Z",
      "freshness_age_hours": 0.5,
      "source_status": "RETRIEVED"
    },

    "away": {
      "team_name": "New York Mets",
      "sport": "MLB",
      "predictive_rating": 1.4,
      "predictive_rank": 18,
      "home_rating": 2.2,
      "away_rating": 0.6,
      "strength_of_schedule": 0.498,
      "last_5_rating": 1.0,
      "last_10_rating": 1.5,
      "display_odds": 125,
      "retrieved_at": "2026-08-08T18:00:00Z",
      "freshness_age_hours": 0.5,
      "source_status": "RETRIEVED"
    }
  }
}
```

### Key rules for MLB operators

| Rule | Detail |
|---|---|
| `matchup_win_prob_home` required | Raw predictive ratings alone cannot be converted to a win probability. Without this field, TR weight = 0. |
| Weight range | 7.5% default of the sport model ensemble; 10% hard ceiling. |
| Freshness | `freshness_age_hours > 4.0` → `STALE` → zero weight. Retrieve TR data within 4 hours of first pitch. |
| `display_odds` exclusion | `display_odds` from TR is stored for context but **never** copied to `sportsbook_odds` and **never** fed to the no-vig model. Copying it corrupts the market prior. |
| `PROXY_ONLY` → zero | If you reconstructed the data rather than reading it directly from TR, set `source_status="PROXY_ONLY"`. Weight will be zeroed per governance. |
| Absence is safe | If TR data is unavailable, omit the block. The backend returns `DATA_UNOBTAINABLE` for the TR submodel and the base model is completely unaffected. |

### Supported sports for this field

```text
MLB  NBA  WNBA  NFL  NCAAF  NCAAB
```

All other sports return `UNSUPPORTED_SPORT` with zero TR weight.

---

## Hard Blocks

```text
POST_DEADLINE_ROSTER_STATE_UNRESOLVED  — no calibrated probability
LINEUP_STRENGTH_UNRESOLVED             — no calibrated probability
STARTER_DISTRIBUTION_UNAVAILABLE       — no calibrated probability
BULLPEN_AVAILABILITY_UNRESOLVED        — no calibrated probability
GAME_SCRIPT_FRAGILITY = HIGH without fragility modeling — max WATCH
MARKET_NEWS_ABSORPTION_UNCHECKED
  with material news present            — max HOLD
PROBABILITY_BRIDGE_INCOMPLETE          — label capped at LLP_SCOUT
```
