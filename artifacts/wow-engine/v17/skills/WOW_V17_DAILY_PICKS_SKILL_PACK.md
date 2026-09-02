# WOW V17 Daily Picks Skill Pack

status=V17_ACTIVE_INSTRUCTION_LAYER
can_execute=false
terminal_authority=V17_TERMINAL_REDUCER
probability_authority=GOVERNED_BACKEND_ONLY

## Purpose
This skill pack turns the normal V17 workflow into reusable user-facing skills without adding a new model layer or changing any fitted probability. It covers autonomous best-prop discovery, screenshot/PDF/pasted-board extraction, cross-sport ML winner/upset discovery, research/market context refresh, and final best-pick/core construction.

## Default invocation
Use any of these phrases:
- `Run V17 Daily Picks`
- `Run V17 Best Props`
- `Review these screenshots with V17`
- `Run V17 ML Winners`
- `Build the V17 Core`

If the user says only `Full model`, `V17`, `best picks today`, or equivalent in a betting context, prefer the Daily Picks orchestration unless the request clearly narrows to one lane.

## Automatic research/context stage
Before final ranking, invoke `WOW_V17_RESEARCH_MARKET_CONTEXT_SKILL.md` for material candidates. Refresh current event identity, starters/lineups/rosters, injuries/status/team changes, role/workload, relevant history and matchup context, rest/travel, venue/weather where applicable, and current exact/adjacent market evidence. Research may hydrate certified model inputs and audit results but never substitutes for the fitted model or invents a probability.

## Orchestration
### Mode A — DAILY_DISCOVERY
1. Run broad discovery through `WOW_V17_BEST_PROPS_SKILL.md` and `WOW_V17_ML_WINNERS_SKILL.md`.
2. Run research/market-context refresh on material candidates before final ranking.
3. When requested or useful, pass qualified candidates to `WOW_V17_PICK_CORE_SKILL.md`.

### Mode B — SCREENSHOT_REVIEW
1. Run `WOW_V17_SCREENSHOT_REVIEW_SKILL.md` first.
2. Extract every visible betting row before any scoring/filtering.
3. Route props to WOW and team/event ML rows to LLP.
4. Refresh research/market context for material candidates.
5. Rank only governed supported rows.

### Mode C — HYBRID
When screenshots are provided and the user also wants the best slate-wide plays:
1. extract and score screenshots;
2. independently discover the broader supported slate;
3. refresh research/market context;
4. deduplicate identical market identities;
5. present screenshot-origin and autonomous-discovery origin separately;
6. build the final core from the combined governed pool.

## Universal V17 rules
- WOW_BETTING_ENGINE owns player/scalar/prop rows.
- LLP_TEAM_BETTING_ENGINE owns team/event winner, favorite, underdog, upset, match-winner, and fight-winner rows.
- Exactly one controlling specialist owns each row.
- Scout/Research may gather and reconcile evidence but never substitute for a fitted model.
- Rank official model leaderboards by governed calibrated lower bound where the route requires it.
- Never relabel sportsbook implied probability, consensus projections, hit rates, narrative, rankings, or generic reasoning as model probability.
- `MODEL_UNAVAILABLE` and other typed fail-closed results are diagnostic outputs, not invitations to invent a probability.
- Missing market price may block value/economics analysis but must not erase completed sporting probability when the backend contract preserves it.
- Keep `EXACT_LINE`, `ADJACENT_LINE`, and `NO_MARKET` separate.
- Material research evidence must carry source/as-of provenance and must not use future information unavailable at decision time.
- `can_execute=false` always. Never place or approve a wager.

## Standard final response
Present only useful sections; do not force empty sections.

### Best Model-Discovered Props
For each official row show rank, sport/event, player + market + line + direction, model probability, calibrated probability, calibrated lower bound, terminal/model status, controlling specialist, exact/adjacent/no-market context, strongest supporting factors, and material contradiction/risk.

### Best Screenshot-Extracted Props
Same fields, plus source screenshot/page and extraction confidence.

### Best ML Winners
For each official row show team/competitor, opponent, calibrated probability, calibrated lower bound, status, controlling specialist, market context, and primary risks.

### Best Underdogs/Upsets
Keep separate from favorites/winners. Do not call a market underdog an upset pick unless the governed sporting probability supports that side.

### V17 Core
Return the strongest 3–5 independent plays, or fewer when fewer genuinely qualify. Never add filler to hit a quota.

## Diagnostic appendix
Unsupported or incomplete candidates may be summarized separately with exact typed statuses such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, OOD/rejection, or acquisition blockers. They must never be mixed into the official ranked leaderboard.
