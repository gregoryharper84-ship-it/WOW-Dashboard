---
name: WEATHER_SCOUT logging pattern
description: Auto-log guard rules, Brier score formula, and settle endpoint behavior for the weather_scout_log table.
---

## Rule

Only `gaussian_forecast` mode evaluate calls write to `weather_scout_log`. Calls that return `binary_final_cli` (already-settled date) are silently skipped. This prevents a post-settlement re-evaluation from overwriting a completed Brier record.

**Why:** The Brier score is only meaningful against a pre-settlement prediction. If a user re-evaluates after the CLI is FINAL (for audit purposes), that call returns `binary_final_cli` mode — logging it would corrupt the calibration ledger with a "prediction" that was made after the outcome was known.

**How to apply:** The guard is in `wow_kalshi_weather_evaluate()` — `if scoring_mode == "gaussian_forecast": _log_weather_scout_row(...)`. Do not add logging in any other branch.

## ON CONFLICT behavior

`_log_weather_scout_row` uses `ON CONFLICT (city, scout_date) DO UPDATE ... WHERE weather_scout_log.settled_at IS NULL`. Re-evaluating the same (city, date) before settlement updates the forecast (e.g., NWS updated its afternoon forecast). After settlement, the row is frozen.

## Brier score

`brier_score = Σ (model_prob_i − outcome_i)²` over all brackets.  
`outcome_i = 1` if `observed_high` falls inside bracket `i`, else `0`.  
Returns `None` if no bracket matches (parse failure on label).  
Range: 0–2; below 0.25 is good for a 6-bracket distribution; a perfect point forecast scores 0.

Bracket label parser in `_compute_brier_score` handles: `≤N`, `≥N`, `N-M`, `<=N`, `>=N`.

## Milestone gates

- **Milestone 1:** 25 settled rows → run first Brier review; `milestone_1_ready` field in scout/log response
- **Milestone 2:** After Milestone 1 passes, consider unlocking `KALSHI_PLAYABLE_LIMIT_ONLY` for real evaluation

## Settle endpoint guards

1. CLI date-guard: if NWS auto-fetch returns a CLI whose `issuance_time[:10] != date_str`, returns 422 with both dates — user must supply `observed_high` manually
2. Duplicate settle: returns 409 with `settled_at` + `brier_score` of existing settlement
3. Missing scout row: returns 404 — must call evaluate first to create the row
4. `observed_source` field: `"nws_cli"` for auto-fetch, `"override"` for manual

## Table

`weather_scout_log` in the shared PostgreSQL DB. Created by `_ensure_weather_scout_schema()` called lazily on first access. UNIQUE on `(city, scout_date)`. Indexes on `(city, scout_date DESC)` and `settled_at` (partial, non-null).
