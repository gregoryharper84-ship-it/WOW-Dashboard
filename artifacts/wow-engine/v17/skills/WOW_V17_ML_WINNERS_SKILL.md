# WOW V17 ML Winners Skill

skill_id=WOW_V17_ML_WINNERS
owner=LLP_TEAM_BETTING_ENGINE
can_execute=false

## Trigger
Use for requests such as `best ML winners today`, `best winners and upsets`, `full model ML`, or the team/event portion of `Run V17 Daily Picks`.

## Objective
Find the strongest governed team/event winner probabilities across all supported sports scheduled today, while keeping favorites/winners and legitimate underdogs/upsets as separate leaderboards.

## Workflow
1. Discover today's eligible team/event slate across supported sports.
2. Resolve exact event identity, participants, scheduled time, and current status.
3. Hydrate sport-specific material context: starters/lineups/rosters, injuries/status, rest/travel, venue/weather where applicable, matchup inputs, and other required model features.
4. Route each event to exactly one certified sport-specific controlling team/event model through LLP_TEAM_BETTING_ENGINE.
5. Require the route's complete mutually exclusive outcome space and valid governed probability package.
6. Preserve sporting probability even when downstream market price is missing if the backend contract preserves it. Missing odds may block value/edge work, not erase a completed event probability.
7. Never use sportsbook implied probability, consensus projection, ranking, record, or narrative as a replacement for the governed team/event model.
8. Run favorite failure-path and underdog/upset evaluation required by the team/event contract.
9. Rank official winner candidates by calibrated lower bound, then calibrated probability as a tie-breaker unless a stricter route-specific rule controls.
10. Keep unsupported sports/events fail-closed with exact typed status.

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
- market availability/context if known

### Best Underdogs/Upsets
Use a separate table. A market underdog is not automatically a model upset pick. Include only sides whose governed sporting probability supports the upset thesis.

## Publication rules
- Only rows with the required certified model output belong in official leaderboards.
- `MODEL_UNAVAILABLE`, model completion/scorer failures, invalid outputs, or identity failures remain diagnostic and unranked.
- One side or no pick per governed event decision.
- Never fabricate a probability because the user asked for a minimum number of picks.
- `can_execute=false` always.
