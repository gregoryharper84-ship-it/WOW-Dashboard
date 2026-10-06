# WOW V17 MLB In-Play Replay V1 — Experiment Receipt

Date: 2026-10-05  
Parent issue: #1408  
Change class: C — research only  
Terminal state: **EXPERIMENT_CREATED**

## Purpose

Create a leakage-safe historical point-in-time state corpus from the official MLB
Stats API event tree as the first governed input to an MLB in-play winner
challenger.

This experiment does **not** fit, calibrate, register, promote, publish, rank, or
execute a sporting probability.

## Source governance

- Sport: MLB
- Provider: MLB Stats API official game feed
- Evidence domain: SPORTING
- Existing historical source manifest state: V17_APPROVED
- Source payload must be bound to a SHA-256 digest.
- Sportsbook, market, public, or provider win probabilities are not accepted as
  sporting-probability authority.

## Leakage contract

Each replay row is reconstructed after a completed plate appearance using only
state available at that point in the official event tree:

- inning and half;
- outs;
- cumulative home/away score at that plate appearance;
- home score differential;
- batting side;
- base occupancy reconstructed from runner movements;
- batter and pitcher identifiers when present;
- extra-inning state;
- point-in-time play timestamp when present.

The final winner is stored only as the supervised label. Final score is not
copied into the feature payload.

Regression coverage proves that changing later scoring and the eventual winner
does not modify earlier feature rows.

## Fail-closed behavior

Replay construction rejects:

- non-final feeds;
- invalid or tied final labels;
- missing/empty event trees;
- malformed score/inning/out state;
- non-strict plate-appearance ordering;
- missing/invalid source hashes;
- invalid event identity.

## Remaining Class C work

Before any MLB live specialist can be considered for promotion:

1. freeze a multi-season replay corpus with immutable source hashes;
2. audit feature completeness and missing-state regimes;
3. add pitcher/bullpen, batting-order, remaining-opportunity and extra-inning
   context without future leakage;
4. define chronological train/calibration/holdout boundaries;
5. fit challenger model(s);
6. evaluate log loss and Brier score;
7. evaluate reliability by probability and game-state regime;
8. validate predictive lower bounds;
9. perform counterexample review;
10. run untouched holdout and true forward shadow;
11. freeze artifact/calibrator/bounds package;
12. complete governed promotion review.

`probability_publishable=false`  
`automatic_promotion=false`  
`can_execute=false`
