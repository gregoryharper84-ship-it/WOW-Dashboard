# WOW V17 — NBA prior-10 rolling-threshold joint-dependence sensitivity (research)

**Status:** `RESEARCH_HOLD_NOT_CERTIFIED` · `can_execute=false`
**Date:** October 27, 2026 (America/Chicago)
**Source:** SHA-pinned SportsDataverse/ESPN-derived 2026 NBA player-boxscore cache, `SPORTSDATAVERSE_ESPN_NBA_PLAYER_BOX_2026_7A4868DA`, SHA-256 `7a4868da67ae7f6ebfba23389e04220be2e3f69d2e56a3942c02aa56f1becc89`.
**Reproducer:** `nba_prior10_empirical_holdout_20261027.sql` (read-only SQL against existing `wow-engine-validation` schema).

## Correct claim boundaries

This is a historical **diagnostic**, not a certified NBA prop model, exact sportsbook board, live forward shadow or pregame Action receipt. It tests if research-only **same-player joint statistical exceedances** have forecastable dependence beyond the marginal-product baseline. The dataset was collected retrospectively; its source snapshot itself was **not frozen pregame**. No actual offered lines, calibrated probabilities, lower bounds, market payouts, costs, bankroll change, betting ROI, or execution are generated. The governance terminal authority remains `V17_TERMINAL_REDUCER`.

A hit here means points, rebounds or assists **strictly exceed that player's preceding ten played-game mean**, not an actual `7+`, `8+`, 24.5, or similar offer. Every rolling mean uses only ten earlier player games as ordered by source event time and event identity. Same-game results and any later results are excluded from that feature computation. For each cutoff, train the player-specific marginal/joint rates on prior flag rows only, require at least 20 preceding flag rows (which requires at least 30 underlying games), and freeze the estimate for subsequent games. The actual prior-ten feature thresholds continue updating based on past events during the test period; fitted player frequency parameters do not.

**Benchmark:** player-level marginal probabilities each have fixed Jeffreys half-count smoothing, `(wins+0.5)/(n+1)`. Independence comparator is `pA*pB`. Research joint candidate is `(n_AB+15*pA*pB)/(n+15)`, where fixed 15 is an arbitrary **research shrinkage parameter**, not a historically optimized or certified coefficient. Both can be misspecified. `gain=Brier(independent)-Brier(joint)` or `logloss(independent)-logloss(joint)`, so positive means the challenger did better on this *particular diagnostic*.

## Chronological sensitivity results

These are reproducible results of the read-only query, independently re-executed from the committed SQL file. Cutoff-specific holdouts **overlap**; they do not provide four independent replicates. Day-cluster standard errors are descriptive and do not account for all repeated-player or matchup dependence.

| Holdout begins | Pair | Rows | Players | Days | Brier gain | Day-cluster SE | Log-loss gain |
|---|---|---:|---:|---:|---:|---:|---:|
| 2026-02-01 | Points + rebounds | 7,390 | 304 | 65 | +0.001546 | 0.000607 | +0.006119 |
| 2026-02-01 | Points + assists | 7,390 | 304 | 65 | +0.001216 | 0.000510 | +0.004666 |
| 2026-02-01 | Rebounds + assists | 7,390 | 304 | 65 | +0.000075 | 0.000484 | +0.002306 |
| 2026-02-15 | Points + rebounds | 6,585 | 326 | 53 | +0.001317 | 0.000759 | +0.005502 |
| 2026-02-15 | Points + assists | 6,585 | 326 | 53 | +0.000788 | 0.000602 | +0.003479 |
| 2026-02-15 | Rebounds + assists | 6,585 | 326 | 53 | **−0.000431** | 0.000562 | **−0.000216** |
| 2026-03-01 | Points + rebounds | 5,464 | 342 | 42 | +0.002489 | 0.000983 | +0.009352 |
| 2026-03-01 | Points + assists | 5,464 | 342 | 42 | +0.001261 | 0.000719 | +0.005493 |
| 2026-03-01 | Rebounds + assists | 5,464 | 342 | 42 | +0.000283 | 0.000639 | +0.001653 |
| 2026-03-15 | Points + rebounds | 3,828 | 355 | 28 | +0.004094 | 0.001337 | +0.014218 |
| 2026-03-15 | Points + assists | 3,828 | 355 | 28 | +0.002167 | 0.000972 | +0.008637 |
| 2026-03-15 | Rebounds + assists | 3,828 | 355 | 28 | +0.001033 | 0.000885 | +0.005026 |

March 1 diagnostic: mean observed joint exceedances 26.592% P+R, 24.780% P+A, 23.280% R+A. Marginal-product forecasts were 21.281%, 20.331%, 19.921%; research joint forecasts were 25.217%, 22.630%, 22.722%, respectively. These are *historical research forecasts for rolling thresholds*, not probability packages for bookmaker offers.

## Interpretation

- **P+R** demonstrates the most consistent descriptive improvement at all four tested cutoffs, but this is not proof of reproducible prop-betting advantage.
- **P+A** shows smaller positive gains in the same overlapping evaluations.
- **R+A** is **inconsistent**: it loses to the independence benchmark for the February 15 cutoff on both Brier and log loss. No blanket thesis that every same-player stat pair benefits from correlation may be supported.
- The research model and fixed shrinkage parameter were devised during exploratory analysis. There is no preregistered, untouched next-season replication or independent model certification; these comparisons alone do not justify a claims of statistical significance.
- Player role, opponent, roster availability, injuries, minutes, game pace and common game script can explain much of the joint exceedance. A richer fitted specialist must be compared on genuinely frozen future evidence.
- The video's four-ticket AB/AC/BC/ABC structure remains an **independent** downstream portfolio/market question requiring exact offered multi-prop prices, settlement/push handling, and cross-ticket hinge risk.

## Remaining gated work

1. Under existing NBA exact-prop source work #1988 and immutable forward evidence #491, capture actual exact offer, player, game, stat, direction, model version, feature cutoff, model timestamp and the numeric **research candidate** distribution before game start; preserve typed `RESEARCH_UNCALIBRATED` status.
2. Settle offers via authoritative official player-event results without reconstructing historical forecasts or lines.
3. Evaluate on locked chronological forward data and check uncertain bounds, multi-cutoff/season drift, correlation after controlling minutes and matchups, and true P/R/A exact-line semantics.
4. Independently certify and calibrate any candidate **before** governed probability ranking; test four-ticket value separately using verified market prices and all portfolio safeguards.

**Final:** `probability_publishable=false`, `rank_eligible=false`, `model_promoted=false`, `can_execute=false`. No live betting or market orders.
