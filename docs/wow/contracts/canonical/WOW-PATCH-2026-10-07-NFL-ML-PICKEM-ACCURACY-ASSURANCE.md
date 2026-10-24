# NFL ML / Pick'em — immutable weekly accuracy scoreboard

Issue #1519. Class A: audit-only, **not** a fitted-model update.

The NFL Pick'em card is determined by the controlling fitted NFL moneyline specialist's calibrated two-sided game-win probabilities. A valid probability-bearing source terminal can be held below betting approval and still be eligible for Pick'em; its source terminal must stay unchanged.

## What the scoreboard proves

`audit_governed_pickem_week` reconciles the complete supplied official weekly manifest against one Pick'em model receipt and one independently settled official winner for **every** event. It will fail closed on any missing game, duplicate identity, wrong specialist, stale/post-kickoff prediction, invalid two-sided distribution, unqualified probability-bearing source, missing settlement source, premature result, or unresolved tie-rule event.

It reports exact correct / total, straight-up top-1 accuracy, point-probability Brier and log loss on actual settled events, immutable prediction and settlement IDs, and one aspirational target bit:

```text
operator_target_met = actual_correct / source_game_count >= 0.90
```

For a 16-game full slate, 15/16 = 93.75% and 16/16 = 100%. Results are **observations, not forecasts** or promises of next-week accuracy. A completed 13-game bye-week slate is audited as 13, not quietly normalized to 16. All weekly scheduled games are required, including low-confidence matchups. A held betting-publication row may still be scored if it has a valid governed probability-bearing Pick'em receipt.

No probabilities are generated or edited. No sportsbooks, crowd pick percentages, odds, opponent standings or narrative reasoning can be substituted for the fitted NFL model. There is no logic to alter, approve, route, or execute wagers.

## Independent ownership and review

- Scout/Evidence: immutable manifest, event identity, trustworthy final scores, source freshness; **never** a probability.
- NFL Specialist/Model Research: incumbent baseline, OOD feature audit, challenger tests (#1342); Class C review required before fitted-model changes.
- Engineering: pure audit and typed completeness/failure contract; no changes to service runtime.
- Systems Intelligence & Reliability: stale/missing event checks, failure-drift review, source integrity, repeatability.
- Independent Verification/QA: synthetic fixture tests, exact-head protected CI; verify every failed input fails the *whole week*.
- Release Observability: bind the audit to an exact revision; do not imply green CI proves a production probability improvement.

## Current gap

This module is a passive audit. Actual multiweek performance reporting requires supplied **authentic** pregame model receipts and independent settled results. The 90–100% target is not an empirically established capability. Improving the current NFL champion is governed separately under #1342 and its existing forward-shadow experiment. The 2025 retrospective challenger proper-score improvements are not by themselves proof of 15+/16 future performance.

```text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```
