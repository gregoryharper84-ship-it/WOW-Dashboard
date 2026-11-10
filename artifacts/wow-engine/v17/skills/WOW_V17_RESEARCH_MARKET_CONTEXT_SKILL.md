# WOW V17 Research + Market Context Skill

skill_id=WOW_V17_RESEARCH_MARKET_CONTEXT
owner=V17_SHARED_RESEARCH_MARKET_CONTEXT
can_execute=false
probability_authority=NONE
terminal_authority=NONE

## Trigger
Invoke automatically before final ranking for `WOW_V17_DAILY_PICKS`, `WOW_V17_BEST_PROPS`, `WOW_V17_ML_WINNERS`, and `WOW_V17_SCREENSHOT_REVIEW` whenever current external context can materially affect identity, fitted inputs, evidence quality, market interpretation, or final risk reporting. It may also be called directly with `Run V17 Research Context`.

## Purpose
Refresh and reconcile the evidence around a candidate without creating a new model layer. Research may hydrate certified model inputs and audit model output; it may never substitute for the controlling fitted model, invent a probability, or add an uncoded narrative probability penalty.

## Evidence contract
For each material candidate, gather when relevant and available:
1. exact schedule/event identity, opponent, venue and start time;
2. confirmed/probable starters, lineups, rotations/rosters and participant availability;
3. injuries, suspensions, scratches, team changes and status updates;
4. role, workload, opportunity and recent usage;
5. relevant recent form plus an appropriate longer-run historical sample;
6. opponent/matchup and handedness/style/surface/context splits when sport-specific;
7. rest, travel, back-to-back/fatigue context and venue/environment/weather where applicable;
8. current exact-line market prices across credible books when available;
9. opener/current movement and consensus/no-vig context when available;
10. source/provenance and an as-of timestamp for every material item.

## Temporal provenance
- Record when each material input became knowable.
- Do not use lineup, injury, closing-line, settled-result, or other future information that was unavailable at the decision timestamp.
- If evidence is stale, contradictory, or unavailable, record that condition instead of guessing.

## Sport-specific pregame uncertainty and postmortem learning

Prior to freezing a team-event winner, check material evidence by sport and mark `KNOWN`, `UNKNOWN` or `CONTRADICTORY` with an exact source and `available_to_user_at_utc`:
- **NFL:** quarterback status/efficiency, passing-matchup and protection context, turnover sensitivity, major starters and recent injury changes.
- **NBA:** actual player availability and late scratches, replacement rotation/usage upside, opponent offensive ceiling and shooting variance, rest/back-to-back and travel.
- **NHL:** confirmed (not projected) starting goaltender, back-to-back/rest/travel, injuries, special teams, and regulation/OT/shootout settlement exposure.

A missing or uncertain input is a typed risk or hydration blocker where the fitted specialist requires it; do not invent the missing input or apply a manual probability penalty. A news item first available **after** the immutable selection freeze cannot be attributed to the original decision. If it arrives before kickoff, create a new timestamped governed recheck rather than rewriting the old record. Information first available during the game may be an explanatory postmortem hypothesis, never a pregame feature.

For settled losses, compare only the frozen knowable features to independently verified outcomes. Register sport-specific hypotheses for out-of-time baseline/challenger replay; **one losing game does not prove any particular feature coefficient was wrong**. Preserve immutable original grades and require independent forward validation before changing calibrators or publishing improved-model claims.

## Escalation for mandatory full-slate research winner selections

For user-requested NFL/NBA/NHL full-game winner cards, uncertain or contradictory evidence triggers *more independent source reconciliation* and explicit available/unavailable or alternate-lineup scenarios, **not** a discretionary pass in the user-facing research card. Preserve any governed-model HELD status independently and never treat market implied probabilities as fitted model output.

Prioritize: NFL starting QB and protection, defensive personnel and turnover volatility; NBA actual star availability versus a questionable designation, replacement rotations, opponent shooting variance and back-to-backs; NHL confirmed rather than projected starting goalie, rest/travel, special teams and full-game OT/shootout exposure. Across sports compare opener and current favorite with as-of timestamps, resolve disagreements and distinguish known, unknown and contradictory evidence.

Seek at least two independent nonmarket publishers per matchup, time-stamp material research and mark source shortfalls, stale/future inputs, unresolved market conflicts and unsupported HIGH confidence as research-quality warnings. These never authorize fake model probabilities or automatic probability changes. Use the read-only audit_research_winner_card preflight; grade postmortems only against frozen pregame evidence and independent official finals.

## Pregame game-script analysis (all full-game winner selections)

