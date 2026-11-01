# WOW V17 Decision Intelligence — Product Acceptance & Work Reconciliation

Date: 2026-10-08
Status: PROPOSED; no production changes, no wagering execution
Product owner: WOW Betting Intelligence
Engineering implementation owner: WOW Engineering
Independent reliability owner: SIRT
Certification: Independent Verification + governed V17 release process

## User-facing promise

Answer **three distinct questions**, independently and with honest unknowns:
1. How strongly does the certified model support the exact outcome? (sporting-probability ranking, descending calibrated lower bound)
2. Does a fresh exact market price offer theoretical value under clearly labeled assumptions? (market research, not model approval)
3. What concentration, duplication and correlation risks exist across proposed cards? (portfolio research, not an allocation instruction)

If any layer is unavailable, show the valid outputs from independent layers and the precise blocker. Never invent a substitute.

## Non-negotiable presentation

- Probability and market-value leaderboards remain separate; market edge never promotes V17 rank eligibility.
- Value is clearly labeled hypothetical research, with quote timestamp/source, exact line and settlement, raw break-even probability, calibrated point and lower bound, conservative gap, payout assumptions and uncertainty.
- A high-probability negative-EV candidate is visibly negative value, not automatically a valuable opportunity.
- Positive EV at a point estimate with non-positive conservative gap is visibly uncertain.
- A request for N legs/slips may result in fewer or none; never force fillers.
- PrizePicks offers must preserve direction, Power/Flex/promo terms, payout ladders and dependence. No pseudo per-leg odds.
- Do not state a bankroll size, Kelly stake, allocation amount or expected daily profit from these research-only services.
- can_execute=false; V17_TERMINAL_REDUCER remains sole global terminal/publication authority.

## Given / when / then acceptance scenarios

| ID | Scenario | Acceptance |
|---|---|---|
| P-01 | Valid certified prediction, quote missing | Keep sporting forecast/rank eligibility intact; show market unavailable |
| P-02 | 70% hypothetical side offered at -300 | Display negative theoretical unit EV; don't conflate likely outcome and valuable price |
| P-03 | 54% hypothetical side offered at +110 | Show positive point unit EV but clearly separate confidence/uncertainty and conservative LB result |
| P-04 | Uncertified specialist, strongly favorable price | No governed market-value publication and no price-derived substitute |
| P-05 | Correct player and opponent but wrong 4.5 vs 5.5 prop line | Do not compare price or grade as same immutable prediction |
| P-06 | Price stale, suspended, in-play or event started | No fresh pregame executable-price conclusion |
| P-07 | Opposing prices from different books | Never synthesize same-book no-vig probability |
| P-08 | PrizePicks multi-leg payout rule or joint distribution absent | No overall monetary EV; leg-level sporting forecasts preserved when valid |
| P-09 | Repeated event thesis across three candidate cards | Count one model observation, three exposures; keep sporting probabilities unchanged |
| P-10 | Shared-player/game-script unknown dependence | Surface unknown risk; no independence assumption or automatic stake |
| P-11 | Single loss on previously valid 70% prediction | Grade realized result separately; do not automatically patch probability |
| P-12 | Postgame statistic on a different line or direction | Never credit historical model with an unissued pregame thesis |
| P-13 | Model valid but separate market-analysis service fails | Probability leaderboards function; typed market failure surfaced; no silent candidate loss |
| P-14 | Reviewer checks a merged/deployed build | Verify original incident replay, CI, exact deployed SHA, independent QA and rollback proof |
| P-15 | User asks to place/order/approve a bet | No execution action; can_execute=false unchanged |
| P-16 | User asks top value across a full board | Every discovered candidate scored, blocked or purged exactly once; no cherry-picked subset |

All numeric examples are hypothetical fixtures, not certified model outputs or profit evidence. Production pass/fail requires executable regression assertions and independent receipts, not a checkbox marked manually.

## Metrics and release evidence

Report separately: eligible sporting candidate coverage; market exact-match/freshness coverage; analysis completion/failure counts; payout/joint-model support rate; thesis dedup and shared-exposure counts; calibrated Brier/log loss by adequate cohorts; CLV and theoretical-versus-realized value where exact records permit; user-request completeness; first-pass success; revision-linked verification status.

Never equate short-term realized winnings with model quality, or CLV alone with profitable edge. Any proposed automatic calibration, model-weight, confidence-tier or qualification change is Class C: hypothesis -> point-in-time replay -> counterexample review -> untouched holdout -> forward validation -> independent review -> governed promotion.

## Reconciled backlog (avoid duplicate features)

| Work package | Existing issue / dependency | Implementation posture |
|---|---|---|
| Market-price exact identity, de-vig and gaps | #874 and existing v17_moneyline_market_value_semantics.md | Research-only Class B; no qualification gate edits |
| Downstream economic-capacity/EV diagnostics | #1227; depends on STANDARD_OPERATOR | Deferred / Class B shadow after activation |
| Duplicate thesis and weakest-leg structure | #974 | Class B card diagnostics; unchanged sporting p/LB |
| Covariance and joint portfolio risk | #1221 | Deferred; numerical dependence estimator is Class C |
| Calibration, CLV, outcome-grade dashboard | #1252 and #1221 | Deferred where governing gate requires; observer-only first |
| SIRT independent assurance | This design's sirt-assurance.md; tracked by initiative/SIRT review issue | No self-certification |
| Production rollout | Future bounded PRs after owner-specific gates | No bulk merge, no unattended deploy |

## Completion levels

- SPECIFIED: architecture + SIRT assurance + acceptance scenarios published as draft review material.
- BACKLOG_RECONCILED: parent initiative cross-links existing issues and new assurance work, with named owners and priority.
- IMPLEMENTED_IN_SHADOW: bounded code, tests, immutable receipts on isolated shadow data; no production publication behavior changed.
- VERIFIED_IN_PRODUCTION: approved per-lane release after exact-head CI, independent QA, live revision/source checks, original-scenario replay, rollback test, monitoring and authorization.
- Never call this initiative complete solely because documentation or an issue/PR was created.

## Immediate activation trigger

When independent lifecycle evidence confirms the exact STANDARD_OPERATOR condition stated in #1221/#1227, Product and SIRT may re-triage those deferred issues. Until then, document/design review is permitted but priority remains existing restoration and Golden Path closure.