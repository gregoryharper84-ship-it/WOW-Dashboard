# WOW V17 — Upset Integrity V2 + Universal Winner Research

Status: **DRAFT_RESEARCH_AND_SOURCE_PATCH — NOT CERTIFIED / NOT DEPLOYED**
Date: 2026-11-10
Owner: LLP_TEAM_BETTING_ENGINE (team/event winner probability)
Terminal authority: V17_TERMINAL_REDUCER
Execution: can_execute=false
Canonical architecture: Project Hub #2076; reuse existing PR #1680 and source #1703 / research #1708, weekly scorecard #1530, QA #1521, postmortem #1852. This document creates no new governance authority.

## Precisely what this patch implements

1. Harden `v17/team_event_upset_alert.py`: reject NaN/Inf/boolean probabilities, reject non-normalized complete outcome spaces, and inspect *all* alternatives for interval overlap. Preserve already-governed sporting probability, original market-favorite identity, and the existing alert-only contract.
2. Add `v17/upset_market_diagnostics.py` as an **unwired, read-only research helper**: default pinned `power` de-vig (multiplicative available ONLY as labeled sensitivity comparison) for complete two- or three-way outcomes; cross-book consensus/dispersion; exact market type/settlement basis; pregame timestamps and source snapshot IDs; staleness rejection; conservative disagreement; point-in-time reference-class bias decomposition when a real-information edge is claimed; unavailable reference-class data leaves only descriptive market disagreement.
3. Add regression tests for both paths. This does **not** train, score, calibrate, certify, publish, rank, enter or place any bet. New diagnostics do not grant model authority. The upstream source receipt supplied to this helper is contextual and does not prove independent certification.

## Upgrade plan for the fitted winner model (not implemented here)

For sport `s`, market `m`, event `e`, outcome `k` and frozen pregame information `I_t`:

```text
p*(Y=k) = C_(s,m){ Sum_z P(z | I_t) Sum_r w_(s,r)
                   Integral P(Y=k | X_t,z,theta_r) p(theta_r | D_<t) dtheta_r }
winner_max_correct = argmax_{k in eligible_scoring_outcomes} p*(Y=k)
```

The fitted model must generate an *entire mutually exclusive outcome distribution*, including draws when settlement requires them, and map through a sport-appropriate scoring process.

**Three-way winner-selection contract (not a probability-model change):** the prediction outcome space and the contest's *scoring-eligible choices* must be separately typed. For a three-way HOME/DRAW/AWAY market, if the competition awards a point for predicting a draw, choose `argmax(P(HOME),P(DRAW),P(AWAY))`. If it requires choosing **one participant** and a draw scores zero for both, choose `argmax(P(HOME), P(AWAY))`; normalization conditional on no draw gives the SAME ranking but changes the reported probability's meaning, so do not mislabel `P(HOME)/(P(HOME)+P(AWAY))` as the unconditional win chance. If the contest grades eventual **advancement** (penalties/extra time), use the separately fitted advancement probabilities under that settlement definition, not the 90-minute HOME/DRAW/AWAY distribution. Never silently pool NHL regulation three-way, NHL full-game two-way, soccer 90-minute, soccer advancement, pushes, retirements or walkovers. Source-visible contest rules must bind the eligible scoring choices and grade labels.

For research-only full coverage, a provisional winner may be selected from external data with an explicit **PROVISIONAL** label; the official certified leaderboard must retain `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_OUTPUT_INVALID` and other fail-closed ceilings rather than inventing probabilities or admissions. Scenario probabilities are pregame and uncertainty-aware. **Sequential challenger protocol:** freeze the feature specification and sport/settlement label first. On chronological/purged training folds, fit base specialists and obtain genuine out-of-fold predictions. Fit ensemble weights using only those training-era out-of-fold predictions, then FREEZE weights. Fit the calibrator on a subsequent, disjoint chronological calibration window generated with those frozen weights. Score once on a later, untouched holdout unseen by either fitting stage. Repeat rolling-origin evaluation by refitting only from information available at each origin. No reuse of calibration outcomes for tuning weights or model selection. Audit explicit training/ensemble/calibration/holdout ID disjointness (reuse the basketball `disjoint_calibration_and_holdout` and `BASKETBALL_CALIBRATION_HOLDOUT_OVERLAP` precedent). Carry parameter, ensemble-weight, calibration and market-side uncertainty through cluster/block-resampling **the entire fitting stack**, not only base model parameters. Point-fitted calibration by itself does NOT justify narrow lower bounds.

