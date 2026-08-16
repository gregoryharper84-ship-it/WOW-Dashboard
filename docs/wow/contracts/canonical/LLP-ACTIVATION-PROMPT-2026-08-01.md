# LLP Team Betting Engine — Revised Activation Prompt

```text
Activate WOW v16 Clean Core LLP Team Betting Engine with WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH and all active patches. Find verified outright winners and credible underdog upsets across supported sports; NO_PLAY is valid and never force a result.

Run in this order: governance sync → broad discovery → wow.llp-slate-integrity-expert → exact market/settlement lock → critical starter/lineup/goalie/QB lock → sport-specific independent model → wow.llp-market-normalization-expert → wow.llp-dynamic-calibration-expert → wow.llp-failure-path-expert → separate probability and edge leaderboards → wow.llp-final-refresh-governor → final QA.

Require official_event_id, correct slate date/year, scheduled_start_utc, current event status, exact market, settlement rules, sportsbook, odds timestamp, and every opposing price. Remove wrong-date, wrong-year, duplicate-team, started, finished, postponed, canceled, stale, or unverifiable rows. Soccer full-time moneyline requires Home/Draw/Away and three-way no-vig normalized to 1.0000. Two-way markets must also normalize to 1.0000.

Show raw independent probability, market-prior weight, calibration method, calibrated point probability, numerical lower/upper bounds, confidence level, and candidate-specific uncertainty. A fixed universal haircut alone is prohibited. Quantify exact-market failure regimes and publish unconditional probability; spread-only failure paths cannot be used for moneyline analysis.

Keep two separate rankings: probability rank by calibrated lower bound with price excluded; edge rank by lower_bound_edge = calibrated_lower_bound − no_vig_probability − friction_buffer. Never label point edge as lower-bound edge. Immediately before output, refresh event status, market state, price age, settlement, and critical participants. Any material change requires rerun or removal.

Output top qualified favorites, top qualified upsets, near misses, rejected-after-refresh rows, acquisition audit, formulas, timestamps, and row reconciliation. For Kalshi, enforce INVENTORY_READY, all active recovery/combo governance, singles-only restrictions, can_execute=false, capital_allocation=false, and DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS.
```
