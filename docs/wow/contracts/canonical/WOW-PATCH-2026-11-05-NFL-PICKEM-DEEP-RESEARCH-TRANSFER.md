# NFL Pick'em — LLP Deep-Research Transfer (2026-11-05)

Parent: #2076. Controlling engine: `LLP_TEAM_BETTING_ENGINE` / V17 NFL full-game team-event specialist. Accuracy & certification: #1530 and #1521; event evidence #1703; optional pool strategy #1708 and PR #1680. This is an **additive read-only Pick'em consumer**, not a new team predictor, fitted model, market model or pool strategy.

## Research findings mapped to the NFL winner workflow

| Factor | Upstream LLP fitted-model/evidence requirement | Downstream Pick'em use |
| --- | --- | --- |
| Process vs. outcome | Opponent-adjusted EPA, early-down success, CPOE, pressure quality, explosive creation/prevention, turnover-worthy plays and conversion-regression indicators; do not treat recent final scores as stable skill | Inspect documented divergence; request model recheck without turning recent form into an independent pick |
| Player value + replacement | QB and other personnel availability probabilities, estimated snaps, replacement quality, positional scarcity and opponent-specific tactical impact | Alert on questionable QB or material mismatch; maintain original model receipt until officially regenerated |
| Unit continuity + matchup | Offensive-line combinations, pass protection vs opposing pressure, coverage/passing interactions and other sample-shrunk unit effects | Surface changed combinations and missing evidence, not generic "X starters out" penalties |
| Neutral-state efficiency | Score/garbage-time adjusted efficiency, neutral-script pass/run mix and game-state-specific behavior | Diagnose misleading recent form and which observations belong to neutral game states |
| Schedule/travel | Recovery hours, travel miles and direction, time zones, cumulative congestion, Thursday short weeks and circadian starts | Flag dated schedule stress, rather than a static away-game penalty |
| Special teams / coaching | Field position, kick coverage, return efficiency, coach 4th-down choices, timeouts, two-minute and red-zone decisions | Review tight-game pathways; use stabilized estimates rather than narrative coaching labels |
| Upset variance | Empirical/simulated joint game distributions: possessions, turnover/explosive variance, tactical high-risk strategy, personnel mixture, environment; sport-learned weights | Display *certified* Upset Volatility Index only once produced, forward validated and independently governed; do not use two-sided probability gap as a substitute |
| Forecast fragility | Scenario-conditioned probabilities for uncertain QB/OL/weather states plus sensitivity across plausible inputs; mixture weights must be learned | Flag forecasts requiring fresh model scoring; no invented scenario weights or automatic probability change |
| Market disagreement | Timestamped, de-vigged comparable full-game price from same side/event/outcome, separate from model inputs | Missing-information diagnostic only; investigate material disagreement, do not blend market and modeled probabilities |
| Regime drift & calibration | Rosters/coaches/rules/league drift; out-of-time Brier/log loss, reliability bins, lower-bound coverage, conditional calibration | Review unusual risk states, publish sample sizes/coverage, and prohibit promoting unvalidated confidence bounds |

**Priority:** first fix immutable current-model coverage and lineup freshness, then opponent-adjusted process metrics, QB/replacement and OL interactions, then scenario-based probability distribution/variance and conditional calibration. Add travel, special teams, game state and market audits when genuine source receipts are available. Evidence collection must not block the full official weekly slate; show typed unavailable statuses.

## Implementation contained in this PR

The existing `v17/nfl_pickem_pool_optimizer.py` remains `MAX_EXPECTED_CORRECT`. Its existing `selection_volatility_band` is **only a two-sided probability-gap proxy**. This change explicitly publishes `selection_volatility_basis=PROBABILITY_GAP_PROXY_NOT_UPSET_VARIANCE` and a separate `deep_research_audit`. There is no numerically fabricated UVI, lineup mixture, forecast fragility or certified scientific evidence.

