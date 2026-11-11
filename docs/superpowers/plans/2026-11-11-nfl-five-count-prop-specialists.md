# NFL Five Missing Count-Prop Specialists Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development, with RED → GREEN per producing-layer change.

**Goal:** Restore five currently unbuilt NFL count-stat prop specialties without bypassing the V17 lifecycle.

**Architecture:** The new offline, non-serving candidate builder fits exactly one independent Poisson count model for each of COMPLETIONS, PASSING_TOUCHDOWNS, PASS_ATTEMPTS, RECEPTIONS and RUSH_ATTEMPTS from frozen nflverse player-weekly source files. The first stage records hashes, independent 2024 calibration, untouched 2025 holdout and fail-closed errors. Candidate output never registers into production, becomes rank eligible, or changes the terminal reducer.

**Tech Stack:** Python, pandas, numpy, scipy, sklearn, pytest.

**Spec:** WOW V17 Player-Prop Qualification Blockers — Engineering Recovery Handoff, dated 2026-11-11 (user-provided handoff).

## Global constraints

- custom_gpt_identity=WOW_BETTING_ENGINE
- runtime_generation=V17_ACTIVE
- terminal_authority=V17_TERMINAL_REDUCER
- can_execute=false
- DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
- Never fit from odds, heuristics, research narratives or an LLM probability.
- No probability, calibration, catalog, threshold, certification, display or protected-release modifications in the candidate stage.
- Preserve every original NFL certified direct-prop lane.

## Review focus

1. Missing/NaN/fractional counts → typed source rejection, never fabricated zero.
2. Repeated player/game identity → reject, never double-count target or history.
3. Later game outcomes entering a pregame feature → forbid; only earlier games feed L10/L5.
4. Missing 2024 or 2025 cohort → no calibration/holdout claim.
5. Apparently successful candidate → remains non-publishable and unregistered.

## Task 1: Candidate acquisition and feature contract — implemented on this branch

**Files:** scripts/train_nfl_missing_prop_candidates.py; tests/test_nfl_missing_prop_candidates.py.

- [x] RED: 10 initial tests failed on unimplemented scaffolds.
- [x] GREEN: strictly validated source/identity/count, role- and stat-specific leakage-safe features, independent five-route fits, candidate-only artifact.
- [x] Local targeted tests: 15 passed; compileall passed.
- [x] Offline synthetic CLI: all five candidate artifacts emitted, registration_attempted=false, all probability_publishable=false.
- [ ] Hosted CI against exact GitHub head and independent code review.

## Task 2: Genuine source and independent model selection — not done

- [ ] Retrieve authorized nflverse 2021–2025 frozen source bytes and verify license/reproducible hashes; do not commit protected raw datasets.
- [ ] Execute 5 independent candidate fits, report train/cal/holdout counts and OOD without using the holdout for model selection.
- [ ] Predeclare held-out exact-line probability metrics, conditional calibration, empirical count-tail/overdispersion and role-regime tests. Compare Poisson, negative-binomial and other genuinely fitted specialists as appropriate. The initial Poisson candidate is not presumptively certifiable.
- [ ] Run independent source audit, graded forward replay and leakage-proof feature hydration.
- [ ] Keep any model failing a quality/rights gate unavailable or held; never promote by changing a status flag.

## Task 3: Governed per-stat production integration — not done

- [ ] Register each independently approved model artifact, calibrator/bounds and exact controlling specialist in the existing governed path.
- [ ] Verify stat synonyms, correct event/player identity, role validity, start time, literal line, MORE/LESS distributions and push/void settlement.
- [ ] Unit/integration/replay, exact-head CI, independent non-author QA/SIRT/Release acceptance, then governed production deployment.
- [ ] Verify live Render SHA, real /score-pick-request terminal receipts, display authorization, no regressions in four existing NFL lanes, and strict can_execute=false.
- [ ] Record scoreboard delta and V17 memory ledger; never mark fixed without real production receipts.

## Acceptance and current disposition

**CANDIDATE_BUILDER_IMPLEMENTED; PRODUCTION_NOT_FIXED.** 0 of 5 new lanes certified by this branch, no runtime registry change, no deploy. No numeric fitted probability from this candidate builder is currently authorized for publication.
