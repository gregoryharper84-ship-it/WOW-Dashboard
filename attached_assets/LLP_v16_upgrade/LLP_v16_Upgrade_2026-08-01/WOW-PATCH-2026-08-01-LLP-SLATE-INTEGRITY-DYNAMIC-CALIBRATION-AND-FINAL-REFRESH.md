# WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH

## Status

```text
ACTIVE
patch_priority=CRITICAL
framework=WOW_v16_CLEAN_CORE
activation_date=2026-08-01
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

## Purpose

Make the LLP Team Betting Engine materially stronger than news-aggregation betting assistants by enforcing reproducible slate identity, exact market normalization, candidate-specific calibration, quantified failure paths, and a final freshness recheck before any winner or upset is presented.

This patch prevents:

- wrong-date, wrong-year, duplicate-team, started, final, postponed, or phantom events;
- two-way treatment of three-way soccer markets;
- raw implied probability being mislabeled as no-vig;
- point-model difference being mislabeled as lower-bound edge;
- universal fixed haircuts being presented as calibration;
- market-mismatched failure paths such as backdoor-cover risk on a moneyline;
- stale starters, lineups, goalies, quarterbacks, injuries, prices, or market roles;
- recommendations surviving after event start;
- next-day games leaking into a same-day slate;
- polished narrative substituting for model evidence.

## New Mandatory Skills

```text
wow.llp-slate-integrity-expert
wow.llp-market-normalization-expert
wow.llp-dynamic-calibration-expert
wow.llp-failure-path-expert
wow.llp-final-refresh-governor
```

## Universal LLP Call Order

```text
1. governance_sync
2. full_slate_discovery
3. slate_integrity_lock
4. exact_market_and_settlement_lock
5. critical_participant_lock
6. independent_sport_model
7. market_normalization
8. dynamic_calibration
9. failure_path_model
10. probability_and_edge_lane_separation
11. final_refresh_governor
12. lowest_ceiling_output
```

No step may be skipped. A downstream pass cannot erase an upstream blocker.

## Stage 1 — Full-Slate Discovery

Discovery is broad and non-qualifying.

Allowed labels:

```text
DISCOVERY_FAVORITE
DISCOVERY_UPSET
DISCOVERY_ALT_LINE
DISCOVERY_WATCH
```

Discovery rows are menus only. They may not receive probability, edge, money, or final labels until all mandatory gates pass.

## Stage 2 — Slate Integrity Lock

Every row must include:

```text
official_event_id
league
event_date_local
event_date_utc
scheduled_start_utc
home_participant
away_participant
venue
official_schedule_source
event_status
status_timestamp
```

Hard blockers:

```text
WRONG_DATE
WRONG_YEAR
EVENT_NOT_FOUND
EVENT_ALREADY_STARTED
EVENT_FINISHED
EVENT_POSTPONED
EVENT_CANCELED
DUPLICATE_TEAM_EVENT
PARTICIPANT_IDENTITY_CONFLICT
UNSUPPORTED_EVENT_STATUS
```

The same participant cannot appear in two same-league events in an impossible time window unless an official doubleheader or tournament structure is verified.

## Stage 3 — Exact Market and Settlement Lock

Required:

```text
market_type
period
selection
boundary_operator
settlement_rule
push_or_draw_treatment
sportsbook_or_exchange
odds_or_price
odds_timestamp
opposing_prices
```

Outright winner, spread, total, draw-no-bet, double chance, advancement, series, and derivative markets are not interchangeable.

### Soccer Rule

Full-time three-way soccer markets require:

```text
home_odds
draw_odds
away_odds
```

No soccer moneyline can pass with only two outcomes.

### Normalization Identity

```text
sum(two_way_no_vig)=1.0000 ± 0.0005
sum(three_way_no_vig)=1.0000 ± 0.0005
```

Failure returns:

```text
MARKET_NORMALIZATION_FAILURE
```

## Stage 4 — Critical Participant Lock

Sport-specific mandatory checks:

```text
MLB: confirmed/probable starters, projected/confirmed lineups, bullpen availability, weather
NBA/WNBA/NCAAB: active roster, expected starters, minutes/usage redistribution, rest/travel
NFL/NCAAF: starting quarterback, offensive-line health, critical defensive absences, weather
NHL: confirmed or adequately verified starting goalie
Soccer: projected/confirmed XI, goalkeeper, formation, suspensions, draw outcome
Tennis: participant status, surface, retirement settlement
Golf: field status, withdrawals, dead-heat rules
MMA/Boxing: participant status, weight/bout status, draw/no-contest rules
```

Any material status change invalidates the prior model and forces a fresh run.

## Stage 5 — Independent Probability Model

Required outputs:

```text
independent_model_probability
market_prior_probability
market_prior_weight
matchup_component_probability
role_status_adjustment
venue_rest_travel_adjustment
raw_model_probability
model_timestamp
```

If market prior weight exceeds 50%:

```text
MARKET_DEPENDENT_MODEL
highest_tier_prohibited=true
```

## Stage 6 — Market Normalization

For two-way markets:

```text
q_a = implied_probability(odds_a)
q_b = implied_probability(odds_b)
hold = q_a + q_b - 1
p_a_no_vig = q_a / (q_a + q_b)
p_b_no_vig = q_b / (q_a + q_b)
```

For three-way markets:

```text
p_i_no_vig = q_i / (q_home + q_draw + q_away)
```

Required separation:

```text
raw_implied_probability
market_hold
no_vig_probability
```

These fields may never be merged or relabeled.

## Stage 7 — Dynamic Calibration

A fixed universal percentage haircut is prohibited as the sole calibration method.

Required:

```text
raw_model_probability
calibration_method
historical_calibration_sample
base_calibration_error
sport_volatility_penalty
sample_size_penalty
lineup_or_starter_uncertainty
injury_uncertainty
market_disagreement_penalty
source_conflict_penalty
freshness_penalty
calibrated_point_probability
calibrated_probability_lower_bound
calibrated_probability_upper_bound
confidence_interval_level
```

Candidate-specific uncertainty must widen when critical information is projected, stale, contradictory, or recently changed.

If no valid calibration evidence exists:

```text
UNCALIBRATED_MODEL
highest_result=WINNER_WATCH or UPSET_WATCH
```

## Stage 8 — Failure-Path Model

Every probability must incorporate exact-market failure paths before final calibration.

Favorite outputs:

```text
primary_structural_advantage
secondary_advantage
largest_outright_loss_path
secondary_loss_paths
P(largest_loss_path)
P(other_material_loss_paths)
unconditional_win_probability
```

Underdog outputs:

```text
baseline_win_probability
matchup_advantage
variance_path
late_game_or_finish_path
favorite_failure_path
underdog_failure_path
unconditional_upset_probability
```

Moneyline failure paths must cause an outright loss. Spread-cover, backdoor-cover, or total-market scenarios cannot be used as moneyline failure paths unless they also produce an outright loss.

## Stage 9 — Probability and Edge Lane Separation

Maintain separate leaderboards.

### Probability Leaderboard

Rank by:

```text
calibrated_probability_lower_bound
```

Price does not control rank.

### Edge Leaderboard

Calculate:

```text
point_edge = calibrated_point_probability - no_vig_probability
lower_bound_edge = calibrated_probability_lower_bound - no_vig_probability - friction_buffer
```

Point edge may never be labeled lower-bound edge.

Default edge floors:

```text
liquid_major_market >= 1.5%
WNBA_or_lower_liquidity >= 2.0%
derivative_market >= 2.5%
alt_or_niche_market >= 3.0%
```

## Stage 10 — Final Refresh Governor

Immediately before presentation, recheck:

```text
event_not_started
market_open
odds_age_within_threshold
starter_or_lineup_unchanged
participant_status_unchanged
market_role_unchanged
settlement_unchanged
no_new_source_conflict
```

Default freshness:

```text
market_price <= 10 minutes
lineup_or_status <= 15 minutes when available
weather <= 30 minutes when material
final_refresh <= 5 minutes before output
```

Any failure removes the row from the final list.

## Final Labels

Probability lane:

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
```

