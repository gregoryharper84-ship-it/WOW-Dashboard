# WOW V17 — Certified Reference Model (CRM) and Deterministic Failover Program

> **P2 SOURCE-ONLY / BACKLOG CANDIDATE — not activated, certified, independently accepted, or deployed.**
> Priority: P0 #1562 restoration and security/release correctness always preempt this program.
> Owner: WOW Betting Intelligence product; implementation: Model Excellence + Production Engineering; recovery: AMRT; independent assurance: SIRT and independent QA/Release; handoffs: Ecosystem Conductor.
> `can_execute=false` · `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` · `V17_TERMINAL_REDUCER` is sole global terminal authority.

## Decision and goal

Create a legitimate **separately fitted, calibrated, certified, scope-constrained reference-model capability** per sport/market and a deterministic failover selector. A missing specialist must never cause an invented probability, nor should a valid but unqualified primary estimate trigger model-shopping. The backup is a *candidate controlling specialist* for the exact row only after trusted registry authorization and independent review; the terminal reducer alone decides publication.

Separate concepts:
1. **Service recovery:** retry and restore the existing scorer / credential / feature provider without changing the controlling model.
2. **Certified replacement:** use a distinct model that independently produces a valid prediction package for the exact settlement and feature availability regime.
3. **Research-only evidence:** verified notes, historical frequency and current prices clearly typed as non-model information if neither certified model can complete.

Do not use odds/no-vig, projections, L5/L10 hit rate, narrative, an LLM answer or legacy V16 provisional packages as governed probability. No production rollout is authorized by this document.

## Findings from current main at proposal creation

- `artifacts/wow-engine/agent_runtime/provider.py` already validates AVAILABLE matching capability with fitted artifact and calibrator, with a single match rule.
- `artifacts/wow-engine/agent_runtime/runner.py` has a `CONTROLLING_MODEL_PROVIDER_NOT_WIRED` barrier for the general worker. It must **not** be bypassed with a status flag; wire real scorer + trusted evidence + certification.
- `artifacts/wow-engine/agent_runtime/idempotency.py` already keys prediction identity by evidence snapshot, model artifact, calibrator, line and side. Reuse it.
- `artifacts/flask-scoring-api/validation/baselines/stat_model.py` is a useful research comparison baseline for MLB first inning, not a certified reference scoring adapter.
- `artifacts/wow-engine/migrations/20260915_first_six_team_event_ownership.sql` makes clear that routing ownership alone does not certify numerical capability.

These are **source inspection findings**, not statements about live production behavior.

## Proposed architecture

```text
User/current board + Scout (discovery, never probability)
  -> exact candidate, official event identity, status, line, settlement
  -> frozen trusted evidence snapshot / timestamps
  -> primary controlling specialist
      -> VALID package: retain primary; downstream terminal may still HOLD/REJECT
      -> eligible typed primary failure:
           1) bounded same-model recovery; preserve primary outcome
           2) server-side registry selects ONE certified reference for exact scope
           3) standalone reference model inference; own artifact + calibrator
           4) validate timestamp, probability, lower/upper, source/identity, drift
           5) selector proposes one controlling model, preserving primary failure
      -> any upstream hard blocker: no alternative model
  -> immutable independent receipts (primary error plus reference attempt)
  -> final refresh and one-per-row reconciliation
  -> V17_TERMINAL_REDUCER (only publication authority)
  -> Pick Intelligence Report with PRIMARY / REFERENCE / RESEARCH_ONLY provenance
```

No self-approval, no approval based solely on certified flag within a client request, no simultaneous multiple controlling probabilities for one thesis. Inference API must hydrate artifact/registry/approval from trusted server data, not source uploads or model-generated fields.

### Source-only deliverable in this PR

- `v17/certified_reference_selector.py` is a PURE off-line selection policy, not a scorer or live adapter. It preserves the primary failure, rejects status shopping, requires exact certified scope, requires independent review identity, blocks missing-input regimes unless explicitly certified, and never publishes.
- `test_v17_certified_reference_selector.py` contains adversarial unit cases, including unsupported lines, invalid probabilities, stale timestamps, certification expiry, nonpregame requests, missing input regimes and untrusted transition attempts.
- No existing runtime, Supabase schema, Action, workflow, registry, deploy controller, model probability or calibration threshold is modified.

