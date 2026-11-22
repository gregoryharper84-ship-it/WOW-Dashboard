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

## Upset Integrity V2 — optional research diagnostics (non-authoritative)

When source-visible exact-line market snapshots are available, the analyst may
run `v17.upset_market_diagnostics.analyze_upset_market_snapshot` alongside the
existing governed `team_event_upset_alert` interpretation layer. Require matching
event/outcome space, exact market type/settlement basis, source snapshot IDs,
timely pregame book quotes, pinned power primary no-vig method (multiplicative sensitivity-only) and
complete governed outcome probabilities and bounds. Treat cross-book dispersion
and reference-class favorite–longshot bias as uncertainty, not model evidence.
A reference-class decomposition requires a separate time-safe receipt.
Never label `market_disagreement` as proven upset edge. Display descriptive
`MODEL_MARKET_DISAGREEMENT_UNVALIDATED` until the independently evaluated
reference-class and forward evidence justify a distinct `UPSET_EDGE_VS_MARKET`
claim. The pre-existing alert's `probability_gap` is **model favorite minus
strongest model alternative**, not model-versus-market edge.

All outputs from that helper are `RESEARCH_ONLY` (or explicitly single-book):
not certified, not rank eligible, not allowed to update sporting probabilities,
not permission for pick promotion or a cash/profitability decision. A positive
market disagreement is not proof of information advantage. No stale,
after-kickoff or different-settlement quote may contribute to an edge claim.
The existing alert governs favorite vulnerability from a complete model package.
The diagnostic measures *separate* market disagreement.

Do not confuse **within-event winner selection** with **cross-event confidence
ranking**. If the objective is maximum expected correct selections, the
mathematical within-event decision is the highest calibrated probability among
the contest's explicitly scoring-eligible outcomes, subject to the controlling
Event Decision Governor and all stricter model/terminal gates. The existing
cross-event leaderboard can rank accepted winner selections by calibrated
lower bound as a separate reliability ordering. For a three-way soccer game,
choose HOME/DRAW/AWAY only when the contest allows a draw selection. If it
requires a participant, with a draw scoring as incorrect for either team,
choose the higher of **unconditional** P(HOME), P(AWAY); conditional-on-no-draw
normalization does not change the ranking and must never be misreported as
unconditional winner probability. If picking who qualifies after extra time,
use a separately fitted advancement market rather than 90-minute outcomes. Never override a stricter
governed decision with research-only argmax or a market-derived probability.

For requested tennis winner research, include ATP/WTA scheduled matches in the
research slate when identity and source coverage permit, but route through a
fitted certified tennis specialist only when genuinely available. Never
manufacture FULL_MODEL support from rankings, Elo alone, or market prices.
Challenger tennis is independently source/capability-qualified.

The proposed universal winner posterior-predictive ensemble, variance/tail
modeling, reference-class learning, sequential market snapshots, CLV,
calibration and prospective testing are **research designs, not implemented
fitted capabilities**. See:
`docs/wow/contracts/canonical/WOW-PATCH-2026-11-10-UPSET-INTEGRITY-AND-UNIVERSAL-WINNERS.md`.

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