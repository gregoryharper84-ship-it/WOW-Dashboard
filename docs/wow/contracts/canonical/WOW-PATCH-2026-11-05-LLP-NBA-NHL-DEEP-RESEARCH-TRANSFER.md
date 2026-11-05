# 2026-11-05 — Deep-research transfer: NBA + NHL daily winner intelligence

**Scope:** existing LLP team-event intelligence downstream consumer. Governing project #2076; upstream fitted NBA owner #1950 and NHL owner #1952; capability #2020/#1562; independent QA/SIRT assurance. NFL-only Pick'em overlay is separate PR #2471; this patch must **not** redefine the NFL selection engine, tiebreaker or Thursday scheduler.

## Why this patch

The LLP research audit established that "injuries / rest / form" categories are inadequate unless represented causally, source-timestamped and tested out of time. The next goal is **a calibrated distribution of possible full games**, not simply an average expected winner. The same principle applies to the NBA and NHL, with distinct sport-specific mechanisms.

The **immediate code change** is deliberately narrower than fitting those models: source-pinned, optional, read-only research metadata on the existing cross-market event bundle and Daily run readback. Evidence cannot change a winner, probability, lower bound, terminal, or qualifying market. Missing research output remains explicitly missing, numerical upset-volatility/fragility are `null`, and the audit cannot certify itself. To obtain actual learned improvement, the existing sport owners must implement and forward-validate the listed upstream models.

## NBA critical model/data factors — #1950

1. **Underlying performance:** opponent-adjusted expected shot quality / points per possession vs made-shot luck, neutral-score-state offensive and defensive efficiencies, and garbage-time exclusions.
2. **Projected players:** probability of playing, estimated minutes, player-value-weighted replacement quality, scarcity, role elasticity when ballhandlers/rim protectors are out, multi-player interactions.
3. **Lineup continuity:** expected five-player combinations, minutes together, lineup-sample regularization, bench-unit composition and coach substitution patterns.
4. **Possession distribution:** pace, rim/three-point shot mix, three-point variance, offensive rebounding and turnover rates, foul generation and matchups. A point-estimate favorite does not independently define upset risk.
5. **Schedule-stress state:** B2B, 3-in-4, core-player minutes, overtime carryover, travel distance, time zones/direction, altitude, circadian start and trip sequence.
6. **Context:** opponent tactical fit, validated officiating tendency only if sample-stabilized, playoff/standings incentives conveyed through projected minutes/lineups, regime shifts after trades/coach changes.
7. **Governance:** scenario-weighted injury/minutes mixtures learned from actual data, forward-time holdout of full-game-including-OT win probabilities, calibration by lineup certainty, travel, favorite size, shot-profile volatility and market disagreement.

## NHL critical model/data factors — #1952

1. **Five-on-five process:** opponent- and score-adjusted expected goals, high-danger chances, shot quality and prevention vs unsustainable shooting/save percentages; pulled-goalie/empty-net effects separated.
2. **Goalie scenarios:** official/probable starter timestamp, true start probability, stabilized shot-stopping vs replacement, workload and backup roster effects; mixture weights must be learned, never guessed.
3. **Unit and special-teams state:** forward line and defensive-pair continuity, opponent-specific line matchups, PP vs PK, penalties drawn/taken, special-teams availability and shrinkage for small samples.
4. **Outcome variance:** low-goal sample size, conversion variance, goalie variance, penalty/event volatility and plausible underdog tactics; calibrate a real model-produced UVI and fragility, not a probability-gap proxy.
5. **Schedule and incentives:** fatigue, back-to-back road travel, direction/circadian mismatch, goalie rest and lineup management, coaching response to score state, playoff advancement context.
6. **Governance:** fit and validate **full-game INCLUDING overtime/shootout** probabilities and settlement semantics, never a regulation-only 3-way probability or market odds disguised as NHL model.

## Common research+governance rules