## Model science workstreams (Model Excellence)

Each model must be a separately fitted artifact with hashed training snapshot, feature schema, calibration artifact and reproducible training recipe. Data must be pregame as-of, versioned, legally usable, correctly timestamped and leakage-tested.

**Pilot A: NFL full-game winner and spread**
- Fit a predictive final scoring-margin distribution from team quality, quarterback status, venue, rest, pace, injuries, defensive efficiency and verified material inputs, not sportsbook implied percentages.
- Winner uses exact settlement P(margin > 0) plus tie/overtime semantics; spread uses P(margin crosses the exact signed threshold) with push probability for integer spreads. Never treat P(win) as P(cover).
- Calibrate both derived market families independently or jointly with coherence/monotonicity tests and interval reliability. The same underlying game cannot produce inconsistent opposing sides.
- Use appropriate alternative distributions (e.g., count-scoring or flexible margin residuals) as challenger candidates; choose on untouched holdout evidence, not anecdote.

**Pilot B: NFL rushing and receiving yards**
- Model playing-time/role/workload distribution and conditional per-opportunity production with injury, usage and opponent context. Permit zero opportunities, heavy tails, negative play yards, and scenario dependence.
- Produce exact-line probabilities from the predictive distribution, not manually shifted averages. Separate rushing and receiving specialist artifacts/calibrators.
- Exact opponent/line/period/settlement and role availability must pass; no interpolation for uncertified line support.

**Later, contingent on pilot certification:** NFL anytime TD, totals, NBA minutes/opportunities and points/assists/threes, MLB K/outs, NHL, soccer three-outcome match, WNBA/NCAAF/NCAAB. Each gets its own sport/stat artifact and calibrator; no cross-sport cloned coefficients.

### Required datasets and train/validation discipline

For each family:
- schema, provenance, usage rights, source update times, canonical event IDs, pregame snapshots, official outcomes and push/void settlement;
- chronological walk-forward train -> separate calibration -> untouched out-of-time holdout, with no player/game/date leakage across folds;
- stratification by season, home/away, spread/line bands, role certainty, injury regime, bookmaker-independent context and key opponent cohorts;
- Brier, log loss, calibration intercept/slope, ECE with confidence intervals, reliability diagrams, cohort sample sizes, out-of-sample sharpness and lower-bound coverage;
- prespecified baseline/champion comparisons, latency and resource budgets, drift triggers, minimum viable coverage and rollback criteria;
- fallback-trigger-distribution validation (evaluate on examples *selected because primary fails*, not just all normal games);
- calibrated lower-bound method predeclared, with bootstrap/conformal/other empirically justified interval construction and holdout coverage verification; never arbitrary point-minus-fixed-percent;
- reproducible artifact hashes, signed approvals, domain restrictions, confidence claims and cold-start rejections.

**No guessed numeric certification threshold** in this source package. Model Excellence proposes metrics by family; independent authority must accept them before any publishable fallback. Thresholds must not be optimized on the final holdout.

## Failover contract (Production Engineering + AMRT)

