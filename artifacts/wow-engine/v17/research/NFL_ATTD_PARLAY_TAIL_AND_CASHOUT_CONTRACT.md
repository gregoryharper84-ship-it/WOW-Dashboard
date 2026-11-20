# WOW V17 — NFL Anytime-TD Parlay Tail & Cash-Out Research Contract

Status: **RESEARCH / NOT FITTED / NOT CERTIFIED / NOT PRODUCTION**. Related issue #1813 and draft PR #1814. Owner: isolated P2 model research, separate from P0 cross-sport restoration. **can_execute=false**.

## Why this exists
An unverified seven-leg Fanatics screenshot displayed a $794 stake, +102139 combined odds, six green leg markers, one unresolved leg and $374,028.51 cash-out. The screenshot is illustrative, cannot authenticate the cash-out or leg statuses, and **is not an immutable WOW pregame prediction**. Do not train or calibrate a model from one viral winner. Its appeal is a test of sportsbook payout arithmetic, tail-event modeling, time-aware cash-out valuation, and risk discipline.

## Routing & ownership
- **Exact sporting selection:** NFL anytime touchdown scorer (ATTD) -> WOW_PROP_LANE -> **one independently fitted/certified NFL touchdown specialist per player/event**, using official identity and exact book settlement rules.
- **Sporting distribution:** prediction of at least one scored TD from fitted event/count model; model output must contain calibrated probability and governed lower bound (and calibration source, timestamp, failure paths) before ranking.
- **Downstream-only components:** exact sportsbook price, parlay pricing, edge/EV, leg/cross-slip dependence, cash-out quote, and card construction. They cannot create or modify governing sporting probability.
- **Final authority:** V17_TERMINAL_REDUCER only. Always dry-run, no wager placement/approval.
- Reuse existing slip/exposure guards and V17 postmortem. Keep the current production model unaffected.

## Data and identity requirements
Store original data provenance and UTC timestamps; use **official event ID, player ID, team and opponent at event time, market/stat identity, touchdown settlement inclusions, locked per-leg lines and exact offered book price, combined price, boosts/repricing, cash-out quote timestamp and expiry, settled/voided leg evidence, and source authentication status**. Unknown or covered screenshot prices remain null. Do not infer individual offered prices from aggregate payout.

Candidate independent pregame feature families for a fitted challenger: game and drive scoring opportunities, player routes/targets/carries by personnel and goal-line role, snap share, team red-zone possessions and play-calling, QB health, opposing touchdown prevention, blockers, projected pace, game script, injuries/workload/rest, stadium/weather. Use only as verified historical features available by original forecast timestamp; no future scores, closing prices after prediction, or outcome-derived leakage.

## Modeling research (Class C, behind P0)
1. Generate a chronological historical NFL ATTD training cohort with full covered board inventory, not only winners. Lock exact player/event/price and market settlement.
2. Fit a **player event/hazard or count-distribution** challenger that yields P(player scores >=1 TD); include a structurally modeled no-TD regime, playing-time/role uncertainty and calibrated failure paths. This is a hypothesis, not a declared certified model.
3. Calibrate on chronologically separated history with uncertainty-aware bounds. Check rare-event and longshot bins, bench/role shifts, injuries and different sports-books' eligible touchdown types. Report probability validity, Brier/log loss, reliability charts and cohort effective sample size; no universal haircut.
4. Fit joint dependence for multi-leg baskets using shared game scripts, team possession totals, injury assumptions and correlated within-game opportunity. Test cross-game dependence too. **Do not assume independent multiplication by default**. Preserve individual governed probabilities unchanged when calculating card dependence.
5. Evaluate *out-of-sample* expected value only against exact original market prices (including boosts, book fees and settlement rules). Check tails by odds bucket and leg count, report uncertainty and sample sizes. One million-dollar payout does not show long-run edge.
6. Cash-out analytic mode: if earlier legs are **authenticated settled WIN**, evaluate the *conditional joint chance of all remaining legs* using the controlling **pregame specialist only if the remaining events have not started**; otherwise a dedicated certified live model is mandatory. Compare to authenticated live offer only after refreshing payout, quote and event state.
7. Apply exposure/weakest-leg governance; do not add a weak or duplicate hinge to increase payout. Use the V17 terminal reducer and record immutable pregame predictions for retrospective evaluation.

## Price arithmetic and quote logic (Class B; not sporting model)
- American odds +a => decimal 1+a/100, -a => decimal 1+100/abs(a).
- At combined +102139 and $794: full gross return $811,777.66; price-implied ticket break-even = 1/1022.39, not model probability.
- Cash-out = $374,028.51 => net profit $373,234.51; ratio cash-out/full return = 0.4607524060. This ratio is the *hypothetical conditional threshold* when all other legs are won and exactly one remains. Never call it a modeled or verified hit probability.
- With any PENDING, LOSS, PUSH or VOID inconsistent with the standard all-or-nothing remaining-leg scenario, return typed evidence/repricing hold and avoid a cash-out recommendation.
- Source/ticket/quote evidence must be authenticated by a separate trusted upstream component; **a caller boolean or text receipt reference is not authentication**. In isolated research code, label attested receipts as assertions pending trusted upstream verification.
- Even with a certified probability, cash-out comparison is **advisory economics**, not trade execution.

## Required research acceptance
- Fixture arithmetic with screenshot numbers and seven exact leg statuses; partially obscured per-leg price -> PRICE_EVIDENCE_INCOMPLETE.
- Unverified screenshot/single boolean cannot unlock a verified conditional calculation or model qualification.
- Leg LOSS, PUSH, VOID and two pending -> no single-remaining-leg decision metric.
- Combo-per-leg price discrepancy flagged for source/boost/correlation audit; no fraud assertion or forced probability.
- Monotonicity and invariant tests for positive/negative American odds, decimal gross/net arithmetic, quote greater than possible return, nonfinite/negative money.
- Pregame started/live gate is hard: stale pregame package never supplies live probability.
- Fully chronological fit/replay, counterexamples and out-of-sample tail calibration before Class C adoption.
- Branch unit tests and authorized CI receipts **distinct from** Render deployment, production Action canary, live GPT editor sync and independent QA/Release.

## Completion definitions
- **Research built:** prototype source and isolated test fixtures in draft PR; this does not mean fit, certified, or deployed.
- **Model challenger ready:** fitted independent probability + immutable timestamp + calibrated lower bound + held-out rare-event validation + conditioned dependence evidence, never substituted odds.
- **Production usable:** authorized exact SHA deploy, authenticated end-to-end Action/canary, reducer publication, live editor parity and independent release evidence. Never bypass P0 restoration or approval boundaries.

Rollback: any evidence of odds interpreted as sporting probability, fabricated receipt authenticity, misattributed screenshot outcome, failed type/freshness/normalization checks, or changes leaking into existing production specialist/reducer; keep challenger research-only.
