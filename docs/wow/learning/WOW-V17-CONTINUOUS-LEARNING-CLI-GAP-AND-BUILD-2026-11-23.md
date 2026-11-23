# WOW V17 Continuous Learning Intelligence — gap review and build packet
Date: 2026-11-23 UTC
Owner: WOW Betting Intelligence (requirements and acceptance)
Engineering owner: AMRT / existing Engineering single writer
Independent challenge: SIRT
Release acceptance: Independent QA + Release Authority + independent production verification
Tracking: #2164 (continuous intelligence); #1252 (context pipeline); reuse #2020 for any relevant release-gate incident

## Source review and verified code baseline

This packet reviews the uploaded "Building a World-Class Continuous Learning Intelligence for WOW V17" proposal against repository main 9b1256fb609de7a20a4a7c739d7322aa354f0182 and the existing WOW V17 Postmortem Retro & Learning Engine.

Existing work **must be reused**, not replaced:
- v17/llp_postmortem_recalibration.py: immutable team/event Brier, log loss, lower-bound gap, stratified roles, ranking regret, refresh diagnostics and advisory review triggers.
- v17/postmortem_engine.py and migrations/20260907_v17_postmortem_preserve_refine_regression.sql: append-only postmortem records and retrospective probability isolation.
- v17/continuous_intelligence_cycle.py: the existing scheduled orchestration of settled PROP/EVENT capture, summaries and memory.
- v17/experiments/nov16_ml_slip_learning/learning_shadow.py: per-prop discrete PMF CRPS and late revision-regret research, plus conditional game-script research.
- v17/experiments/nov14_followon_intelligence/followon.py: separate probability/economics and same-event dependence audits.
- .github/workflows/wow-v17-model-improvement-experiment.yml: existing governed experiment pathway.

No new orchestration service, worker, autonomous approval authority, vendor subscription, feature store, model registration, database migration, or prediction-table writer is introduced by this change.

## Built in this PR: additive diagnostic primitives

- v17/learning_distribution_diagnostics.py: binary **binned** Murphy reliability/resolution/uncertainty with a disclosed reconstruction residual. The approximation residual is not mistaken for drift.
- Read-only **count-prop** discrete CRPS and deterministic integrated randomized-PIT expectation, using only explicitly frozen pregame PMFs and an exact independent official settlement.
- Counted blockers for invalid pregame chronology, material refresh after model timestamp, duplicate prediction ID, exact event/player/market/period/offer/direction/settlement mismatch, nonofficial settlement, malformed PMFs, and impossible binary settlement.
- A zero-model-mass realization still contributes to CRPS; it is excluded from PIT only, with its receipt ID retained.
- Small cohorts return INSUFFICIENT_SAMPLE; all outputs remain diagnostic only.
- Sixteen focused tests use synthetic fixtures and do not establish production source integrity or sport performance.

**Important:** the module validates supplied metadata; it does not cryptographically authenticate a receipt, query actual settled records, or establish independent source custody. Callers MUST perform those steps before the helpers consume data. The audit's local reason strings are research diagnostics, not new V17 backend failure codes. If later surfaced through production APIs, register them in the canonical failure-code registry in the same change.

## Corrections and missing safeguards in the uploaded proposal

