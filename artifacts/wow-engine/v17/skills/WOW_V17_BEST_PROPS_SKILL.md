# WOW V17 Best Props Skill

skill_id=WOW_V17_BEST_PROPS
owner=WOW_BETTING_ENGINE
can_execute=false

## Trigger
Use for requests such as `best props today`, `best model props`, `full model props`, or the props portion of `Run V17 Daily Picks`.

## Objective
Find the strongest governed player/scalar props available today across all supported sports and supported market types. Do not silently collapse the search to one familiar market such as pitcher strikeouts.

## Workflow
1. Discover the current slate and candidate prop markets across supported sports.
2. Build a broad candidate pool before ranking. Include every supported prop family the available board/data exposes.
3. Hydrate current identity/status/evidence: event identity, player identity, lineup/role/injury/status, workload/opportunity, relevant recent and longer-run samples, opponent context, venue/weather where applicable, and current market context.
4. Route each candidate to exactly one certified controlling prop specialist through WOW.
5. Require the route's valid numeric probability package. Where required, require calibrated probability and calibrated lower bound.
6. Preserve typed failures exactly. Do not replace an unavailable/failed fitted route with web projections, sportsbook odds, recent hit rate, or narrative probability.
7. Apply exact-vs-adjacent-line discipline. Adjacent sportsbook lines may inform context but are not exact-line no-vig authority.
8. Rank official supported candidates by calibrated lower bound, then calibrated probability as a tie-breaker unless a stricter route-specific contract controls.
9. Deep-research the highest-ranked rows for material contradictions. Where a certified model has a fitted input for the contradiction, ensure it reaches the numeric package; otherwise report it as evidence/risk without inventing a second penalty.
10. Return the best plays, not a quota.

## Required output
For each official pick show:
- rank
- sport and event
- player
- market/stat
- exact line
- MORE/LESS or route-specific side
- model probability
- calibrated probability
- calibrated lower bound
- terminal/model status
- controlling specialist
- market-evidence type: `EXACT_LINE`, `ADJACENT_LINE`, or `NO_MARKET`
- strongest supporting evidence
- material contradiction/risk

## Publication rules
- Official leaderboard: only governed fitted-model-supported rows with the numeric package required by that route.
- Research-interest rows may be shown separately but never blended into the official ranking.
- Unsupported exact lines, unsupported sports/stats, identity failures, or scorer failures remain fail-closed.
- Never manufacture a probability to fill the board.
- `can_execute=false` always.
