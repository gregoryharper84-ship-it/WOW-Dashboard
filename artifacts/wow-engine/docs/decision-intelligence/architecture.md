# WOW V17 Decision Intelligence — Engineering Design (Proposal)

Date: 2026-10-08
Status: DESIGN_ONLY / NOT CERTIFIED / NOT DEPLOYED
Owner: WOW Engineering; product acceptance: WOW Betting Intelligence
Reviewers: SIRT (independent assurance) and Independent Verification (release evidence)

## Mission and non-goals

Extend existing V17 to distinguish (1) sporting outcome probability, (2) market price/value, and (3) portfolio/exposure quality, without reducing ongoing P0 restoration throughput. This is a bounded implementation plan, **not** a new top-level probability authority and **not** an authorization to bet.

Permanent locks:
- WOW_BETTING_ENGINE is the governing host; exactly one fitted sport/stat specialist owns each sporting probability.
- V17_TERMINAL_REDUCER is sole global terminal/publication authority; downstream analysts cannot reverse holds or alter probabilities, calibration or lower bounds.
- can_execute=false; DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.
- No wager placement, modification, cancellation, order routing, bankroll mutation, or production staking suggestions.
- No market-implied, consensus, historical-hit-rate, LLM or external-projection substitution for governed sporting probabilities.
- Keep BACKEND_RUNTIME, MODEL_CAPABILITY, REPOSITORY_GOVERNANCE and LIVE_GPT_EDITOR_SYNC independent.

## Existing implementation anchors (extend, do not fork)

- #874: exact-line Market Edge Reconciler, price provenance, raw/de-vig conversion, typed failures. Preserve its Class B research-only boundary.
- #974: weakest-leg elimination, session thesis graph, repeated exposure, Power/Flex structure.
- #1221: post-STANDARD_OPERATOR temporal data, chaos tests, calibration monitoring and shadow covariance research.
- #1227: post-STANDARD_OPERATOR rolling-origin model validation, independent market disagreement diagnostics and **post-reducer** economic-capacity research.
- #1252: point-in-time outcomes, monitoring, learning and diagnostics.
- Existing contract: artifacts/wow-engine/docs/v17_moneyline_market_value_semantics.md.
- Typed statuses: artifacts/wow-engine/docs/failure_codes.md. Do not mint new codes unless that registry changes with implementation.

### Dependency / activation

The repository currently records #1221 and #1227 as DEFERRED_WITH_JUSTIFICATION pending maintained STANDARD_OPERATOR status. Documentation, review, tests on isolated fixtures and planning may proceed; do not represent engineering implementation as activated until its explicit gate and capacity criteria are verified. P0 transport/Action/slate recovery remains primary. No automatic merges, deployments, model promotions or editor sync are authorized by this document.

## Proposed bounded architecture

A. Discovery -> canonical identity -> exact controlling specialist -> calibrated probability/lower-bound package -> refresh, reconciliation, persistence and V17 terminal reduction. This existing path must remain operational if every new research component is down.

B. **#874 Market Edge Reconciler:** may perform evidence-only exact-price diagnostics in the existing supported flow. It does not change reducer status or produce a terminal market permission. Link every diagnostic to the original immutable sporting prediction and quote snapshot.

