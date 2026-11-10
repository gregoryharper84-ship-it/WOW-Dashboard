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

## Preferred winner, upset watch and frozen evidence

- Keep exactly one **preferred winner** per event in the provisional research card. An upset alternative is a distinct `UPSET_WATCH` research hypothesis; never silently reverse the preferred side or count both as preferred picks.
- A market favorite disagreement, external 51–49 projection, narrative upset case, or close final margin is **not** a fitted-model upset. Only an actual timely governed specialist can establish a qualified upset-side probability.
- Before favoring a contrarian replacement, demand a fresh independently verified event identity, source/as-of receipts, explicit contradictory evidence review, and a governed pregame model recheck. Without that, show the alternative as WATCH only and preserve the original preferred card.
- Snapshot the original selected side, `PREFERRED` or `UPSET_WATCH` role, sport, official event ID, kickoff, selection timestamp, **unique pregame receipt ID for each selection (including a distinct receipt for an upset-watch alternative on the same event)** and user-visibility timestamp **before kickoff**. Reused receipt IDs fail closed. Keep a separately frozen final-refreshed model package when available. Never reconstruct the prelock card from a post-lock sheet.
- When the user requests a retro, use `v17.llp_research_card_retro.audit_research_card` with independently verified official results and an official full-slate manifest; require complete reconciliation before reporting a headline winning percentage. The output is explicitly research-only; it adds zero fitted-model calibration samples.
- The fitted-model postmortem remains `v17.llp_postmortem_recalibration.build_llp_learning_report` using immutable governed probability receipts. Its optional `research_card_picks`/`research_card_finals` inputs are deliberately a separate, non-mutating report section.
- Report the primary and upset-watch exact win/loss grades side by side. Never award credit for a close loss, market movement, a postgame explanation, or an upside hypothesis that did not win.
- Keep GH's actually submitted Pick'em card, research-preferred card, upset-watch alternatives and fitted-model card as four distinct provenances; no transfer of credit across them.

## User-requested full-slate research winners — no discretionary pass

When GH explicitly requests a straight-up NFL/NBA/NHL winner for **every scheduled game**, independently reconcile the authoritative complete slate first, including unusual kickoff windows, postponed games and correct full-game OT/SO settlement. Deliver **exactly one selected winner per scheduled playable event** in an additional RESEARCH_PROVISIONAL user card when certified probabilities are missing. Do not substitute PASS, SKIP, NO_PICK or a preferred shortlist merely because the matchup is uncertain. Record a research winner even when the governed model route remains HELD or MODEL_UNAVAILABLE. This research selection is *not* fitted and cannot fill a governed model field, terminal, certification receipt, calibrated probability or lower bound.

For every selected game, present a compact **pregame game-script finding**: (a) the most plausible winning path for the selected team (pace/possession, tactics, matchup leverage and expected scoring conditions), (b) the strongest opponent/upset script (a genuine alternative trajectory, not simply reversed language), and (c) specific swing factors such as turnover/penalty changes, NBA shooting variance/rotation, NFL early-lead vs trailing play-calling, or NHL goalie/special-teams/OT. Explain which script assumptions make the chosen side preferable and which facts might overturn it. Reference pregame evidence and as-of timestamps. Use narrative scenarios only unless an independently certified fitted sport-specific model supplies scenario probabilities; never invent game-script weights or apply ad hoc numeric win-probability modifications.

For every difficult matchup, deepen independent research rather than stop: corroborate starters, injuries, expected replacement effects, matchup efficiencies, rest/travel, availability scenarios and timestamped opener/current market direction. Document contradictory evidence and the strongest counterargument. Seek two independent nonmarket publishers with as-of times; if sources are unavailable, disclose the deficiency. Use qualitative confidence only, no invented precise probabilities.

Before publishing, run read-only v17.llp_research_winner_card.audit_research_winner_card with an independently verified official manifest and one research selection for each event. Selection blockers prevent a complete-card claim; evidence warnings demand transparent uncertainty and more research but do not automatically change the winner to a pass. Missing/invalid/late picks remain incomplete: this validator never generates a selection. Keep the research user card, the governed certified leaderboard, the upset-watch alternatives, GH's submitted card and the official-result postmortem separated. No pick for genuinely cancelled/postponed events; no wagering or automatic submission.

## Required full-game matchup media + scenario brief

Alongside each researched winner (NFL/NBA/NHL), include: 1–3 current *linked, attributed, timestamped* real media headlines; key narrative/storyline and source; the competing viewpoint; expected win script; opponent upset script; material stress script; and concrete swing factors. Treat media sentiment as context, not a fitted variable, model probability or prediction proof. The user must still receive a preferred winner, even for divided or sparse reporting. Never substitute fictional coverage. Show source gaps as evidence warnings.

The unweighted research scenario summary may say "preferred side supported in more documented scripts" or "script-sensitive" but must never display numeric scenario *win probabilities* or claim measured robustness without authenticated fitted/validated conditional outcome distributions. Preserve a pregame snapshot; the retrospective later tests whether the predicted mechanisms actually occurred, without backfilling hindsight. Read-only v17.llp_matchup_media_script_audit is part of the winner-card validation; it never changes the official governed winner or terminal.

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