1. The inventory must include **cricket** explicitly as the 13th sport; use the live specialist registry rather than assuming that every inventory sport has a fitted production model.
2. Do not call all losses model defects or near-threshold wins prediction failures. Fragile-win margin is postsettlement descriptive evidence, NOT a pregame input, fitted confidence haircut, or an automatic challenger trigger.
3. Murphy REL-RES+UNC is exact when forecasts are constant within bins; when variable forecasts share bins, explicitly report the binning reconstruction residual. Fix bin boundaries and disclose sample sizes.
4. Discrete PIT/CRPS require frozen full count distributions. A binary MORE/LESS probability or an after-the-fact invented PMF cannot support distributional grading. Do not drop zero-mass tail misses from CRPS.
5. **Soccer 1X2, ties, multi-runner golf, boxing draw/no-contest**, and other multi-outcome settlement spaces need their OWN properly normalized multiclass scoring contracts, not three independent binary rows or push-as-loss.
6. Separate forecast events, receipt versions, canonical theses, repeated slip exposure, unique official settlements, and user-published selections as different denominators; keep all rejected/held discovered rows in the reconciliation ledger without inventing unissued forecasts.
7. Prevent outcome leakage through source revisions, market closing odds, late injury information, feature lookback, calibrator training windows, refreshed after-event snapshots, and train/test grouping across legs from the same event.
8. Grade **model-version + calibration-artifact digest + sport + market family + favorite/underdog role + prop direction** before pooling. Respect season/phase, published/held selection strata, abstention and coverage, and prelock quote/price availability.
9. For no-vig CLV, preserve exact line, both contemporaneous sides, source/time, settlement and bookmaker; Power/Shin are optional research methods, not mandatory one-size-fits-all policy. No price = CLV unavailable, not zero.
10. Never automatically widen calibration intervals for market-heavy favorites, apply a uniform edge gate, set exposure caps, or change usage weights in a diagnostic patch. These are Class C hypotheses needing fitted calibration/replay/independent promotion.
11. For cross-lane game-script updates, maintain an explicit input graph (affected event/team/player/stat/market), one canonical material-update timestamp, invalidation, deduplicated rerun and an immutable before/after receipt. A WINNER model cannot fabricate prop distribution parameters.
12. Online ADWIN/Page-Hinkley, conformal PID, high-dimensional CopPIT and HLN-DM are optional shadow tests, not a required Python dependency or automatic deployment gate. Use effective independent sample size and cluster-by-event uncertainty; HLN-DM assumptions do not prove causal lift.
13. Never take a postmortem narrative as a verified causal failure code. Separate observed fact, prelock predictability, the fit's numerical consumption, counterexamples, and human-review hypothesis.
14. Supabase hybrid memory needs verified tenant isolation/RLS, dedup, source licensing, retention, embedding provenance, and a measured lexical-search insufficiency before adopting pgvector or paid embedding calls.
15. Always preserve all existing authorized failure codes, the original prediction ledger and the independent exact-head release path. A green test or job status is not accepted production learning.
16. Datadog is retired and is not a source, dependency, acceptance gate or repair target. Reuse GitHub + Render + Supabase, within $0 incremental spend.

## Canonical next integration sequence and acceptance

| ID | Priority | Scope | Owner | Acceptance evidence |
|---|---|---|---|---|
| CLI-01 | P0 | Independently verify frozen pregame source joins and 13-sport inventory/coverage; map existing settled prop/event capture to exact receipts | AMRT Data | Live read-only capture logs, source snapshots, per-sport denominator reconciliation; no unexplained row losses |
| CLI-02 | P0 | Integrate this pure diagnostic module into existing continuous_intelligence_cycle only AFTER CLI-01; expose differentiated available/unavailable CRPS/PIT/Murphy | Engineering | Integration replay at exact version, negative stale/void/line fixtures, production readback, validated cohort counts; never compute from synthetic PMFs |
| CLI-03 | P0 | Separate binary and multiclass settlement grading, calibration and lower-bound reliability; register any new external typed failures | LLP / PROP fitted owners | Soccer draw + multiway normalized distribution tests; both directions; unchanged production fitted outputs |
| CLI-04 | P1 | Add missing revision/override sample denominators, selection-coverage and ranked-rejected counterfactual ledgers without rewriting pregame history | Engineering + Betting Intelligence | Original/revised prelock receipts, official settlement, ranking regret, no hindsight, preserved correct predictions |
| CLI-05 | P1 | Independent cohort drift and challenger proposals; no direct weight/interval/threshold adjustment | Betting Intelligence + SIRT | Same-cutoff, event-grouped chronological holdout; n, paired Brier/log loss/CRPS, bias, bounds, uncertainty, counterexamples, rollback |
| CLI-06 | P2 | Cross-lane scenario models, specialized regime mixtures, optional Conformal PID/HLN-DM and memory index | Fitted specialist / Engineering | Real frozen scenario features, validated covariance/joint calibration, preserved strengths, independent Class C acceptance |

Promotion contract: Discover -> Hypothesis -> Challenger -> Historical Replay -> Counterexample Review -> Untouched Forward Holdout -> Regression -> Governed Recommendation. Engineering proposes changes; SIRT independently challenges; exact-head QA and Release review plus production/Action receipt establish acceptance. V17_TERMINAL_REDUCER alone owns terminal disposition.

## Hard invariants

can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
terminal_authority=V17_TERMINAL_REDUCER
No automatic fitted parameter, calibration, bounds, qualification, portfolio-policy, or wagering change.
No certification without independently authenticated historical receipts, exact model/artifact version and genuine forward evidence.

## Statistical references

- Czado, Gneiting & Held (2009), *Predictive Model Assessment for Count Data*, Biometrics: https://doi.org/10.1111/j.1541-0420.2009.01191.x
- Stephenson et al., *Two Extra Components in the Brier Score Decomposition*, on binning approximation and residual terms: https://doi.org/10.1175/2007WAF2006116.1