C. **Post-reducer Market Capacity/Economic Research Analyzer (#1227):** consumes only already terminal-authorized immutable pregame predictions and matched quotes; computes descriptive gaps and hypothetical unit-payoff EV. It is NOT a scoring specialist and is read-only.

D. **Portfolio Exposure Research (#974, #1221):** consumes immutable candidates, proposed hypothetical cards and correlation provenance; emits structural annotations, not probabilities, stakes or terminal-label overrides.

E. **Outcome/Validation Read Model (#1252, #1221):** grades exact immutable pregame selections using verified settlement and close snapshots; separates model validation, market-value diagnostics, card design and realized variance.

SIRT monitors each independent service; Independent Verification certifies test and production-revision evidence. A market or risk outage never erases a completed sporting probability.

## Immutable research contracts

### Probability input (read-only)

Required: prediction_id, research_run_id, canonical_candidate_id, exact event ID, league/sport, scheduled start and pregame status, selected side/direction, period/stat/line if applicable, settlement basis, controlling_specialist, model artifact/version, model timestamp, calibrated_probability, calibrated_lower_bound, confidence metadata, probability_publishable, rank_eligible, terminal status and durable receipt reference.

Never rebuild probability from odds. No valid exact specialist proof -> no value conclusion. A sporting package with a market-only blocker must be preserved independently, without manufacturing a value receipt.

### Exact market snapshot

Required: source/provider, sportsbook/venue, event and selection identity, period/stat/exact line, settlement terms, bid/ask or executable/observable price as applicable, price type, UTC quote timestamp, captured_at, market-open/source status, raw implied break-even threshold. Opposing same-book sides are required for ordinary de-vig; never pair unrelated books. Keep raw break-even and de-vig consensus separately.

Classify evidence as EXACT_LINE, ADJACENT_LINE or NO_MARKET. ADJACENT_LINE is context only unless a separately certified conversion exists. The BEST observed price is a market candidate and must have its own freshness and availability proof.

### Decision research receipt (new data contract; no table yet)

Suggested fields: analysis_id, schema_version, parent_prediction_id, candidate_id, model_version, probability_receipt_hash, market_snapshot_id/hash, compared_at_utc, exact_identity_status, settlement_status, freshness_status, source_book, source_price, raw_breakeven_probability, no_vig_fair_probability_nullable, calibrated_probability_readonly, calibrated_lower_bound_readonly, point_price_gap, conservative_price_gap, unit_expected_net_return_nullable, lower_bound_unit_expected_net_return_nullable, payout_assumptions, fees_and_friction_provenance, research_status, typed_failure_code_from_registry_if_any, can_execute=false.

Unit normalized theoretical EV for a **binary, no-push** settlement and decimal gross return d: p*(d-1) - (1-p), before separately evidenced frictions. If push/void/tie is possible, use the contract's full outcome distribution and actual settlement. Use the exact executable raw break-even price for unit-return evaluation, not no-vig consensus as if executable.

PrizePicks: do not invent standalone leg odds. Evaluate complete exact Power/Flex/promo payout rules only if a valid jointly-modeled outcome distribution and push/void policy are available. Never multiply leg probabilities assuming independence without certified dependence support. Otherwise explicitly mark payout/EV analysis unavailable while preserving individual sporting forecasts.

### Exposure research receipt (no allocation)

Fields: analysis_id, card_or_session_id, list of immutable prediction IDs, canonical-thesis dedup map, shared game/participant/scenario keys, dependence_method/version/provenance, confidence/unknown flags, weakest-leg analysis, duplicate counts, hypothetical unit-downside only when joint support valid, structural status. **No account balances, wager amounts, stake recommendations, Kelly outputs or bankroll writes** in the first phase.

A future allocation or Kelly challenger requires a separate governed proposal. #1227 disallows sizing in its current lane; #1221 lists fractional Kelly as research hypothesis only, not release permission.

## Independent dual leaderboards

1. SPORTING PROBABILITY leaderboard: only current V17 rank-eligible rows, descending governed calibrated_lower_bound. Market prices do not influence the rank.
2. MARKET VALUE RESEARCH view: only exact-price, valid, post-reducer research receipts; report point and conservative price gaps, theoretical unit EV and uncertainty/assumptions. **Not** an alternate V17-approved-picks leaderboard; no arbitrary edge gate or recommendation promotion.
3. PORTFOLIO STRUCTURE view: independent duplicate/correlation/concentration annotations only. Do not mutate (1) or (2).

## Phased implementation plan (separate small PRs)

- D0 — review this specification and SIRT/product criteria. No production code.
- D1 — reuse #874 exact-line matching/market capture/de-vig; implement/replay an isolated read-only fixture adapter if needed, without probability gate changes. Class B, independently testable.
- D2 — when STANDARD_OPERATOR and #1227 gates pass, add post-reducer, read-only economic diagnostics using existing immutable receipts; no execution or sizing. Class B research.
- D3 — reuse #974 thesis graph and weakest-leg fields; only validated joint-risk measurement under #1221 Class C challenger. Keep any unsupported dependence unknown rather than zero.
- D4 — extend #1252 / #1221 outcome read-model and reliability dashboards, cohort calibration, Brier/log loss and CLV. Observation-only, not auto-certification.
- D5 — SIRT injects adversarial identity, freshness, payout, numerical, and downstream outage failures; Independent Verification owns exact-head CI, production SHA and read-only canary evidence.
- D6 — only a future separately proposed, validated Class C project may consider value thresholds for qualification or hypothetical capital allocation. This proposal grants no such authority.

## Delivery and rollback criteria

Each PR: bounded changed-file scope, linked issue/owner, frozen before-after fixture receipts, unit + integration regressions, typed error mapping to registry, no changed sporting probability snapshots, independent review, exact-head required CI. Research feature flags default OFF; missing config OFF. Deployment (if later authorized) must be revision-pinned, independently observed, and rollback-tested. Never claim FIXED_VERIFIED from local checks alone.

A successful D0 means documentation and tickets are reviewable, **not** that value or portfolio services are operating.