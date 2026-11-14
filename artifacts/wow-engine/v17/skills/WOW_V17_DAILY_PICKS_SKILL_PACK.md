# WOW V17 Daily Picks Skill Pack

status=V17_ACTIVE_INSTRUCTION_LAYER
can_execute=false
terminal_authority=V17_TERMINAL_REDUCER
probability_authority=GOVERNED_BACKEND_ONLY

## Purpose
This skill pack turns the normal V17 workflow into reusable user-facing skills without adding a new model layer or changing any fitted probability. It covers autonomous best-prop discovery, screenshot/PDF/pasted-board extraction, PrizePicks board-to-verified-pool/slip construction, cross-sport ML winner/upset discovery, research/market context refresh, and final best-pick/core construction.

## Default invocation
Use any of these phrases:
- `Run V17 Daily Picks`
- `Run V17 Best Props`
- `Review these screenshots with V17`
- `Run PrizePicks Board to Slips`
- `Run V17 ML Winners`
- `Build the V17 Core`

If the user says only `Full model`, `V17`, `best picks today`, or equivalent in a betting context, prefer the Daily Picks orchestration unless the request clearly narrows to one lane. If the user supplies a PrizePicks board and asks for `Full Model`, `best picks`, `highest hit probability`, or equivalent, invoke `WOW_V17_PRIZEPICKS_BOARD_TO_SLIPS_SKILL.md` in `POOL_ONLY` mode unless slip/card construction is explicitly requested.

## Automatic research/context stage
Before final ranking, invoke `WOW_V17_RESEARCH_MARKET_CONTEXT_SKILL.md` for material candidates. Refresh current event identity, starters/lineups/rosters, injuries/status/team changes, role/workload, relevant history and matchup context, rest/travel, venue/weather where applicable, and current exact/adjacent market evidence. Research may hydrate certified model inputs and audit results but never substitutes for the fitted model or invents a probability.

For prop candidates with sufficient pregame role/opportunity/distribution evidence, run `WOW_V17_JS_STYLE_INTELLIGENCE_SKILL.md` as an additive research annotation. JS research priority may change research order only; it cannot remove non-JS rows from discovery, create model probability, modify calibration/lower bounds, or override typed blockers. Preserve same-event thesis clusters as dependency context for downstream construction rather than as a probability bonus.

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

### Mode D — CASH_PROFITABILITY
Use when the user asks for cash singles, a profitability plan, paid Game Winners, return/ROI optimization, or which governed candidates should actually enter a monetary workflow.

1. Produce the normal governed probability leaderboards first. Do not alter the sporting probabilities.
2. For every PrizePicks Game Winner candidate considered for cash use, retrieve the exact current gross multiplier and timestamp plus an exact fresh two-way/no-vig reference market.
3. Run `v17.game_winner_cash_single_gate.evaluate_game_winner_cash_single`.
4. Include only `cash_single_eligible=true` Game Winner rows in the cash/profitability pool.
5. A cash rejection does not remove the row from `Best ML Winners`; show the probability result separately when useful.
6. Run normal structure/exposure governance, final refresh, immutable pregame write, and V17 terminal reduction before publication.
7. Never substitute a lower-quality Game Winner merely to preserve a requested number of cash singles.

### Mode E — PRIZEPICKS_BOARD_TO_SLIPS
Use when a PrizePicks board/PDF/screenshots/pasted board is supplied and the user wants the strongest props, a verified pool, or slips/cards built from that board.

1. Invoke `WOW_V17_PRIZEPICKS_BOARD_TO_SLIPS_SKILL.md`.
2. Phase 1 is mandatory: extract every readable exact-line prop, expand undirected best-side rows into MORE + LESS before the canonical Action, score all canonical rows, preserve exact typed failures, rank only governed eligible rows, refresh the current board, and freeze the verified pool.
3. Default to `POOL_ONLY`; do not construct slips merely because the user asked for `best picks` or `Full Model`.
4. If slips/cards are explicitly requested, run Phase 2 only after Phase 1 completes. Use `WOW_V17_PICK_CORE_SKILL.md` for structure/exposure governance.
5. Preserve individual sporting probabilities exactly. Card-level independence estimates are portfolio math only and must never be relabeled governed sporting probability.
6. Shrink the card rather than add filler or unresolved dependent legs.

## Explicit full-slate game-winner card (NFL/NBA/NHL)

When the user requests all NFL, NBA and NHL game winners rather than a shortlist or cash singles, discover and independently verify every official scheduled playable event and use the existing LLP team/event synthesis owner. Present exactly **one researched winner per playable event**. No discretionary PASS for weak evidence, conflicting sources or missing certified probability: deepen injury, QB/goalie, rotation, volatility, scenario and current market reconciliation. Keep qualitative uncertainty visible, without asserting certified Full Model provenance.

Add a concise game-script finding beside each full-game research winner: expected winning path, opponent's realistic upset path, and the named swing factors. Preserve a source/as-of time and do not substitute the outcome actually observed after kickoff. Absent or late game scripts raise research-quality warnings, not an automatic PASS; numeric script weights remain unavailable unless separately fitted and certified.

Run read-only v17.llp_research_winner_card.audit_research_winner_card as the research user-card coverage preflight. Report its board_complete separately from research_evidence_fully_reconciled and from qualified fitted-model coverage. An unverified slate or incomplete/late/duplicate selection is an actual incomplete result, never silently filled by the validator. Model/terminal HOLD is preserved. Keep upset watch, fitted probabilities, research winners and submitted NFL Pick'em entries separated. Never mutate the shared Sheet, wager or automatically submit an entry.

## Multi-sport winner media brief and scenario sensitivity

