# WOW V17 Daily Picks Skill Pack

status=V17_ACTIVE_INSTRUCTION_LAYER
can_execute=false
terminal_authority=V17_TERMINAL_REDUCER
probability_authority=GOVERNED_BACKEND_ONLY

## Purpose
This skill pack turns the normal V17 workflow into three reusable user-facing skills without adding a new model layer or changing any fitted probability. It covers:

1. autonomous best-prop discovery across supported sports;
2. screenshot/PDF/pasted-board extraction and governed review of every visible prop or ML row;
3. cross-sport ML winner/upset discovery and final best-pick/core construction.

## Default invocation
Use any of these phrases:

- `Run V17 Daily Picks`
- `Run V17 Best Props`
- `Review these screenshots with V17`
- `Run V17 ML Winners`
- `Build the V17 Core`

If the user says only `Full model`, `V17`, `best picks today`, or equivalent in a betting context, prefer the Daily Picks orchestration unless the request clearly narrows to one lane.

## Orchestration

### Mode A — DAILY_DISCOVERY
Run both:
- `WOW_V17_BEST_PROPS_SKILL.md`
- `WOW_V17_ML_WINNERS_SKILL.md`

Then, when requested or useful, pass qualified candidates to `WOW_V17_PICK_CORE_SKILL.md`.

### Mode B — SCREENSHOT_REVIEW
Run `WOW_V17_SCREENSHOT_REVIEW_SKILL.md` first. It must extract every visible betting row before any scoring or filtering. Route extracted props to WOW and extracted team/event ML rows to LLP. Then rank only governed supported rows.

### Mode C — HYBRID
When screenshots are provided and the user also wants the best slate-wide plays:
1. extract and score the screenshots;
2. independently discover the broader supported slate;
3. deduplicate identical market identities;
4. present screenshot-origin and autonomous-discovery origin separately;
5. build the final core from the combined governed pool.

## Universal V17 rules
- WOW_BETTING_ENGINE owns player/scalar/prop rows.
- LLP_TEAM_BETTING_ENGINE owns team/event winner, favorite, underdog, upset, match-winner, and fight-winner rows.
- Exactly one controlling specialist owns each row.
- Scout/Research may gather and reconcile evidence but never substitute for a fitted model.
- Rank official model leaderboards by governed calibrated lower bound where the route requires it.
- Never relabel sportsbook implied probability, consensus projections, hit rates, narrative, or generic reasoning as model probability.
- `MODEL_UNAVAILABLE` and other typed fail-closed results are diagnostic outputs, not invitations to invent a probability.
- Missing market price may block value/economics analysis but must not erase a completed sporting probability when the backend contract preserves it.
- Exact-line and adjacent-line evidence must remain separate.
- `can_execute=false` always. Never place or approve a wager.

## Standard final response
Present only useful sections; do not force empty sections.

### Best Model-Discovered Props
For each official row show:
- rank
- sport/event
- player + market + line + direction
- model probability
- calibrated probability
- calibrated lower bound
- terminal/model status
- controlling specialist
- exact/adjacent/no-market context
- primary supporting factors
- material contradiction/risk

### Best Screenshot-Extracted Props
Same fields, plus source screenshot/page and extraction confidence.

### Best ML Winners
For each official row show team/competitor, opponent, calibrated probability, calibrated lower bound, status, controlling specialist, and primary risks.

### Best Underdogs/Upsets
Keep separate from favorites/winners. Do not call a market underdog an upset pick unless the governed sporting probability supports that side.

### V17 Core
Return the strongest 3–5 independent plays, or fewer when fewer genuinely qualify. Never add filler to hit a quota.

## Diagnostic appendix
Unsupported or incomplete candidates may be summarized separately with exact typed statuses such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, OOD/rejection, or acquisition blockers. They must never be mixed into the official ranked leaderboard.