**Multiclass calibration:** test simplex-constrained temperature or Dirichlet methods with proper multiclass log loss and reliability; independent per-class Platt+renormalize is an existing legacy comparator, not automatically a new validated gold standard. Explicitly verify probability normalization and ordering changes. Replacing any already-certified calibrator requires separate independently accepted model certification and cannot be smuggled through this research patch. The posterior predictive must include both location and scale/tails; an optional fitted market-logit shrinkage term is only a shadow challenger. Market quotes remain research inputs unless explicitly admitted as time-safe fitted model features through existing certification and Provenance/Probability Claim Audit.

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
2. **Market and favorite-longshot bias:** primary research reference method is **POWER** (`sum(raw_odds_implied_i ** exponent) = 1`); pin/version method and compare multiplicative only as a clearly labeled SENSITIVITY baseline, with Shin as a separate future research implementation. Do not treat the methods as interchangeable or silently change the benchmark. Proportional removal can systematically distort longshot prices (direction/effect varies with market odds). Distinguish empirical probability-bucket baseline correction from incremental matchup disagreement. **A verified point-in-time reference class is mandatory for any claimed information edge**; without it, show `MARKET_DISAGREEMENT_ONLY` and `INFORMATION_EDGE_UNPROVEN`. Even with a reference class, residual disagreement is only a candidate signal until forward validation.
3. **Market timing and CLV:** immutable T-24h, T-6h, T-1h and close snapshots where available. Only compare like-for-like market types/settlement and independently verify the genuine close. CLV is an information/timing diagnostic, not a substitute for proper scoring or a claim of profitability.
4. **Robust intervals:** block/cluster by week, team, season or tournament as relevant; bootstrap *all* base fitting, stacking weights and calibration rather than base-model parameters alone; quote dispersion; interval coverage and PIT checks for continuous outcomes. Binary predictive outcomes require appropriate calibration/coverage diagnostics, not naive PIT interpretation.
5. **Conditional calibration:** probability band × sport × market type × season phase, with partial pooling and sample-size safeguards; audit market anchoring and residual gains against a market-only model on unseen data.
6. **Label hygiene:** exact competition rules and 2-/3-way market, NFL ties and pushes, NHL regulation versus full game, soccer 90m versus qualification, tennis retirements/walkovers, postponements and forfeits. No hidden settling conventions.
7. **New signal families:** incentive/rest/opt-outs, replacement-level player deltas, travel/altitude/time zones, officiating, derivative markets, unexplained line movement, and priced/unpriced news timestamps. Ablate every family; narratives alone cannot create probability.
8. **Decisions:** independently report UPSET_WATCH, FAVORITE_REINFORCEMENT/FADE and selected maximum-correct winner. Separate NFL Pick'em pool-win-equity research; never make wager or staking decisions here.
9. **Backtest hygiene:** purged/rolling pregame training with disjoint ensemble-weight/calibration/test folds, preregistered feature ablations and held-out evaluation, source-availability receipts, reference-class empirical rates, baseline/entropy ceiling. Before promoting any uplift, publish *sport- and segment-specific* sample-size/power analysis and minimum detectable effect (MDE), including correlated-event design effects, multiple-comparison correction and uncertainty over training seasons. A 4,000–5,000-game illustration is not a universal proof threshold.
10. **Monitoring:** calibration and residual drift (e.g. CUSUM), staleness kill switches, adverse source/model changes, abstention from certified publication on unresolved blockers, independently checked postmortems.

## Winner-first evaluation protocol

Primary goal for winner card: maximize expected correct selections with argmax over independently calibrated outcome probabilities, conditional on the current actual slate. Grades use immutable pregame forecasts and independent official results. Track log loss, Brier/multiclass Brier, reliability, favorite/underdog strata, proper score improvement versus frozen benchmark, exact slate completeness, model availability, and uncertainty bound reliability.

No individual model feature, market model, ensemble weight, scenario engine or calibration method is *assumed* to improve precision. Promote a candidate only after genuine chronological out-of-sample tests and exact-head QA/Release acceptance. A single successful example, CI green, or market movement cannot certify a sport.

For NFL Pick'em, `MAX_EXPECTED_CORRECT` is the **already implemented** default in `v17/nfl_pickem_pool_optimizer.py` / `v17/nfl_pickem_pool_win_equity_shadow.py`. `POOL_WIN_EQUITY` remains shadow research, **not a production-approved strategy** (`PICKEM_STRATEGY_MODE_UNSUPPORTED`); do not rebuild a rival pool optimizer. Pool research uses the existing paired replay and a separately validated objective with actual source-visible shares, unknown hidden opponents, tied-entry count and tiebreaker distribution, never post-lock inferred picks.

**Naming contract:** `team_event_upset_alert.probability_gap` is **model favorite minus strongest model alternative**, NOT a model-market edge. The research helper's `market_disagreement` is governed model outcome probability minus independently de-vigged market outcome probability, NOT a proven informational edge. A future independently validated signal, if admitted, must receive its own versioned `UPSET_EDGE_VS_MARKET` reason code and evidence status; it must never be written into `probability_gap` or change a model probability.

**Review fixture proof:** commit `f2527b0ef2f2e352c641c85019ed5a3da8f89c1b` changes the multiclass overlap fixture from HOME .46/.40/.52, DRAW .40/.31/.39, AWAY .14/.04/.43 to HOME .50/.46/.56, DRAW .40/.31/.43, AWAY .10/.04/.47; expected candidate remains AWAY, expected alert remains `UPSET_ALERT_UNCERTAINTY_OVERLAP`, and expected probability gap updates from .32 to .40. This restores the intended **valid input** (all point estimates inside intervals, normalized probabilities) while retaining the substantive regression assertion; not a bypass.

## Planned independent QA

- Run all touched unit tests, plus existing team/event and LLP integration suites against the exact commit SHA.
- Verify zero sporting-probability mutations, zero automatic pick promotion, `can_execute=false`, and untouched V17_TERMINAL_REDUCER.
- Verify malformed and stale evidence remains HELD/RESEARCH, no fallback inferred from odds or headlines.
- Capture immutable forward receipts, compare against official results and holdout reference classes before ever requesting certification.
- Protected merge, Render deploy and production readback require independent non-author approvals. A draft PR is not deployment proof.

## Known limitations

This source patch does NOT build the trained winner ensemble, game-script outcome distributions, reference-class learner, CLV history ingestion, sport-specific simulation engines, continuous drift controller, full Challenger tennis coverage or a production connected market-feed. Those need separate bounded implementation, existing issue ownership, training evidence and acceptance.
