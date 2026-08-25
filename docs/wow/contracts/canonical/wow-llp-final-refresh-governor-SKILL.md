# Skill: wow.llp-final-refresh-governor

## Purpose

Perform the final mandatory recheck immediately before any LLP favorite, upset, or alternate-line candidate is displayed.

## Governance

```text
lane_status=FINAL_FRESHNESS_GATE
can_execute=false
```

## Required Inputs

```text
official_event_id
scheduled_start_utc
event_status
status_timestamp
market_status
odds_timestamp
lineup_or_starter_status
lineup_or_starter_timestamp
injury_status
weather_status_if_material
settlement_identity
prior_model_timestamp
```

## Default Freshness Thresholds

```text
market_price_minutes <= 10
critical_status_minutes <= 15 when available
weather_minutes <= 30 when material
final_refresh_minutes <= 5 before output
```

## Required Checks

```text
event_not_started
event_not_finished
event_not_postponed
market_open
price_fresh
starter_lineup_goalie_QB_unchanged
injury_status_unchanged
market_role_unchanged
settlement_unchanged
no_new_source_conflict
```

## Decision Logic

```text
all checks pass => FINAL_REFRESH_PASS
any event/status failure => REMOVE_FROM_FINAL_OUTPUT
material price failure => MARKET_STALE_REMOVE
critical participant change => MODEL_RERUN_REQUIRED
```

A row requiring rerun cannot remain visible as qualified during that output cycle.

## Output

| Candidate | Event | Market | Critical Status | Price Age | Change Detected | Result |
|---|---|---|---|---:|---|---|

```text
refresh_timestamp=
rows_checked=
rows_passed=
rows_removed=
rows_rerun_required=
can_execute=false
```
