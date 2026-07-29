---
name: Kalshi NO-Side Tail Risk Patch
description: WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION — five reviewer-mandated additions to the Kalshi evaluation pipeline; can_execute=False, CANDIDATE_FORWARD_TEST_ONLY.
---

## Patch identity

```
PATCH_ID    = "WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION"
PATCH_STATUS = "CANDIDATE_FORWARD_TEST_ONLY"
can_execute = False
capital_allocation = False
```

## Files

- `kalshi_engine/no_side_tail_risk.py` — main gate module (all five gates + `run()`)
- `kalshi_engine/no_side_calibration_ledger.py` — separate Postgres table `kalshi_no_side_calibration` (price bucket tracking)
- `gate_engine/labels.py` — added `PropLabel.HISTORICAL_NON_OCCURRENCE_MISUSED`
- `kalshi_engine/tests/test_no_side_tail_risk.py` — 66 tests, all passing

## Five gates

**Gate 1 — COMPLEMENT_SIDE_SCAN**
Bidirectional YES/NO scoring on every contract. `P(NO) = 1 - P(YES)` only when binary. Third-state (void/push/dead-heat) sets `void_state_preserved=True` and `complement_valid=False`. Outright markets flag `FIELD_NORMALIZATION_REQUIRED`.

**Gate 2 — HIGH_PRICE_TAIL_RISK_GATE**
Triggers at entry_price ≥ 0.85. Publishes: `entry_cost`, `net_profit_if_win`, `maximum_loss`, `loss_to_win_ratio`, `wins_required_to_recover_one_loss`, `fee_adjusted_breakeven`. Extreme bucket (≥0.95) separately flagged. `HIGH_WIN_RATE_IS_NOT_POSITIVE_EV` warning always stamped.

**Gate 3 — HISTORICAL_ZERO_FALLACY_BLOCK**
- `model_probability == 0.0` without `logically_impossible=True` → BLOCKED (`HISTORICAL_NON_OCCURRENCE_MISUSED`)
- `model_probability == 1.0` without `logically_certain=True` → BLOCKED
- `historical_occurrence_count == 0` with non-zero probability → WARNING only

**Gate 4 — VOLUME_IS_NOT_DEPTH**
`depth_within_1c` and `depth_within_2c` are mandatory. `market_volume` without depth fields adds `VOLUME_PRESENTED_WITHOUT_DEPTH` violation. `volume_is_not_depth_rule=True` is always stamped.

**Gate 5 — NO-side calibration entry builder**
`build_calibration_entry()` produces a dict ready for `no_side_calibration_ledger.log_entry()`. Price buckets: 50-69c | 70-84c | 85-89c | 90-94c | 95-99c. Always `mode=paper`.

## Lane ceilings (priority order)

1. Fallacy blocked → `KALSHI_REJECT_NO_EDGE`
2. Missing depth + calibrated model → `KALSHI_DATA_UNOBTAINABLE`
3. Missing depth + uncalibrated → `KALSHI_WATCH`
4. Uncalibrated (no `calibrated_probability_lower_bound`) → `KALSHI_WATCH`
5. Lower bound ≤ fee-adjusted breakeven + positive point edge → `KALSHI_REJECT_NO_EDGE`
6. Lower bound ≤ fee-adjusted breakeven + no point edge → `KALSHI_WATCH`
7. All pass → `KALSHI_SINGLE_RESEARCH_ELIGIBLE`

## Calibration DB table

`kalshi_no_side_calibration` — separate from `kalshi_forecast_ledger` to avoid polluting Brier/CLV stats. Tracks per-bucket: `brier_score`, `log_loss`, `net_roi`, `loss_to_win_ratio`, `wins_required`, `maximum_drawdown`, `maker_or_taker`, `time_to_expiry_hours`.

## Integration note

The module is a standalone enrichment step, not yet wired into the live `/kalshi/evaluate-contract` or `/wow/kalshi/scan` routes. The reviewer specified "patch the system" — the module is ready to call; the route hook is the follow-on task.

## Key design decisions

**Why the fallacy gate is priority 1:** A model returning exactly 0.0 or 1.0 invalidates every downstream metric. There is no meaningful edge, breakeven, or calibration to compute on a point-mass probability.

**Why high-price is a warning, not a lane ceiling on its own:** A 92¢ NO contract with a strongly calibrated lower bound can still have positive expected value. The tail risk metrics expose the risk; the lane ceiling decision requires LB validation to confirm edge.

**Why a separate calibration table:** The 95–99¢ bucket must be reviewed in isolation. Mixing it with the main `kalshi_forecast_ledger` would dilute mean Brier/CLV stats for the entire Kalshi portfolio.
