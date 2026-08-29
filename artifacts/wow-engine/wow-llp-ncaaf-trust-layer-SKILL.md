# Skill: wow.llp-ncaaf-trust-layer

## Purpose

Make NCAAF outright-winner research a dedicated governed WOW v16 Clean Core lane rather than allowing generic LLP reasoning to imply trusted college-football probabilities.

This layer supplements, but does not replace, the controlling NCAAF fitted game-win model. It owns NCAAF-specific acquisition requirements, quarterback/depth-chart gating, failure-path decomposition, dynamic-calibration inputs, forward calibration/CLV evidence, and trust-state ceilings.

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Controlling-model invariant

```text
NCAAF outright winner
=> controlling event model must be a governed NCAAF fitted game-win model
=> generic LLP reasoning is supporting orchestration only
=> missing fitted model or eligible calibrator => MODEL_UNAVAILABLE / probability not publishable
```

No market prior, L5/L10 trend, power rating, qualitative matchup note, or manual estimate may replace the controlling event model.

## Required NCAAF evidence

Every candidate must account for:

```text
official_event_id
correct_date_and_year
scheduled_start_utc
venue
neutral_site
home_away
starting_qb_status
backup_qb_downgrade_value
offensive_line_injury_status
defensive_front_pass_rush_health
top_wr_rb_availability
travel_rest_spot
weather_and_wind
market_role
current_two_way_ml_prices
market_timestamp
no_vig_probability
model_timestamp
source_snapshot_id
```

Every critical field must retain source provenance/freshness through the existing WOW source-timestamp contract.

## Hard QB / depth-chart gate

A merely expected or unresolved quarterback does not clear NCAAF qualification.

```text
NCAAF_QB_STATUS_UNCONFIRMED
=> favorite ceiling WINNER_WATCH
=> underdog ceiling UPSET_WATCH
=> trusted qualification prohibited
```

Use native WOW terminal labels. The NCAAF blocker and trust state are metadata and may only lower the final Full Model ceiling.

If a backup is confirmed, `backup_qb_downgrade_value` must be explicitly modeled or the row carries:

```text
NCAAF_BACKUP_QB_DOWNGRADE_UNRESOLVED
```

## Required failure regimes

The event model must quantify a mutually exclusive, collectively exhaustive top-level scenario partition containing at least:

```text
BASE_SCRIPT
QB_UNDERPERFORMANCE_OR_BACKUP
TURNOVER_NEGATIVE_GAME
EXPLOSIVE_PLAY_ALLOWED
WEATHER_OR_LOW_POSSESSION_VARIANCE
SPECIAL_TEAMS_OR_FIELD_POSITION_SWING
```

For every regime publish:

```text
P(regime)
P(win | regime)
regime loss contribution
```

Required identity:

```text
P(win unconditional) = sum_i P(regime_i) * P(win | regime_i)
sum_i P(regime_i) = 1
```

Narrative-only risk is invalid. Shared causes must be resolved before top-level regime weighting so probability mass is not double-counted.

Required outputs:

```text
unconditional_probability
failure_path_score
largest_failure_path
largest_failure_contribution
regime_probability_sum
```

## NCAAF dynamic-calibration inputs

Feed these candidate-specific components into the existing dynamic calibrator:

```text
conference_tier
FBS_vs_FCS
QB_certainty
depth_chart_certainty
injury_reporting_quality
market_liquidity
weather_variance
team_tempo
turnover_volatility
special_teams_volatility
model_disagreement
```

These extend, not replace, the universal calibration inputs. Universal fixed haircuts remain prohibited.

## Calibration / outcome ledger

Every NCAAF pregame candidate must persist enough immutable evidence to reconstruct:

```text
date
sport
league
event_id
team
opponent
market_role
market
selection
source
price_if_available
opposing_price_if_available
no_vig_if_available
independent_probability
market_prior_weight
unconditional_probability
calibrated_probability
lower_bound
upper_bound
terminal_label
blockers
failure_tags
model_timestamp
closing_price
closing_no_vig
result
brier_score
log_loss
clv
clv_grade
postmortem_note
```

The additive SQL contract is `ncaaf_trust_schema.sql`, with joined view:

```text
wow_ncaaf_calibration_ledger
```

## Automatic closing-line capture

Implementation module:

```text
ncaaf_closing_capture.py
```

Recommended scheduler cadence:

```text
every 5 minutes
```

The collector only reads a configured, approved two-way moneyline feed and writes calibration evidence. It never sends an order or modifies a market.

Required production configuration:

```text
SUPABASE_URL
SUPABASE_SERVICE_KEY
WOW_NCAAF_MARKET_FEED_URL
```

Optional authenticated read-only feed token:

