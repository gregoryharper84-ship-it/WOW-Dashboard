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
3. Invoke `WOW_V17_RESEARCH_MARKET_CONTEXT_SKILL.md` for material candidates to refresh starters/lineups/rosters, injuries/status/team changes, recent + longer-run historical context, matchup inputs, rest/travel, venue/weather where applicable, and current exact/adjacent market evidence with source/as-of provenance.
4. Route each event to exactly one certified sport-specific controlling team/event model through LLP_TEAM_BETTING_ENGINE.
5. Where refreshed evidence is a certified fitted input, ensure it reaches the governed model path. Otherwise keep it evidence-only; do not invent a numeric adjustment.
6. Require the route's complete mutually exclusive outcome space and valid governed probability package.
7. Preserve sporting probability even when downstream market price is missing if the backend contract preserves it. Missing odds may block value/edge work, not erase a completed event probability.
8. Never use sportsbook implied probability, market consensus, external projection, ranking, record, or narrative as a replacement for the governed team/event model.
9. Run favorite failure-path and underdog/upset evaluation required by the team/event contract.
10. Classify market context as `EXACT_LINE`, `ADJACENT_LINE`, or `NO_MARKET`; when exact-line pricing exists, report no-vig/market disagreement and opener/current movement without redefining model probability.
11. Before any official ranking or card admission, apply the fail-closed semantics of `v17/team_event_official_publication_guard.py`. A completed sporting probability may still be displayed diagnostically when preserved by governance, but it may not become an official ranked pick unless the publication guard passes.
12. Rank official winner candidates by governed calibrated lower bound, then calibrated probability as a tie-breaker unless a stricter route-specific rule controls.
13. Keep unsupported sports/events fail-closed with exact typed status.

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

### Best Underdogs/Upsets
Use a separate table. A market underdog is not automatically a model upset pick. Include only sides whose governed sporting probability supports the upset thesis.

### Research / held probabilities
A sporting probability preserved by the backend while publication or ranking is blocked may be shown in a separate diagnostic section when useful. Label it clearly as research/held/unranked. Do not assign it an official rank, call it the best pick, or admit it to an official card.

## Publication rules
- Only rows with the required certified model output belong in official leaderboards.
- `probability_publishable=true` and `rank_eligible=true` are mandatory for official leaderboard/card admission. Either flag false is a hard official-publication block even when numeric probabilities exist.
- The row must carry V17 terminal-reducer authority/receipt. Host prose, Scout, Research, shadow tables, or a specialist alone cannot create official ranking eligibility.
- Never rank directly from `wow_mlb_forward_shadow_events`, `wow_mlb_forward_score_snapshots`, a `SHADOW_SCORED_*` status, or a `PASS_RESEARCH_BOUND`. Those artifacts are research evidence only unless a later governed route produces a separately certified publishable row.
- A calibrated point estimate must not be relabeled as its own lower bound. Official MLB ranking requires a certified bounds method and a genuine lower bound below the calibrated point estimate, with a valid upper bound above it.
- Official MLB ranking requires the confirmed lineup to be numerically bound into the current model package. Require a confirmed lineup context, a lineup identity fingerprint, and a model-input fingerprint/hash. Merely removing a lineup blocker without recomputing/binding the numeric model is insufficient.
- Official MLB favorite ranking requires an explicit numeric favorite failure-path package, including multiple quantified loss regimes, largest loss path, failure-path probability, and regime-model provenance. Narrative risks alone are insufficient.
- `MODEL_UNAVAILABLE`, model completion/scorer failures, invalid outputs, identity failures, or publication-guard failures remain diagnostic and unranked.
- One side or no pick per governed event decision.
- Research/market context never substitutes for the fitted model.
- Never fabricate a probability because the user asked for a minimum number of picks.
- Never backfill a postgame governed prediction to repair a missing pregame publication record.
- `can_execute=false` always.

## Regression incident — 2026-09-04 Athletics @ Mariners
A forward-shadow Seattle probability existed pregame but was marked `probability_publishable=false`. The game result does not establish model failure; Seattle losing was a plausible outcome under the research probability. The process failure was allowing a nonpublishable research row to be described conversationally as a strongest/#1 selection. This regression is fixed at the publication boundary: research probabilities may remain visible for diagnosis but can never become official ranks or official card selections without the complete governed publication package.