1. Route exact typed primary outcome. Eligible candidates initially: MODEL_UNAVAILABLE, MODEL_SCORER_FAILED, MODEL_OUTPUT_INVALID, MODEL_INPUTS_INSUFFICIENT, each only where the reference certification explicitly lists it.
2. Transport/host failures keep their precise typed failure; never mislabel `ACTION_TRANSPORT_TIMEOUT` as a model failure. An unavailable Action/host does not justify client-side probability synthesis.
3. If primary returns a valid model package, retain it even when calibration/market/terminal gates withhold selection. **No fallback shopping to make a pick.**
4. Attempt bounded retry only for transient, classified same-model failures; receipt and immutable idempotency. Do not retry identity, safety, data or status failures.
5. Evaluate all upstream blockers *before* fallback. Live/started, wrong event/date, stale primary evidence, ambiguous line/settlement and material unresolved injuries remain blocked unless a separately certified live and exact degraded-input route explicitly covers them and full V17 live governance permits it.
6. Server-authenticated reference registry must prove exact `sport/market/stat/period/settlement/line`, artifact hash, calibrated bound method, valid certification window, independent approval receipt, allowed failure class and precise missing-feature cohort.
7. Exactly one designated controlling provider per immutable canonical candidate. Preserve the failed primary as historical attempt evidence and select reference with its own provider/artifact/calibrator and prediction ID.
8. Validate probability geometry, bounds, timestamp >= latest material update, normalization, outcome-space coherence, distribution semantics, independent feature provenance, evidence freshness, cohort coverage and final refresh.
9. A reference model failure retains BOTH native failure statuses and ends unqualified. No third-party proxy, mathematical inversion of sportsbook prices or fabricated certainty.
10. Make a nonpublishable research report on failure. Terminal authority stays reducer, publication and card construction stay downstream.

Production registry/admission adapter needs authenticated source-of-truth reads; selector policy alone cannot confer trust on a supplied `certified=true` field. New public typed failure codes, if needed, must be registered in canonical `docs/failure_codes.md` in the same integration PR; selector reason codes here are *internal diagnostics only*.

### Future trust/persistence design — NOT an applied migration

Add only after approved schema review:
- `reference_model_certifications`: exact market/line/cohort, artifact SHA, calibrator ID, training dataset hash, holdout metrics, coverage, authorized reviewer, valid_from/until, lifecycle state.
- `reference_fallback_attempts`: run ID, immutable candidate/evidence key, primary typed failure/attempt, reference eligibility decision, selected model, reference receipt, timestamps, eventual terminal decision.
- `reference_model_backtests`: dataset/version, chronological folds, predictions, calibration cohorts, bounds and signed validation artifacts.
- `reference_model_health`: model version, run denominator, stale/drift rate, calibration alerts, fallbacks selected, terminal eligibility by market.

No external request can write or edit certification fields; RLS and service-role restrictions need independent security review. Production DB identity must be verified, not inferred from validation Supabase.

## Pick Intelligence output contract (WOW Chat product)

Each evaluated exact pick provides:
- Player/team/event, league, market and exact signed line/side, source/offer/settlement and event time.
- Origin PRIMARY_CERTIFIED / REFERENCE_CERTIFIED / RESEARCH_ONLY; controlling specialist, model/artifact/calibrator IDs, cert ID and receipt ID when available.
- Raw, calibrated, lower/upper, confidence level, projection/distribution/push probability **only as actually emitted** by certified specialist.
- 3-5 sourced factors with "numerically used by model" vs "research context only", current injuries/workload, relevant opponent/cohort samples, observable contradictions.
- Quantified failure paths only if fitted specialist provides them; otherwise explain descriptively without pretending to adjust probability.
- Exact sportsbook market evidence, freshness, no-vig and value separately if requested; never backfill sporting probability from price.
- Primary failure context and why reference was eligible (or why none qualified); model timestamps; final refresh; immutable prediction ID; exact reducer label.
- Card/SGP report after qualified legs only: dependence/correlation, critical hinge, session exposure, weakest-leg and shrink-not-fill policy.

RESEARCH_ONLY output may include market-derived no-vig as a clearly identified **market benchmark** and historical rates with sample sizes, but never as certified WOW probability or qualification.

## Ecosystem ownership and handoff conditions

