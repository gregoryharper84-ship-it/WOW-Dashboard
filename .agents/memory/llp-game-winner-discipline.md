---
name: LLP Game Winner Payout Discipline
description: How the WOW v16 Game Winner (h2h moneyline) payout discipline is split between this Flask engine and the orchestrator.
---

# Game Winner Payout Discipline (WOW v16 Clean Core)

Scoped to the **full-game Game Winner** lane only: exact `market == "h2h"`.
F5 (`h2h_1st_5_innings`) and spreads/totals are intentionally excluded — F5 has
its own routing lane (`F5_ML` / `ML_WATCH_ONLY`) and conflating them would fight
that router.

## Backend vs orchestrator boundary (decided with the user)

This engine has **no dollar/bankroll concept** — `kelly_stake` is a *fraction*
(0–1). So the backend enforces only odds/edge/verification gates and emits an
`inverted_stake_sizing` flag. The **orchestrator** owns: dollar bankroll, the $2
floor when bankroll < $25, the $ stake/net numbers, and post-game
"Q3 Lucky / False-Signal" labeling.

**Why:** repeatedly tempting to add dollar logic here, but it would be invented —
there is no bankroll input in the analyze path. Keep dollar math out of this
engine.

## Rules (by decimal price on the chosen side)

- `< 1.35x` → hard REJECT: tag `game-winner-below-min-payout`, badge floored to
  PASS, `final_decision` forced to PASS.
- `1.35x ≤ price < 1.50x` → approvable only if ALL: no-vig edge ≥ +0.03,
  starter+lineup both `confirmed`, `model_adjustments` non-empty, `kelly_stake`
  > 0; else tag `game-winner-short-price-unverified` (CANDIDATE cap).
- `inverted_stake_sizing` = decimal < 2.0 on the h2h lane.

## How to apply / gotcha

`_llp_decision` runs **before** the discipline, so forcing a badge cap alone
leaves `final_decision` stale (split-brain — capped badge but still shows as a
"winner/best bet" because `winners_ranked`/`best_bets` filter on
`final_decision`). The discipline therefore **also** rewrites `final_decision`:
reject → PASS; short-price-unverified BET/SMALL BET → WATCH. Any future
badge-only gate added after `_llp_decision` must do the same or it will leak into
the winners buckets.