For explicit all-game NFL/NBA/NHL winner cards, accompany every selected research winner with linked pregame media headlines (publisher and publication time), a source-supported central storyline, an opposing/counter storyline and the team's plausible game-script paths. Require expected, adverse and stress cases with qualitative script sensitivity and swing factors. Use authentic researched URLs and time-aware source evidence; never make up headlines, news, reporter quotes or calibrated scenario odds. The validator retains all game picks with research-quality warnings when coverage is missing and cannot promote them into Full Model. Keep narrative claims, confirmed personnel facts and market movement as distinguishable evidence classes.

## Retrospective research winner-card discipline

When reviewing yesterday's NFL/NBA/NHL research-only preferred winner card, run the read-only `v17.llp_research_card_retro.audit_research_card` contract with authentic frozen pregame receipts, an independently verified official slate, and independent settled results. Report `PREFERRED` and `UPSET_WATCH` cohorts separately; show missing receipts/results as HELD, and do not show an overall headline accuracy when slate reconciliation fails.

The optional `build_llp_learning_report(..., research_card_picks=..., research_card_finals=...)` integration keeps those descriptive grades outside certified fitted-model Brier/log-loss and calibration. Every realized miss produces an unvalidated sport-specific review hypothesis (NFL QB/turnover, NBA availability/replacement/offensive volatility, NHL goalie/rest/travel/special-teams/late-game). No automatic probability edit, upset flip, model promotion, historical pick rewrite, or Pick'em sheet mutation is permitted. Independent forward replay must demonstrate an improvement before release.

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
- Probability-card admission is not cash/profitability admission.
- For PrizePicks Game Winner cash use, `cash_single_eligible=true` is mandatory. `rank_eligible=true` alone is insufficient.
- `can_execute=false` always. Never place or approve a wager.

## Standard final response
Present only useful sections; do not force empty sections.

### Best Model-Discovered Props
For each official row show rank, sport/event, player + market + line + direction, model probability, calibrated probability, calibrated lower bound, terminal/model status, controlling specialist, exact/adjacent/no-market context, strongest supporting factors, and material contradiction/risk.

### Best Screenshot-Extracted Props
Same fields, plus source screenshot/page and extraction confidence.

### PrizePicks Verified Governed Pool
Show when `PRIZEPICKS_BOARD_TO_SLIPS` is active. Include rank, exact player/stat/line/direction, model probability, calibrated probability, calibrated lower bound, terminal/model status, current-board verification/as-of, and material risk. Account for every extracted base row in reconciliation; undirected rows must expose the MORE/LESS dispositions or exact Action-layer failure.

### Strongest PrizePicks 2-3 Leg Slips
Show only when Phase 2 was explicitly requested and a verified pool exists. Preserve each leg's governed probability, identify dependency/duplicate-thesis exposure, and show card-level probability only when certified; otherwise label any structurally independent product calculation as a non-governed independence-assumption estimate.

### Best ML Winners
For each official row show team/competitor, opponent, calibrated probability, calibrated lower bound, status, controlling specialist, market context, and primary risks.

### Best Underdogs/Upsets
Keep separate from favorites/winners. Do not call a market underdog an upset pick unless the governed sporting probability supports that side.

### Cash-Qualified Game Winners
Show this section only in `CASH_PROFITABILITY` mode. For each row include exact PrizePicks multiplier, platform break-even probability, exact market no-vig probability, calibrated lower bound, lower-bound platform edge, active safety buffer, lower-bound edge after buffer, freshness, cash gate status, and terminal ceiling. Do not include rows with `cash_single_eligible=false` as cash opportunities.

### V17 Core
Return the strongest 3–5 independent plays, or fewer when fewer genuinely qualify. Never add filler to hit a quota.

## Diagnostic appendix
Unsupported or incomplete candidates may be summarized separately with exact typed statuses such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, OOD/rejection, acquisition blockers, Action/transport failures, `REJECT_NO_EDGE`, or unresolved cash-promotion evidence. They must never be mixed into the official qualified cash pool.

## Ranked cross-sport winner lists and retrospective baseline (research-only)

When GH requests an across-sports top-N research winner list (including college football, college basketball or women's soccer), provide one selected winner for each chosen event and state that the ranked sample is not a complete official slate. Frozen pregame identity, side, time, independent research and opposing game scripts remain necessary. College basketball research should check opponent quality, half-court defense, shooting variance and neutral court; college football should check rush matchup, QB and turnovers; soccer should check starting XI/rotation, finishing variance and the **draw** as an explicit third outcome. Keep headlines and plausible narratives separate from verified factors; no post-result causal attribution.

For its postmortem, use the additive read-only `v17.llp_ranked_winner_retro.audit_ranked_winner_retro` for a separately verified ranked cohort. Snapshot the selected team as HOME/AWAY, unique pregame receipt, user-visible freeze, exact league event ID, and independently sourced official final. Match *the same-time* full-game two-way market favorite, or three-way **regulation** soccer favorite (HOME/DRAW/AWAY), with independently verified event-specific source/visibility receipts; a soccer draw is a lost pick for either team winner. When market evidence is missing, report baseline unavailable rather than inferring an edge. Present research correctness, market-favorite correctness and incremental correct as separate quantities; a single-day winning record or parity with market favorites is not fitted-model validation. Grade verified signed spreads separately from outright results and never infer a cover pick from a winner pick. Research causal stories remain hypotheses pending frozen out-of-time tests.

This additive ranked subset audit must not be used to claim complete NFL/NBA/NHL slate coverage, GH submitted-card success, calibrated V17 probabilities, model learning, or live editor/deploy sync. Existing official full-slate and fitted-model grading paths remain controlling, `V17_TERMINAL_REDUCER` remains sole terminal authority, and `can_execute=false`.