| Party | Deliverable | Handoff / veto boundary |
|---|---|---|
| WOW Betting Intelligence (Chat), PM | scope, user report, prioritization, ranked market roadmap | Cannot certify probabilities, scores or releases |
| Model Excellence | real fitted family artifacts, calibration, OOT / fallback-trigger validation and challenger evidence | Cannot self-authorize terminal publication |
| Research + Scout | timestamped sports features, contradictory evidence, data license and provenance audit | Discovery only |
| AMRT (Model Recovery Commander + Dependency Investigator + Restoration Engineer + Regression Specialist + Deadlock Breaker) | primary root-cause recovery, precise typed failures, bounded retry implementation, regression/adoption handoff | No self-review, no activation |
| Production Engineering | trusted adapter, exact routing, immutable storage, deterministic provider selection, real Action and Render integration | No release bypass; independent ownership required |
| SIRT | adversarial calibration/coverage challenge, drift/incident and production canary assurance | Independent review, not a second deploy controller |
| Independent QA + Release | independent exact-commit tests, role/credential separation, authorized production release/rollback acceptance | Only real independent authority can approve |
| Ecosystem Conductor | one owner per candidate, idempotent stage receipts, cross-team dependencies and stalled-work alerts | Coordination only; no probabilities/releases |

All parties are named as **requested review roles** here; listing them is NOT evidence that an autonomous team reviewed or approved this branch.

## Phases and explicit closure criteria

**Phase 0 — Inventory + contract:** audited active capability registry, training datasets, failure taxonomy, no silent category gaps; production/restoration dependencies bounded; architecture and data security reviewed.
**Phase 1 — Fitted pilots:** NFL margin/win/spread and NFL rushing/receiving reference artifacts; training code, out-of-time replay, calibration and signed candidate receipts.
**Phase 2 — Shadow selector:** implement trusted registry adapter and backend scorer integration on staging only; pregame exact identity/line, deterministic one-owner receipt, preserved original failure and no production change.
**Phase 3 — Independent challenge:** test missing data, transport outages, malformed responses, late injuries, stale scores, live events, opposing direction, spread pushes, changed lines, historical leakage, calibration shift, reference absence, shared primary outage, malicious client-provided certification.
**Phase 4 — Controlled release:** trusted Engineering App source adoption, exact-head CI, independently owned QA and release approval, authorized Render revision, genuine user GPT Action with immutable receipt and terminal; rollback rehearsal.
**Phase 5 — Operational acceptance:** end-to-end production evidence, reconciliation denominators, watch/drift dashboards, independent SIRT acceptance, historical postmortem/learning loop, verified no execution, complete report rendering.

Each phase keeps independent verification and must not be conflated with source presence or a passing local unit test.

## Acceptance matrix (must be independently executed)

- Valid primary retains ownership even when its market/value or reducer label is HOLD.
- No fallback for valid-but-rejected primary, wrong exact line, started/live game without dedicated live capability, unsupported side, stale material status or settlement mismatch.
- Primary scorer failure and model artifact unavailable remain distinct native statuses; Action timeout is not model unavailable.
- Reference calibration must be distinct, valid, not expired, and independently certified for precise cohort/failure/missing-input regime.
- Client attempts to forge certification/approval never affect trusted adapter.
- One canonical controlling provider and one terminal outcome per candidate; no multiple simultaneous authoritative probabilities.
- Reference returning a malformed or stale package remains blocked; original primary failure is preserved.
- Win, spread, and total outcome normalization, opposites/pushes, line monotonicity, and dependence checks pass applicable fitted-family tests.
- Shadow scoring does not write qualified selection or invoke external bet/order endpoints.
- Every proposed row reconciles; duplicate discovery does not multiply selection probability or card exposure.
- Source CI/local test and live staging Action/Render/Supabase validation reported separately.
- No deployment, registration, privilege or config mutation in this P2 research PR.

## Explicit incomplete items / truth labels

SOURCE_IMPLEMENTED: pure selector plus adversarial tests.
DESIGN_COMPLETE: architectural scope and cross-team acceptance contract.
FITTED_MODELS_TRAINED: **NO**.
ARTIFACTS_CERTIFIED: **NO**.
REGISTRY_ADAPTER_INTEGRATED: **NO**.
PRODUCTION_ACTION_VERIFIED: **NO**.
PRODUCTION_DEPLOYED: **NO**.
P0_PREEMPTION: **YES**.

**Backlog rule:** this source package may be preserved now but no production modification, adoption, auto-merge, CI/QA slot consumption or release should compete with unresolved P0 #1562. Re-evaluate after independently verified production restoration; then sequence Phase 0→5.