Edge lane:

```text
EDGE_VERIFIED_HOLD
LINE_VALUE_ONLY
NO_EDGE
DATA_UNOBTAINABLE
```

No label authorizes execution.

## Required Output Footer

```text
events_discovered=
events_identity_verified=
events_removed_wrong_date=
events_removed_wrong_year=
events_removed_started=
events_removed_finished=
events_removed_duplicate=
critical_status_confirmed=
two_way_markets_normalized=
three_way_markets_normalized=
normalization_failures=
calibration_method=
uncalibrated_rows=
final_refresh_timestamp=
rows_removed_final_refresh=
price_used_for_probability_rank=false
price_used_for_edge_rank=true
lowest_ceiling=
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

## Acceptance Tests

1. A next-day event cannot appear in today's slate.
2. A finished event cannot survive final refresh.
3. A soccer full-time market with no draw price is rejected.
4. No-vig probabilities must normalize to one.
5. Raw implied probability cannot be labeled no-vig.
6. Point edge cannot be labeled lower-bound edge.
7. A universal 5% haircut alone cannot qualify as calibration.
8. A changed MLB starter invalidates the prior model.
9. A backdoor-cover scenario cannot serve as a moneyline failure path.
10. Every row must have an official event ID or be removed.
11. Duplicate-team impossible scheduling is rejected.
12. A stale price removes a row at final refresh.
13. Probability and edge leaderboards remain separate.
14. No forced winner or upset when none qualifies.
15. `can_execute=false` appears in every output.

## One-Line Definition

**This patch converts LLP from a candidate-ranking assistant into a slate-locked, market-normalized, dynamically calibrated, failure-aware, automatically refreshed outright-winner and upset auditing engine under WOW v16 Clean Core.**
