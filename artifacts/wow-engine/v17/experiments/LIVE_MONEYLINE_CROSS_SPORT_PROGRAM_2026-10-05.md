# WOW V17 Cross-Sport In-Play Winner Program — Experiment Creation Receipt

Date: 2026-10-05  
Issue: #1408  
Change class: C — research only  
Terminal state: **EXPERIMENT_CREATED**

## Scope

The in-play probability program now explicitly covers every sport in the canonical V17 team/event manifest:

MLB, NFL, NBA, WNBA, NCAAF, NCAAB, NHL, SOCCER, TENNIS, PGA, MMA, BOXING, CRICKET.

This artifact does not fit or promote a model. It freezes the research ownership contract so future live-model work cannot collapse distinct sports into one generic probability formula.

## Invariants

- one sport-specific fitted specialist owns each live probability row;
- no pregame probability reuse after an event enters IN_PROGRESS;
- no sportsbook implied probability or provider/public win probability as sporting-probability authority;
- live state must be point-in-time and server-owned;
- calibration and lower-bound validation are per sport/outcome space;
- promotion is independent per sport;
- no automatic promotion;
- probability_publishable=false in this experiment package;
- can_execute=false.

## Outcome-space preservation

Binary team sports remain two-outcome only when settlement truly is binary.

The research manifest explicitly preserves:
- SOCCER: HOME / DRAW / AWAY;
- MMA/BOXING: participant A / participant B / DRAW_NO_CONTEST;
- CRICKET: team A / team B / TIE_NO_RESULT;
- PGA: market-specific field outcome rather than forcing a binary home/away abstraction.

## Priority order

Wave 1 uses sports that already have production/current-event state infrastructure:
MLB, NFL, NBA, WNBA, NCAAF, NCAAB, NHL.

Wave 2 wires point-in-time acquisition and replay for:
SOCCER, TENNIS, PGA, MMA, BOXING, CRICKET.

This priority does not reduce scope; it sequences implementation by evidence readiness.

## Required evidence before any sport can advance

For each sport independently:

1. frozen historical point-in-time live-state corpus;
2. leakage audit;
3. fitted challenger;
4. chronological train/calibration/holdout split;
5. proper scoring (Brier/log loss or multiclass equivalent);
6. reliability analysis by probability bin and game-state regime;
7. predictive lower-bound validation;
8. counterexample review;
9. untouched holdout;
10. true forward shadow;
11. immutable artifact/calibrator/bounds package;
12. independent verification;
13. governed promotion review.

No production scorer or registry is modified by this experiment.