Every NFL/NBA/NHL full-slate research winner should carry a timestamped scenario pair and explicit swing drivers:
- **NFL:** early lead versus trailing/game pace, QB pass volume and efficiency, protection and pressure, rushing balance, turnover/field-position variation, weather and late-game coaching decisions.
- **NBA:** fast/slow possession and pace environment, half-court efficiency, transition chances, three-point shooting variance, star availability and replacement rotations, foul trouble, back-to-back fatigue and late-game free throws.
- **NHL:** 5v5 shot/quality/possession control, starting goalie and rebound variance, penalties/power play, rest and travel, empty-net effects and full-game OT/shootout branches.

Record game_script.primary_win_path, game_script.opponent_win_path, game_script.swing_factors, and game_script.as_of_utc per researched event, supported by actually pre-selection-available sources. The primary and opponent scripts must describe **distinct plausible paths**, not just a home/away label swap. If incomplete, late or contradictory, the research-card preflight exposes quality warnings but preserves the researched side. Do not claim certified numeric scenario probabilities from the research narrative or treat an older v16/shadow script simulator as V17 certified. Postmortem compares predicted pregame script mechanisms with actual game evolution **without hindsight rewriting**, independently of win/loss grading.

## Media headlines, competing storylines and script robustness

For every user-requested NFL/NBA/NHL full-game winner, **actively retrieve current credible coverage** in addition to statistics: official league/team reports, verified beat reporting, independent local/national analysis and game previews. Capture a short attributed published headline with actual HTTPS source link, publisher, original publication time, earliest pre-selection user-available time and source receipt. Do not invent quotes, URLs, reporters or unavailable news. Cite sources in the user-visible matchup brief. Keep factual reporting (confirmed player availability and transactions) separate from pundit opinion, speculative narratives and market hype.

Summarize matchup storylines with source-linked claims: standings/season stakes, rivalry or revenge narrative, coaching tactical adjustments, injuries and returns, recent form versus underlying efficiency, media disagreement and expectations. Explicitly include the strongest *counter-narrative* explaining why the opponent could win. Avoid one-sided headline cherry-picking. Do not infer a narrative caused the result, or use a headline first published after the pick as if it were pregame evidence.

Extend each existing pregame game-script analysis with **at least three distinct qualitative scenarios**, including an expected winning path, a realistic opponent/upset path, and a meaningful personnel/pace/turnover/goalie stress path. For each record the favored side, mechanisms and swing factors, and an as-of timestamp. The read-only script audit reports how many documented unweighted scenarios favor each side; this is sensitivity *research*, not a probability distribution, validated game-script robustness measure or permission to manually adjust calibrated p. If sources are weak, research further and retain an explicitly provisional winner without calling it a certified fit. Never assign invented scenario likelihoods or treat the legacy/shadow v16 game-state simulator as certified V17 output.

Read-only fields: matchup_media.headlines (headline_id, publisher, title, url, published_at_utc, available_to_user_at_utc, source_receipt_id, stance); matchup_media.storylines (claim, stance, headline_ids); matchup_media.counter_narrative (claim, headline_ids); game_script.scenarios (scenario_id, path, favored_side, swing_factors, as_of_utc). The helper checks structural pregame provenance only; an external independent source readback is still required for verified authenticity.

## Market evidence typing
Every external market observation must be classified as one of:
- `EXACT_LINE`: same participant/event/stat/line/side identity as the candidate; may support exact-line no-vig/economics analysis.
- `ADJACENT_LINE`: related but different threshold/side/market identity; context only, never exact-line authority.
- `NO_MARKET`: no suitable current comparable market found.

Sportsbook implied probability, market consensus, external projections, rankings, records, hit rates, or narrative judgment are not governed model probability.

## Model interaction
- Where the certified specialist has a fitted input for refreshed evidence, ensure that input is supplied through the governed hydration/scoring path.
- Where no fitted coefficient/input exists, preserve the evidence as supporting/contradicting context only.
- Never manually apply a second numeric penalty after the backend has already incorporated the same evidence.
- Missing odds may block value/edge/economics publication but must not erase a completed sporting probability where the backend contract preserves it.
- Unsupported fitted routes remain fail closed even when market consensus strongly favors a side.

## Output contract
Return a compact context package containing:
- identity/status freshness
- material supporting evidence
- material contradictions
- evidence as-of timestamp(s)
- source/provenance summary
- market evidence type (`EXACT_LINE`, `ADJACENT_LINE`, `NO_MARKET`)
- exact-line market/no-vig context when available
- opener/current movement when available
- whether each material item is `MODEL_INPUT`, `EVIDENCE_ONLY`, or `ECONOMICS_ONLY`

The downstream ranked leaderboard still uses the governed fitted-model probability package and calibrated lower bound where required.

## Safety
- No probability fabrication.
- No market-probability substitution.
- No look-ahead leakage.
- No new model layer.
- No wager placement.
- `can_execute=false` always.