- Independent market consensus is a **diagnostic** for missing/stale information only. Keep it outside core fitted sporting probability and never transfer moneyline probability to spread/game totals.
- Dynamic calibration audits: point `p`, calibrated interval/lower-bound empirical coverage, Brier/log loss, ECE/reliability, injury/lineup certainty, UVI, season drift, model-market disagreement, low sample cohorts and confidence intervals.
- Use immutable official event IDs, sport, start time, model artifact/version, certified sport-specific outcome, source snapshot and receipt IDs, UTC-aware observed/model/source timestamps. New *material pre-start* information after the frozen model requires fresh specialist scoring and a new immutable receipt. No post-start re-scoring as pregame.
- Preserve complete daily NBA/NHL slates including games without model receipts. `PROVISIONAL_NO_CERTIFIED_PROBABILITY`/typed HOLD is acceptable; a false Full Model badge is not.
- Freeze pregame predictions, verify independent official final including NBA OT and NHL OT/SO, grade accuracy/coverage and calibration separately from NFL and between NBA and NHL. Compare champion vs research challenger on same untouched times/fixtures with paired metrics. Never claim fitted advantage from one day's games or simulated pool equity.
- No paid data providers required by default; prefer reproducible free licensed/official sources and explicit coverage gaps.
- Only existing LLP controlling specialist may emit certified numerical probability. Terminal authority `V17_TERMINAL_REDUCER`, `can_execute=false`, no wagers and no automatic pool entry.

## Implementation and current limitations

This isolated PR adds `llp_nba_nhl_deep_research_audit.py` and optional `llp_deep_research_context` pass-through from the existing saved Daily model-result detail into `build_llp_team_intelligence_bundle`. A context must match exact NBA/NHL event and model receipt/snapshot, model version, and valid pregame observed timing; factors count as documented only with individual source receipt/time. Known pre-model flags call for analyst review; newer material information calls for model rerun. No model weights, probabilities or grades are learned or changed.

**Not supplied in this PR:** upstream feature producers; confirmed player/goalie source ingestion, scenario simulation, genuinely calibrated UVI, fitted fragility, model registration, current-season NBA/NHL certification, cloud deployment, Action production readback, independent QA approval. Until those are real, status remains `CONTEXT_NOT_SUPPLIED` or a held/uncertified context. Do not use this source PR to claim a newly certified NBA or NHL model.

## Acceptance

- Exact-head targeted tests: `PYTHONPATH=artifacts/wow-engine pytest -q artifacts/wow-engine/test_llp_nba_nhl_deep_research_audit.py artifacts/wow-engine/test_llp_team_intelligence_bundle.py artifacts/wow-engine/test_llp_team_intelligence_readback.py`.
- Negative tests: unreceipted factors, mislabeled sport/event/model/snapshot, missing/non-UTC times, after-start data, goalie/lineup news newer than model, empty/research winner, unknown flag injection, market price non-influence, NFL unchanged.
- Independent SIRT review of date-safe evidence, exact-head CI, protected merge and actual authenticated Action/deployed SHA check before a production claim.
- Upstream fitted features and calibration remain a separate acceptance step under #1950/#1952; the read-only overlay is not equivalent to that step.


## Exact persisted winner provenance — independent acceptance challenge

A self-reported `source_receipt_id`, `probability_publishable`, `rank_eligible`, model version or terminal-authority string inside a Daily row is **not proof** that an immutable pregame prediction exists. The first research readback could label a fabricated but internally consistent result `GOVERNED_RECEIPT_VISIBLE`. The follow-up fail-closed repair requires a second read against the existing **`wow_event_predictions`** table, keyed by its real UUID `event_prediction_id` (never use the prop `wow_predictions` ledger or fabricate an ID). Compare exactly one authoritative stored event/model/side/calibration/snapshot/timestamp/probability record, including the immutable `locked_at` and actual `terminal_reduced_at` fields. A missing, malformed, ambiguous, late, mismatched or unreadable ledger record must hide numeric probability in this read-only presentation and show a typed source-diagnostic reason.

The pure offline projection without an authenticated DB is *always* receipt-unverified, even when a test record has all apparently certified fields. The authenticated route reads `wow_event_predictions` through the same authorized DB client as Daily readback, bounded to one lookup per page row and maximum 25 page rows; no scorer, model lifecycle, prediction writer, schema migration, separate controller or execution authority is introduced. Fixture-positive proof exercises a separately supplied immutable ledger row, while negative tests cover nonexistent/malformed IDs, wrong event, model, snapshot, lock, probability, publication, ambiguous duplicates, lookup errors and independence of optional research.

**Important release limit:** This lookup contract must be independently validated against the actual live server-owned writer and production-bound database before any product claim. A test fake does not establish existing production team-event predictions, independent SIRT acceptance or complete model certification. If a current Daily result uses some other receipt identifier than the event prediction UUID, its display remains held until a trusted mapping is proven rather than guessed.
