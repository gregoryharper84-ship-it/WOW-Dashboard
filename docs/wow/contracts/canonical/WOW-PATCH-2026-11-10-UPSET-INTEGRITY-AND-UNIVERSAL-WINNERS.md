# WOW V17 — Upset Integrity V2 + Universal Winner Research

Status: **DRAFT_RESEARCH_AND_SOURCE_PATCH — NOT CERTIFIED / NOT DEPLOYED**
Date: 2026-11-10
Owner: LLP_TEAM_BETTING_ENGINE (team/event winner probability)
Terminal authority: V17_TERMINAL_REDUCER
Execution: can_execute=false
Canonical architecture: Project Hub #2076; reuse existing PR #1680 and source #1703 / research #1708, weekly scorecard #1530, QA #1521, postmortem #1852. This document creates no new governance authority.

## Precisely what this patch implements

1. Harden `v17/team_event_upset_alert.py`: reject NaN/Inf/boolean probabilities, reject non-normalized complete outcome spaces, and inspect *all* alternatives for interval overlap. Preserve already-governed sporting probability, original market-favorite identity, and the existing alert-only contract.
2. Add `v17/upset_market_diagnostics.py` as an **unwired, read-only research helper**: explicit power or multiplicative de-vig for complete two- or three-way outcomes; cross-book consensus/dispersion; exact market type/settlement basis; pregame timestamps and source snapshot IDs; staleness rejection; conservative disagreement; optional point-in-time reference-class bias decomposition.
3. Add regression tests for both paths. This does **not** train, score, calibrate, certify, publish, rank, enter or place any bet. New diagnostics do not grant model authority. The upstream source receipt supplied to this helper is contextual and does not prove independent certification.

## Upgrade plan for the fitted winner model (not implemented here)

For sport `s`, market `m`, event `e`, outcome `k` and frozen pregame information `I_t`:

```text
p*(Y=k) = C_(s,m){ Sum_z P(z | I_t) Sum_r w_(s,r)
                   Integral P(Y=k | X_t,z,theta_r) p(theta_r | D_<t) dtheta_r }
winner_max_correct = argmax_k p*(Y=k)
```

The fitted model must generate an *entire mutually exclusive outcome distribution*, including draws when settlement requires them, and map through a sport-appropriate scoring process. Scenario probabilities are pregame and uncertainty-aware. Weights and calibration are fitted on proper chronological training folds. The posterior predictive must include both location and scale/tails; an optional fitted market-logit shrinkage term is only a shadow challenger. Market quotes remain research inputs unless explicitly admitted as time-safe fitted model features through existing certification and Provenance/Probability Claim Audit.

Sport-specific processes to research:
- NFL/NCAAF: play/drive-level or margin and tie distributions; efficiency/QB replacement, weather, rest.
- NBA/NCAAB/WNBA: possession distribution, lineups, pace, three-point variance, late-game foul/fatigue.
- NHL: goalie-adjusted expected goals, Poisson/overdispersed goals, 60-minute regulation versus OT/SO settlement.
- MLB: pitcher/bullpen and run environment, inning/run distributions, lineup handedness and park/weather.
- ATP/WTA tennis: surface-adjusted serve/return point probability, Markov point→game→set→match, retirement handling.
- Soccer: joint score distribution/low-score dependence, home/draw/away, 90-minute versus qualification market labels.

No sports specialist receives FULL_MODEL or QUALIFIED status without a fitted artifact, independent validation, current inputs, valid calibration/lower bounds, and an immutable pregame model receipt.

## Upset Intelligence V2 acceptance requirements (10 areas)

1. **Predictive variance/tails:** fit both outcome location and volatility and evaluate favorite-failure paths; no unsupported universal variance multiplier.
2. **Market and favorite-longshot bias:** pre-register and compare power/multiplicative/Shin where valid; distinguish empirical bucket baseline correction from incremental matchup disagreement; missing reference class means bias-decomposition unavailable.
3. **Market timing and CLV:** immutable T-24h, T-6h, T-1h and close snapshots where available. Only compare like-for-like market types/settlement and independently verify the genuine close. CLV is an information/timing diagnostic, not a substitute for proper scoring or a claim of profitability.
4. **Robust intervals:** block/cluster by week, team, season or tournament as relevant; stacked/model-class uncertainty; quote dispersion; interval coverage and PIT checks for continuous outcomes. Binary predictive outcomes require appropriate calibration/coverage diagnostics, not naive PIT interpretation.
5. **Conditional calibration:** probability band × sport × market type × season phase, with partial pooling and sample-size safeguards; audit market anchoring and residual gains against a market-only model on unseen data.
6. **Label hygiene:** exact competition rules and 2-/3-way market, NFL ties and pushes, NHL regulation versus full game, soccer 90m versus qualification, tennis retirements/walkovers, postponements and forfeits. No hidden settling conventions.
7. **New signal families:** incentive/rest/opt-outs, replacement-level player deltas, travel/altitude/time zones, officiating, derivative markets, unexplained line movement, and priced/unpriced news timestamps. Ablate every family; narratives alone cannot create probability.
8. **Decisions:** independently report UPSET_WATCH, FAVORITE_REINFORCEMENT/FADE and selected maximum-correct winner. Separate NFL Pick'em pool-win-equity research; never make wager or staking decisions here.
9. **Backtest hygiene:** purged/rolling pregame training, preregistered feature ablations and held-out evaluation, source-availability receipts, reference-class empirical rates, baseline/entropy ceiling and sample-size/power analysis for each sport/market.
10. **Monitoring:** calibration and residual drift (e.g. CUSUM), staleness kill switches, adverse source/model changes, abstention from certified publication on unresolved blockers, independently checked postmortems.

## Winner-first evaluation protocol

Primary goal for winner card: maximize expected correct selections with argmax over independently calibrated outcome probabilities, conditional on the current actual slate. Grades use immutable pregame forecasts and independent official results. Track log loss, Brier/multiclass Brier, reliability, favorite/underdog strata, proper score improvement versus frozen benchmark, exact slate completeness, model availability, and uncertainty bound reliability.

No individual model feature, market model, ensemble weight, scenario engine or calibration method is *assumed* to improve precision. Promote a candidate only after genuine chronological out-of-sample tests and exact-head QA/Release acceptance. A single successful example, CI green, or market movement cannot certify a sport.

For NFL Pick'em, max-expected-correct is default. Pool win-equity research uses a separately validated objective with actual source-visible shares, unknown hidden opponents, tied-entry count and tiebreaker distribution, never post-lock inferred picks.

## Planned independent QA

- Run all touched unit tests, plus existing team/event and LLP integration suites against the exact commit SHA.
- Verify zero sporting-probability mutations, zero automatic pick promotion, `can_execute=false`, and untouched V17_TERMINAL_REDUCER.
- Verify malformed and stale evidence remains HELD/RESEARCH, no fallback inferred from odds or headlines.
- Capture immutable forward receipts, compare against official results and holdout reference classes before ever requesting certification.
- Protected merge, Render deploy and production readback require independent non-author approvals. A draft PR is not deployment proof.

## Known limitations

This source patch does NOT build the trained winner ensemble, game-script outcome distributions, reference-class learner, CLV history ingestion, sport-specific simulation engines, continuous drift controller, full Challenger tennis coverage or a production connected market-feed. Those need separate bounded implementation, existing issue ownership, training evidence and acceptance.
