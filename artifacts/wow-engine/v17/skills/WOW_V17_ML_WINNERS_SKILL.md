# WOW V17 ML Winners Skill

skill_id=WOW_V17_ML_WINNERS
owner=LLP_TEAM_BETTING_ENGINE
can_execute=false

## Trigger
Use for requests such as `best ML winners today`, `best winners and upsets`, `full model ML`, or the team/event portion of `Run V17 Daily Picks`.

## Objective
Find the strongest governed team/event winner probabilities across all supported sports scheduled today, while keeping favorites/winners and legitimate underdogs/upsets as separate leaderboards.

This skill remains probability-only. A high ranking here is **not** permission to treat the row as a cash single. Cash/profitability promotion is a separate downstream contract.

## Workflow
1. Discover today's eligible team/event slate across supported sports.
2. Resolve exact event identity, participants, scheduled time, and current status.
3. Invoke `WOW_V17_RESEARCH_MARKET_CONTEXT_SKILL.md` for material candidates to refresh starters/lineups/rosters, injuries/status/team changes, recent + longer-run historical context, matchup inputs, rest/travel, venue/weather where applicable, and current exact/adjacent market evidence with source/as-of provenance.
4. Route each event to exactly one certified sport-specific controlling team/event model through LLP_TEAM_BETTING_ENGINE.
5. Where refreshed evidence is a certified fitted input, ensure it reaches the governed model path. Otherwise keep it evidence-only; do not invent a numeric adjustment.
6. Require the route's complete mutually exclusive outcome space and valid governed probability package.
7. Preserve sporting probability even when downstream market price is missing if the backend contract preserves it. Missing odds may block value/edge work, not erase a completed event probability.
8. Never use sportsbook implied probability, market consensus, external projection, ranking, record, or narrative as a replacement for the governed team/event model.
9. Run favorite failure-path and underdog/upset evaluation required by the team/event contract.
10. Classify market context as `EXACT_LINE`, `ADJACENT_LINE`, or `NO_MARKET`; when exact-line pricing exists, report no-vig/market disagreement and opener/current movement without redefining model probability.
11. Rank official winner candidates by calibrated lower bound, then calibrated probability as a tie-breaker unless a stricter route-specific rule controls.
12. Keep unsupported sports/events fail-closed with exact typed status.

## Cash/profitability handoff — P0 invariant
When the user asks to use a PrizePicks Game Winner as a cash single, in a profitability plan, or in any paid-card/value context, this skill stops at the sporting-probability package and hands the row downstream.

Required downstream gate:

```text
v17.game_winner_cash_single_gate.evaluate_game_winner_cash_single
```

The downstream gate must independently verify current payout economics and exact market evidence. The following are intentionally different:

```text
rank_eligible=true
cash_single_eligible=true
```

`rank_eligible=true` is sufficient only for the probability leaderboard. It is never sufficient for cash/profitability inclusion.

If the cash gate rejects a row, preserve the model probability, calibrated probability, calibrated lower bound, and leaderboard rank. Do not rewrite sporting probability because price/value failed.

## Required output
### Best ML Winners
For each official row show:
- rank
- sport/league
- event
- selected team/competitor
- opponent
- model probability
- calibrated probability
- calibrated lower bound
- terminal/model status
- controlling specialist/model
- primary supporting factors
- material risks/failure paths
- market-evidence type and exact-line/no-vig or movement context when available
- evidence as-of/provenance summary when material

When the user requested cash/profitability use, also show the downstream cash-gate result separately rather than silently treating the probability rank as cash qualification.

### Best Underdogs/Upsets
Use a separate table. A market underdog is not automatically a model upset pick. Include only sides whose governed sporting probability supports the upset thesis.

## Publication rules
- Only rows with the required certified model output belong in official leaderboards.
- `MODEL_UNAVAILABLE`, model completion/scorer failures, invalid outputs, or identity failures remain diagnostic and unranked.
- One side or no pick per governed event decision.
- Research/market context never substitutes for the fitted model.
- Never fabricate a probability because the user asked for a minimum number of picks.
- Never promote directly from `Best ML Winners` into a cash/profitability pool without the downstream Game Winner cash-single gate.
- `can_execute=false` always.