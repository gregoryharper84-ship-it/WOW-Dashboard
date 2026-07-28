---
name: LLP MLB Winner Preflight Gate
description: Mandatory three-gate pre-upgrade check for all MLB Kalshi winner/moneyline rows. Patch-level required. Blocks FINAL_APPROVED / MONEY_QUALIFIED without confirmed starter, lineup, clean event/weather, and positive price edge.
---

## What was built

`gate_engine/llp_mlb_winner_preflight.py` — new module, wired into pipeline.py just before `classifier.classify(row)`.

## Three gates

**Gate 1 — Starter / Lineup confirmation**
- `starter_status` must be `CONFIRMED` or `PROBABLE_STRONG`
- `lineup_status` must be `CONFIRMED` or `PROJECTED_ACCEPTABLE`; `PROBABLE_ONLY` is a watch cap (not hard block)
- Missing → watch cap (DATA_UNOBTAINABLE)

**Gate 2 — Weather / Event status**
- `event_status` must be `SCHEDULED` or `ACTIVE_PREGAME_VALID`
- `POSTPONED` / `CANCELLED` / `SUSPENDED` → `SLATE_PURGE` (pick dies; doubleheader restart = new event, full rerun)
- `weather_status` in {`MATERIAL_RISK`, `DELAY_RISK`, `RAINOUT_RISK`} → watch cap

**Gate 3 — No-vig / Model above Kalshi breakeven**
- `kalshi_breakeven_probability = 1 / kalshi_multiplier`
- `sportsbook_no_vig_probability >= breakeven` (else `NO_VIG_BELOW_BREAKEVEN`)
- `calibrated_probability_lower_bound >= breakeven + buffer` (else `MODEL_LOWER_BOUND_BELOW_BREAKEVEN`)
- Buffer: 0.020 when multiplier < 1.60x; 0.015 when >= 1.60x
- Missing price fields → hard block (fail-closed; math cannot be performed)

## Enforcement tiers (priority order)

1. `POSTPONED/CANCELLED/SUSPENDED` → `SLATE_PURGE` (row dies)
2. Gate 3 failures or missing price fields → `MLB_WINNER_PREFLIGHT_BLOCK` (new label, in REJECT_LABELS)
3. Gate 1/2 non-fatal issues → `MARKET_VERIFIED_HOLD` cap (watch, not rejected)
4. PASS → no change to `terminal_label`; classifier proceeds normally

## Labels added

- `PropLabel.MLB_WINNER_PREFLIGHT_BLOCK` added to `gate_engine/labels.py` and to `REJECT_LABELS`

## Pipeline hook

`gate_engine/pipeline.py` — `llp_mlb_winner_preflight.run(row)` inserted immediately before `classifier.classify(row)` in the classifier loop (after ledger, mutex, event grouping, settlement conflict all complete).

## Output fields stamped on every activated row

`preflight_checked`, `preflight_status`, `upgrade_allowed`, `preflight_blockers`, `kalshi_breakeven_probability`, `breakeven_gap`, `starter_status`, `starter_source`, `lineup_status`, `lineup_source`, `event_status`, `weather_status`, `weather_source`, `kalshi_multiplier`, `sportsbook_no_vig_probability`, `model_probability`, `calibrated_probability_lower_bound`, plus `gates["mlb_winner_preflight"]` record.

## Tests

`gate_engine/tests/test_llp_mlb_winner_preflight.py` — 38 tests, all passing:
- 6 reviewer-mandated regression tests (Tests 1–6 from spec)
- Scope/no-op guards (5)
- Gate 1 edge cases (5)
- Gate 2 edge cases (5)
- Gate 3 breakeven math (8)
- Architecture invariants (9): can_execute=False, gates record, label in REJECT_LABELS, kill > hard > watch priority

## Key design decisions

**Why Gate 1/2 failures → WATCH cap not hard block:** Starters post 2–4 hours before game time. NO_STARTER_CONFIRMATION at roster-lock time is expected and resolves. Rejecting outright would kill every pre-confirmation scan.

**Why Gate 3 failures → hard block:** The breakeven math either works or it doesn't. Missing price data means the edge claim is unverifiable; fail-closed is the only safe default.

**Why missing Gate 3 fields → hard block not watch:** The gate exists to enforce price edge. Allowing a watch cap when price data is missing would defeat its purpose — the row could be re-examined later with fabricated numbers.
