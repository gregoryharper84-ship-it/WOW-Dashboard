# WOW V17 — AMRT Model Restoration & Recovery Unit

**Status:** PROPOSED GOVERNANCE / DOCUMENTATION ONLY — **NOT ACTIVATED**
**Parent organization:** Production Engineering
**Authoritative coordination:** existing WOW Ecosystem Conductor, Engineering worker, canonical GitHub incidents and durable receipt/lease system
**Companion source candidate:** [AMRT implementation PR #1843](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1843)
**Organization backlog:** [#1856](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1856)
**Restoration acceptance umbrella:** [P0 #1562](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1562)
**Safety:** `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Purpose and jurisdiction

AMRT is the *Model Restoration & Recovery Unit* inside Production Engineering. It restores **already-governed** model capabilities that are broken, unavailable, incomplete, incorrectly routed, incorrectly integrated or incorrectly published. Its success criterion is **independently verified functionality in production**, not a bug report, green local test, passing CI, generated patch, or a merged PR.

The wider organizational division is:

| Function | Accountable scope | Cannot do |
| --- | --- | --- |
| Production Engineering / Production Operations | Platform, deployment mechanisms, worker operations, reliability, runtime configuration, rollback and protected release integration | Treat operating health as proof of model acceptance |
| AMRT | End-to-end restoration of an incident's pre-existing model capability, including dependency forensics, source repair coordination, adversarial regression and closure tracking | Redesign or recalibrate fitted sporting probability under ordinary repair authority |
| Model Excellence | Develop, retrain, calibrate, challenge or materially extend fitted models; Class C model science | Self-certify its own changes as production released |
| SIRT | Independent systemic failure detection, recurrence analysis and reliability assurance | Self-approve engineering fixes or become a duplicate deployment controller |
| Research & Innovation | Evaluate tools, practices and longer-term improvements across all teams | Insert unapproved tooling directly into production authority |
| WOW Betting Intelligence (Chat) | Product priorities, coverage requirements and user-journey acceptance | Produce fitted probability, bypass protected gates or declare engineering approval |
| WOW Ecosystem Conductor | Unique incident ownership, lease/re-entry, deduplicated routing and durable cross-team handoffs | Replace QA, Model Excellence or Release Authority |

AMRT is a **specialist routing and evidence layer**, **not five more independent daemons, queues, databases, GitHub Apps or autonomous write identities**. Extend PR #1843's seven existing source roles, existing Engineering Reporter/Triage/Engineering/Review/QA/Release and existing worker; no duplicate authority.

## Five specialist responsibilities to add to existing roles

| Specialist responsibility | Existing home / upstream role | Bounded output and next handoff |
| --- | --- | --- |
| **Model Recovery Commander** | Conductor + Blocker Sentinel / Reporter; one accountable case owner, not an additional writer | Own recovery case from detection through verified release, record the current blocker, owner, lease epoch, next action, exact re-entry trigger and linked PRs; maintain case until independent closure |
| **Model Dependency Investigator** | Root Cause Investigator / Research Triage | Trace canonical event and identity, raw feed and freshness, hydration/features, exact fitted artifact/model registration, scorer, package, calibration-stage producer, publication, Action and persistence; record *first failing stage* with positive/negative evidence |
| **Model Restoration Engineer** | Existing Model Recovery Engineer + Data & Integration Engineer + authorized Code & CI Engineering implementation | Repair integration/runtime/data/model-loading issues *without changing fitted probabilities or governance thresholds*; Class C redesign/retraining/calibration transfers to Model Excellence while AMRT retains incident tracking |
| **Model Recovery Regression Specialist** | Code & CI Engineer; independent QA remains separate | Produce minimally reproducible pre-fix failure, post-fix exact-SHA test, adjacent-sport and adverse/fail-closed coverage, safe replay fixtures and source-lineage proof; test author is never independent verifier of their own fix |
| **Recovery Escalation & Deadlock Breaker** | Conductor + Code & CI support; protected Release/Security retain approval | Detect rejected review, stale/expired lease, unchanged head, stuck CI, missing receipt, unavailable coding provider and drift; issue typed re-entry/authorized fallback instruction. Never take another actor's lease, self-approve, force-merge or bypass release |

These are **specialist functions proposed for the existing AMRT role handlers**. Documentation does not create a running agent. The Engineering App must adopt any implementation with its actual independent approval requirements.

## Recovery contract

1. **Detect and reproduce.** Existing SIRT/Datadog/Render/Action/model/GitHub evidence routes to one canonical incident; deduplicate by failure fingerprint, affected model/sport/market and first failing stage. Record observed-vs-expected behavior and source proof.
2. **Trace dependencies.** Pin sport, market, official event/player, pregame-vs-live capability, canonical model ID/version/artifact, source/feature freshness, scorer, emitted stage receipts, terminal, Action transport and persistence. Distinguish absent artifact, insufficient input, failing scorer, missing proof, governance HOLD, and release/runtime drift. Never infer a successful stage from an upstream PASS.
3. **Repair within one lease.** Keep one accountable owner and one authorized mutable writer with atomic epoch/fencing. P0 model recovery receives the existing Conductor's protected RAPID/domain lane; existing incident conflicts and other P0 work cannot be pre-empted by a duplicate PR. Make the smallest correct producing-layer repair and preserve existing fitted outputs.
4. **Regression and challenge.** Reproduce original failure against pre-fix behavior when feasible; prove fixed and adjacent sport/market/failure paths on exact candidate SHA. Independent review, security/model review when applicable and QA come from separate allowed actors. Class C fitted-model changes require Model Excellence certification and governed Class C release.
5. **Authorized merge and deployment.** Only the existing protected release pipeline may merge and deploy. Verify reviewed head/CI SHA, authorized integrated main SHA, Render actual LIVE SHA, production database identity and canonical Action/schema revision. A source branch SHA and a successful workflow are not live acceptance.
6. **Independently verify and close.** Execute the real user-facing Action journey with the representative affected model/market; require correct calibrated probability/lower bound only from the fitted specialist, typed missing/held cases, event/run/SHA-aligned immutable persistence, exact-once reconciliation and terminal concordance. Independent QA and Release supply their own receipts. Only then can the canonical case become `FIXED_AND_VERIFIED`.

### Fail-closed incident packet (existing Conductor/engineering receipts)

Carry: `incident_id`, `parent_incident`, affected sport/market/capability and model ID when known, `failure_fingerprint`, `first_failing_stage`, `typed_failure`, `reproduction_ref`, `root_cause_ref`, `priority`, `current_owner`, `lease_id`/`lease_epoch`/`expiry`, `source_pr`/`head_sha`, `change_class`, `required_external_approvals`, `next_action`, `reentry_trigger`, plus immutable CI/QA/Release/Render/Action/persistence evidence references as obtained.

Proposed fields must map to the **existing** validated receipt envelope; do not create a second ledger or imply that missing evidence was produced. Hold `BLOCKED_WITH_EXACT_REASON` for missing trust root, proof, release approval, lease or live verification; keep an owner and actionable re-entry trigger. `CLOSED_SUPERSEDED` is reserved for proven duplicate/fully transferred cases, not stale work or abandoned PRs.

### Guarded fallback / deadlock resolution

After **configured** bounded attempts or an explicit stalled state, the Escalation specialist sends an evidence-based re-entry request to the existing Conductor. First prefer recovery of the original authenticated writer/reviewer; only route an alternate approved provider under a valid *newly granted* lease/permissions and the existing PR or carefully deduplicated successor. Expired writer leases are **not** automatically acquired by an observer. If a protected reviewer, release authority, model owner or trusted execution path is unavailable, maintain HOLD and identify the required separate authority. Never use a generic Render deploy or GitHub override as a substitute.

## Priority acceptance examples (do not misreport as repaired)

- **[#1841 / PR #1842](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1842):** NFL scored rows retain their real fitted outputs but publication remains HELD while actual dynamic-calibration and final-refresh producing-stage receipts are absent. Restoration requires real producer evidence, full/compact Action readback, same-run persistence and independent acceptance.
- **[#823 and #1806 / PR #1835](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1835):** Cross-sport canonical identity, provider-alias dedupe and at-most-once scoring, source-lease provenance and exact-head independent review must be proved before authorized merge/deploy; verify all affected sports in production, not only NFL.
- **[AMRT source PR #1843](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1843):** Existing seven handlers, closure logic, receipt bridge and supervisor integration are source candidates. Model-specific extensions here must not trigger a second dispatcher. Production activation is unverified until authenticated App adoption, authorized release, fresh resident-dispatcher heartbeat and durable handoff receipts.

## Metrics, with verifiable denominators

**Primary:** `Verified Model Recovery Rate = independently production-verified restored model incidents / model incidents requiring restoration`. Report a declared cohort/date window and count each canonical incident once; do not count source candidates, duplicates or merely merged code. Publish numerator and denominator beside percentage. Separate open/unverified incidents from formally closed non-restoration dispositions.

**Supporting:** median/p90 detection-to-production-restoration time (restored cohort only); age of the oldest blocked/unverified repair; recurrence within 7/30 days after verified closure; percent of declared sport × market × pregame/live capabilities restored with a real Action acceptance receipt; and actionable unowned/stalled incidents. Do not equate registered/healthy routes with publishable model coverage. Split genuine `NO_CURRENT_EVENTS_OR_MARKETS` from unverified unavailable coverage.

## Activation and ownership boundary

This is **documentation-only**, safe to review while #1562 P0 work continues. No new agent is provisioned, no protected workflow/manifest is changed, no credential or lease is granted, and no production rollout is authorized. Code wiring, automated deadlock recovery, specialist evidence handlers and dashboards are future Engineering-App work, subordinate to the release governance in #1856 and the current #1843 recovery candidate.

**Required operational decision before activation:** Production Engineering / Conductor must approve one existing writer and incident owner; Independent QA/Security/Release must approve and verify source and live behavior independently. Model Excellence alone can approve a Class C model-science change. No other human interaction or security bypass is implied.
