# WOW V17 — NBA Three-Thesis Portfolio Coverage (Research Intelligence)

status=RESEARCH_INTEREST_NOT_CERTIFIED
date=2026-10-27
provenance=user-supplied_video_transcript_screenshots
source_coverage=10_readable_of_18_screenshots;visible_00:00-06:23_and_07:59-08:42
source_gap=06:24-07:58_and_after_08:42
route=V17_DOWNSTREAM_PORTFOLIO_RESEARCH
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false

## Finding

A video creator claims $119,000 profit over two NBA seasons and proposes grouping six exact player props into **three player-level joint theses from three separate games**. Each thesis has two same-player props. Construct four tickets: AB, AC, BC (four legs each), and ABC (six legs); stake 0.25 unit on each for 1.00 total unit exposure. The screenshots claim a net 1.5-unit profit when exactly two player-theses hit, and suggest scaling monthly bankroll units. None of these profit or payout claims is independently audited by the supplied excerpts.

This is potentially useful **portfolio-structure intelligence**, not a fitted model, independent probability, evidence of positive EV, approved card, or staking instruction.

## Exact mathematical identity (research, no simulated prices)

Define A = A1∩A2, B = B1∩B2, C = C1∩C2. Let d_AB, d_AC, d_BC and d_ABC be **actual verified gross decimal payouts** (not assumed product odds). With 0.25 units on each ticket:

`net_units=0.25*(d_AB*1[AB]+d_AC*1[AC]+d_BC*1[BC]+d_ABC*1[ABC])-1`

`expected_net_units=0.25*(d_AB*P(AB)+d_AC*P(AC)+d_BC*P(BC)+d_ABC*P(ABC))-1`

For exactly A+B winning, net is `0.25*d_AB-1`. Merely hitting two theses **does not guarantee profit**; a winning pair ticket requires gross decimal payout above 4.0 to yield a positive net result. The transcript's asserted +1.5 net units implies decimal payout 10.0 for the relevant four-leg ticket, not proven for other pairs or nights.

No joint probability may be inferred from marginal probabilities without a certified dependence model, and no headlined payout establishes edge.

## Hypotheses

- **H1 Player joint-thesis model:** Fit/validate `P(A1∩A2)` by NBA role, minutes, usage, fatigue, opponent, game script and uncertainty; compare against naive multiplication on a genuinely held-out sample. Preserve full probability/calibration/lower-bound provenance.
- **H2 Cross-game stress reduction:** Test whether three independently selected events reduce portfolio tail risk relative to equivalent-stake structures using six players or same-game concentration. Do not assume independent games eliminate shared shocks.
- **H3 Four-ticket coverage:** Replay AB/AC/BC/ABC against singles, smaller cards, and other combinations with identical candidate information, actual posted prices, identical risk budget and exact settlement rules. Report EV, variance, tail losses, max drawdown, exposure, and risk of ruin. Payout packaging alone cannot convert negative-EV legs into positive EV.
- **H4 Fractional sizing:** Test constant nominal, fixed-fraction, and drawdown-limited budgets with realistic variable returns. The source's hypothetical '10 units/month' is not an observed guarantee or valid forecast.

## Risks that must be enforced

1. **Correlated same-player failure:** Two props may both fail due to injury, foul trouble, minutes, blowout or role. Three theses are not automatically 'three safe reads.'
2. **Repeated hinge:** A appears in AB, AC, ABC; failure of A eliminates three tickets. Similar for B/C. Across-ticket exposures **compound**; don't call the four tickets diversified independent positions.
3. **Exact offer and correlation pricing:** Verify whether each multi-prop ticket is allowed; obtain bookmaker's exact combined payout, time, fees, DNP and push/void rules. Never synthesize a same-game price by multiplying naive single-leg odds.
4. **Sporting vs portfolio:** Underlying `model_probability`, `calibrated_probability` and `calibrated_lower_bound` must stay unchanged merely because an underlying thesis appears on multiple tickets.
5. **Anti-hindsight:** Grade only immutable pregame records at same player, event, period, stat, exact threshold and direction. Treat the video creator's profit claim as unverified until a transaction-level ledger, prices, losses and sample size exist.

## Fit with repository's current V17 code

The existing `artifacts/wow-engine/v17/portfolio_exposure_gate.py` already detects:
- `DEPENDENCE_UNQUANTIFIED_SAME_EVENT`
- `SESSION_DIRECTIONAL_EXPOSURE`
- `EXACT_THESIS_DUPLICATE_SESSION_EXPOSURE`
- `DUPLICATE_THESIS_COMMON_HINGE`
- `COMPONENT_COMPOSITE_OVERLAP`

It expressly does not modify sporting probabilities. The current `WOW_V17_PICK_CORE_SKILL.md` requires the current same-event dependence gate, exposure audit and weakest-leg elimination. Do **not** relax these checks to accept the strategy. Any new research adapter should be additive and downstream.

## Suggested minimally invasive research artifact

Create an **optional, non-publishing card-structure evaluator** that accepts only valid governed sporting packages and returns:
- canonical `joint_player_thesis_id`, exactly two component prop keys, event ID and model receipt IDs;
- certified pair/triple joint probabilities **or** explicit `JOINT_DEPENDENCE_UNRESOLVED`;
- the four exact offered ticket payouts and timestamp or explicit market-unavailable status;
- ticket-by-ticket and across-ticket common-hinge graph;
- candidate total-risk budget, verified realized-payoff table and risk summary;
- a comparison with existing card optimizer under equal total exposure;
- evidence+provenance, fail-closed typed statuses, no betting/execution.

No model coefficient, qualification gate or staking rule changes are proposed in this research-only PR.

## Validation / promotion requirements

1. Independent timestamped screenshot/source reconciliation for all 18 images; preserve caption transcription uncertainty and omitted words.
2. Full matched pregame candidate universe and immutable exact-line prediction IDs.
3. Proper NBA joint-scoring/calibration and lower-bound package, with holdout and counterexamples.
4. All four exact market payouts and permitted-leg/settlement contract.
5. Replays against baseline with equal risk and no cherry-picked winner-only dataset.
6. Cross-slip exposure / duplicate-thesis / game-script stress tests and a no-filler rule.
7. No loss of current V17 regression coverage or runtime controls; terminal reducer sole global authority.
8. Independent authorized adoption before treating a research hypothesis as production behavior.

## Current disposition

`RESEARCH_INTEREST`: worthy as a **challenger hypothesis** for NBA correlated-prop modeling and portfolio coverage, **not** an approved/proven betting strategy. This document does not assert the second eight images have been transcribed, nor does it claim model scoring, deployment, or live production validation.

`V17_TERMINAL_REDUCER` remains the sole terminal authority. `can_execute=false`.