```text
WOW_NCAAF_MARKET_FEED_TOKEN
```

No default sportsbook provider is hard-coded. Missing feed configuration must fail closed; synthetic or guessed closing prices are prohibited.

For every eligible row, capture the freshest exact governed pregame moneyline snapshot when available:

```text
closing_price_american
closing_opposing_price_american
closing_no_vig
closing_snapshot_timestamp
```

Required close identity/freshness rules:

```text
same official_event_id
same team
same opponent
same MONEYLINE market
quote timestamp < scheduled start
quote age <= 5 minutes at capture
both moneyline sides present
```

The scheduler should repeatedly upsert a valid pregame snapshot inside the final 15-minute window; the latest valid pre-kickoff quote becomes the stored close. After kickoff, an unresolved row is explicitly marked `NO_CLOSE_AVAILABLE` instead of being omitted from calibration.

Compare the selection-side entry no-vig probability with the same selection-side closing no-vig probability.

```text
closing_no_vig > entry_no_vig  => BEAT_CLOSE
closing_no_vig = entry_no_vig  => CLOSED_SAME
closing_no_vig < entry_no_vig  => LOST_TO_CLOSE
no valid close                  => NO_CLOSE_AVAILABLE
```

CLV grading is evaluation evidence only. It does not authorize execution.

## Trust-state machine

Trust states are monotonic publication ceilings, separate from model calibration method.

### `NCAAF_TEST_ONLY`

```text
settled_candidates < 25
or 25-row calibration review not passed
```

Ceiling:

```text
RESEARCH_INTEREST
```

### `NCAAF_WATCH`

```text
25-row review passed
but settled_candidates < 50
or 50-row confirmation not passed
or required CLV/ROI evidence is unavailable/insufficient
```

Ceiling:

```text
WINNER_WATCH / UPSET_WATCH
```

### `NCAAF_PRIMARY_CANDIDATE`

```text
settled_candidates >= 50
25-row review passed
50-row confirmation passed
CLV+ rate >= 55%
ROI > 0
no repeating failure tag
```

Maximum NCAAF trust-layer ceiling:

```text
MODEL_QUALIFIED_HOLD
```

### `NCAAF_TRUSTED`

Requires the 50-row overall confirmation above plus:

```text
NCAAF moneyline bucket candidates >= 20
CLV+ rate >= 60%
ROI > 0
no repeating failure tag
```

The 20-row bucket rule may not bypass the 25-row review or 50-row confirmation.

### `NCAAF_SCALE_ELIGIBLE`

```text
settled_candidates >= 100
NCAAF_TRUSTED conditions still pass
CLV+ rate >= 60%
ROI > 0
no active banned failure pattern
```

`SCALE_ELIGIBLE` does not change `can_execute=false` and does not by itself imply money approval.

## Full Model placement

For NCAAF full-game outright winners:

```text
0 governance / safety
0.5 calibration-health precheck
1 discovery
2 slate/event identity
3 exact moneyline / settlement identity
4 provenance / freshness
4.5 typed hydration / objective readiness
5 NCAAF role + QB/depth-chart/status gate
6 role-valid historical evidence where applicable
7 probability component ledger
8 governed NCAAF fitted game-win model
9 NCAAF matchup/trench/skill/tempo model
10 NOT_APPLICABLE for non-prop side audit; both event sides still normalize
11 NCAAF quantified failure regimes / unconditional probability
12 dynamic calibration + NCAAF uncertainty components
13 strict 0<p<1 and HOME+AWAY normalization
14 market drift/cause where governed
15 exact two-way ML / no-vig / settlement / money audit where requested
16 objective separation
17 dependency / structure
18 session / directional / duplicate-thesis exposure
19 weakest-leg cycle when part of a card
20 final refresh including QB, lineups, weather, market role, price
21 reconciliation
22 immutable pregame write
23 strict terminal-ceiling reduction including NCAAF trust state
24 native-label output
```

## Final refresh

Immediately before presentation recheck:

```text
event status
starting QB / depth chart
critical OL / defensive-front / WR / RB status
weather and wind
market role
exact two-way ML prices and timestamp
settlement identity
source conflicts
```

Any material change invalidates stale scoring and requires rerun or fail-closed removal.

## Current rollout rule

Until real forward evidence satisfies the trust-state machine:

```text
NCAAF_TRUST_STATE = NCAAF_TEST_ONLY or NCAAF_WATCH
trusted qualification = false
can_execute=false
```

A completed trust layer does not mean the underlying NCAAF fitted model exists. Production probability publication remains blocked until the real controlling artifact, eligible event calibrator, calibration-health evidence, immutable prediction/outcome cycle, configured read-only closing feed + scheduler, and final Full Model continuation are all proven.