`audit_nfl_pickem_deep_research(row)` consumes an **optional** `pickem_deep_research_context`, attributed by exact official event ID, modeled source-snapshot ID, model artifact version and timezone-aware source/model/kickoff timestamps. Without those, it returns a typed missing/invalid status. Source evidence newer than the model but still before kickoff produces `MATERIAL_CONTEXT_NEWER_THAN_MODEL` and `model_recheck_required=true`. Post-kickoff evidence is rejected as an input to the pregame audit. Each documented factor must have an individual source receipt ID.

This validates **structure and temporal association only**; it does not independently authenticate source contents or certify a model. Supported diagnostic flags are allowlisted; unexpected narrative flags are ignored. Previously known premodel flags request **analyst review only**, not a redundant model rerun; only evidence newer than the model requires governed re-scoring. Neither those flags, market fields, nor roster notes can mutate `pool_pick`, calibrated probability/bounds, terminal state, tiebreaker, strategy, or `can_execute=false`. Blocked model rows remain blocked. Raw pregame context is not used to recalculate a probability.

### Follow-on upstream ownership (not falsely claimed by this PR)

1. The **existing** NFL specialist must add genuinely fitted and forward-tested expected-performance, personnel/replacement, unit, score-state and schedule-state features with snapshot receipts, sample-shrunk features and leakage-proof train/test partitions.
2. The **existing** fitted NFL specialist must produce coherent scenario-weighted game outcome distributions and uncertainty sensitivity, with properly estimated sport-specific UVI rather than assigning weights by hand. Correlated failure pathways must not be summed.
3. The **existing** model-calibration owner must verify interval/lower-bound coverage and probability reliability by favorite size, injury/QB uncertainty, lineup state, UVI cohort, season and market disagreement against frozen unseen games.
4. The existing scorer/capture lane must forward the optional provenance-bound context into the Pick'em row. **No producer is added in this PR**, so a live row without it correctly reports `CONTEXT_NOT_SUPPLIED`; the engine is not declared upgraded or certified.
5. SIRT (#1521) must independently confirm that each certified feature and distribution reflects frozen pre-kickoff data, that the full schedule is complete, and that the model passes untouched holdout comparisons before promotion. Failing or unavailable evidence remains research/HOLD.

## Evidence and release checks

- Frozen slate: exact NFL season/week, all Thursday/Sunday/Monday (and any exceptional) official event IDs, byes and kickoff times; full-game including overtime.
- No feature leakage: replay event, source observed timestamp, model timestamp, latest material update, outcome timestamp and provenance exact-match. Missing identity/source or newer personnel information => no invented probability.
- Shadow comparison: baseline champion vs new fitted feature/variance challenger on common pregame cohort, including expected correct, actual correct/total, Brier, log loss, probability bins, interval/lower-bound coverage, paired results and sample counts. No promotion from one week or a simulated pool equity estimate.
- Regression: absent/invalid/newer/post-kickoff research context, unreceipted factors, surprise flag injection, huge market disagreement, QB uncertainty, date/time collisions, full slate, source terminal and pick immutability, tiebreaker untouched.
- Protected exact-head tests + independent QA/release acceptance + merge + deployed SHA + real frozen pregame readback. CI or a merged PR alone is **not** production verification.
- Pool usage: GH's weekly sheet is not written; visible pool shares are not full opponent picks; unique tiebreaker requires checking the live sheet; pool equity remains a separate research-only challenger.
- Control plane: `V17_TERMINAL_REDUCER` remains the sole terminal authority; `can_execute=false`; no wagers or automatic pool submission.

## Delivery truth

**This PR implements only the downstream diagnostics and regression tests.** As of authoring, upstream research-context production, new fitted coefficients, a calibrated UVI, line-up mixture, statistical fragility and live deployment are **NOT VERIFIED/NOT DELIVERED**. They must be established by the existing LLP owners and original engineering/QA process, without creating a parallel betting or Pick'em model.
