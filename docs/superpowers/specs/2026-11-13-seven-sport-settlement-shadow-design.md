# LLP seven-sport settlement shadow — design

**Status:** Research-only implementation; not a trained or calibrated model.
**Owner:** `LLP_TEAM_BETTING_ENGINE`, Project Hub #2076; joint-game-state PR #2639.

## Intent
Interpret existing aligned joint-score research distributions under explicit sport-specific settlement rules. Avoid generic win/spread/total triplets implying universally compatible game-ending rules, especially for NCAAF and NCAAB.

## Approaches considered
1. **Chosen:** A narrow settlement adapter to existing joint-game-state PR #2639: reuses supplied score projections, validates sport/period/tie/line conventions and holds certification.
2. Rebuild simulations per sport: rejected as duplicated, uncertified probability infrastructure.
3. Insert heuristics into the production scorer: rejected; fixed Pythagorean exponents, generic sigmas and college upset narratives lack independent calibration.

## Contract
`settle_seven_sport_shadow(sport, settlement_basis, projection, score_unit=None)` receives the structural output of `project_joint_shadow`. Require stable event/model strings, lowercase SHA-256 digest *claim*, at least 50,000 claimed aligned draws, timestamp before start, normalized winner/spread/total outcome partitions, and immutable `shadow_only=True`, `probability_publishable=False`, `automatic_certification=False`, `can_execute=False`.

NFL/NCAAF: explicit PUSH or THREE_WAY tie rules, full-game including overtime. NBA/NCAAB: full-game including overtime, no ties. NHL: full-game including overtime and shootout, no ties. Soccer: regulation, three-way outcome. Tennis: completed-match game count supports game handicap and total only, never match-winner inference. Integer lines preserve exact push probability, half-point lines must have zero push mass. Soccer quarter handicaps and totals require original aligned score draws and are rejected rather than misclassified as full win/loss.

Output is always `RESEARCH_ONLY`, `rank_eligible=False`, `model_certified=False`; never claim a verified fitted artifact, market edge, independent pregame receipt, confidence interval, or production authority.

## Goals and non-goals
- Goal: reject inconsistent settlement, tennis winner inference, malformed numbers, unsupported Asian quarter lines, missing structural provenance, and promotion from research to certified.
- Non-goals: fitted modeling, new provider, model registration, official schedule/line sourcing, probabilistic calibration, distribution fitting, execution or automatic pool entry.
- Preserve `LLP_TEAM_BETTING_ENGINE`, `V17_TERMINAL_REDUCER`, `can_execute=false`, and NFL Pick'em owner boundaries.

## Fitted college model follow-on (not implemented here)
NCAAB: opponent-adjusted offensive and defensive ratings, possession pace, injury/roster continuity, three-point and foul tails. NCAAF: opponent-adjusted EPA/SP+, garbage-time policy, drives, explosiveness, clock/ball control and defensive havoc. Historical Pythagorean exponents and normal residual SDs are research baselines, not sport-certified constants. Require chronological disjoint train/ensemble/calibration/holdout; conditional calibration and uncertainty; frozen official-event pregame evidence; independent market-baseline comparison and QA/SIRT/Release gates. 
