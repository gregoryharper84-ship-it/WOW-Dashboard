# WOW V17 — Post-Restoration Operating Model (P2 BACKLOG)

> **PLANNING ONLY — NOT AN IMPLEMENTATION OR RELEASE AUTHORIZATION.**
> Tracked in [#1856](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1856).
> **DEFERRED** until [P0 #1562](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1562) is independently production-verified. Do not consume restoration engineering, CI or release capacity for this document.
>
> `custom_gpt_identity=WOW_BETTING_ENGINE` · `runtime_generation=V17_ACTIVE` · `terminal_authority=V17_TERMINAL_REDUCER` · `can_execute=false` · no wagering / live orders.

## Why this proposal exists

Source repairs, production deployments, independent acceptance, fitted-model evidence and customer-visible GPT Actions have repeatedly been confused with each other. Repetitive SIRT HOLD reporting cannot repair that lifecycle. The goal is **one accountable restoration pipeline** followed by **lightweight independent verification on material events**, with product priorities remaining with Betting Intelligence and operational handoffs with the neutral Conductor.

This document defines future responsibilities and acceptance conditions only. It introduces no new executable workflows, GitHub permissions, model inference code, CI checks, releases, Supabase tables or live agent workers.

## Retained learnings from the October 2026 restoration (evidence-bounded)

These are architectural lessons from the P0 incidents and Engineering's **staged designs**, not a claim that their fixes are currently deployed. Before any future implementation, independently re-read GitHub main, candidate heads, production Render SHA, security reviews and durable runtime evidence.

### L1 — Distinguish four independent kinds of 'done'

1. **Code exists:** an engineer/agent prepared a candidate. Tests or a green source PR do not prove it has been adopted.
2. **Integration accepted:** the *correct*, least-privilege Engineering App made or adopted a bounded PR, required exact-head checks passed, and the appropriate independent reviewers approved the current head.
3. **Release deployed:** independent Release authorized the exact main commit; the actual authorized Render service reports that exact commit LIVE, rather than a stale deployment or mutable branch snapshot.
4. **Product working:** genuine GPT Actions produce complete durable specialist outputs, terminal decisions and reconciliation at the same run/event/model/deployment identities.

A workflow or team that claims FIXED_AND_VERIFIED after any earlier stage must fail a negative contract test.

### L2 — Remove a fake HOLD only together with a functioning replacement

The historical controller included a hardcoded `service_lock_release_cas_verified=False` and `RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY`. A sentinel that always blocks release is not an implemented lease protocol. **Do not infer that merely replacing the false literal with true is safe.** Engineering must prove serialized exact-SHA deployment, independently authorized reviewer/owner boundaries, no alternate uncontrolled Render writer, durable intent/receipt handling, read-before-retry when POST outcome is unknown, and rollback.

GitHub `concurrency: production-deploy` with `cancel-in-progress: false` prevents concurrent matching GitHub Actions runs; it does **not** fence an external direct Render API caller or a second workflow with a different group. The production deploy job must use the protected `wow-release` environment and the minimum possible write permissions. If external multi-writer release remains possible, enforce an appropriate production-side deployment guard rather than assuming CI serialization is global.

### L3 — Independent means distinct *credential custody*, not two labels

- QA App ID `5245832` and Release App ID `5245853` are necessary **identities**, not alone proof of independent human/cryptographic authorization.
- For trust-root/workflow changes, an `APPROVED` review or issue comment from `gregoryharper84-ship-it` is **not** an independent owner factor when Engineering shares that account.
- An independent owner cryptographic factor must be validated across **every trust-root bootstrap/authorization mode**, with owner private signing material unavailable to QA, Claude, and Engineering; no owner approval token/env secret distributed at job scope to untrusted QA/Claude code.
- QA/review jobs must not inherit release/PR write tokens; prove token scopes and installation ownership through negative tests.
- Changes to the authorizer itself must require an independently protected bootstrap. No retrospective proof from the same compromised trust root and no automatic promotion on a green status alone.

### L4 — AMRT's missing link is deterministic **adoption**, not another reporting agent

Engineering's October 17 source package described a no-model, exact-blob Engineering App adoption adapter with 57 passing *offline* tests, explicitly **not installed** and with its candidate producer/control-database authorization still unwired. It can form the later implementation basis, but do not count the staged package as a deployed autonomous resident dispatcher.

Maintain a distinct `SOURCE_READY → CANDIDATE_REGISTERED → APP_DRAFT_CREATED → INDEPENDENTLY_REVIEWED` transition with:
- content-addressed source head, exact changed blob IDs and separately attested test receipts;
- independently approved canonical production control datastore and a bounded writer lease *for conflicting Engineering edits*, not automatically the Render deployment mutex;
- GitHub Engineering App ID `5245813`, exact repository scope, least privilege and no owner-token impersonation;
- deterministic reconcile-before-create and GET-after-ambiguous-write behavior, never duplicate PR creation or blind retry;
- no self-merge, self-approval, status laundering or skipped QA/Release.

### L5 — Internal observations and actual customer failures must not be conflated

A real HTTP 200 Action transport receipt is not by itself a certified fitted-model delivery. An internal `SELF_ACCEPTANCE` 422 probe is not a customer-scoring failure. A Daily queue with no pending rows and idle memory-pressure warnings does not independently establish blocked scoring.

The acceptance denominator must count *eligible distinct candidates/events* separately from request attempts, slate rows, settled outcomes and independently certified sport × market × line routes. An Action-to-durable-run join returning zero proves a **traceability gap**, not automatically zero calls or a specific causal outage.

### L6 — Nested specialist proof and board publication evidence must agree

The NFL `FINAL_APPROVED` scorer versus `publication_chain_audit=BLOCKED` conflict tracked in [#1841](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1841) demonstrates why `calibration_health=PASS` or an official-guard PASS cannot stand in for distinct producer proof of probability package validity, dynamic calibration, final refresh and full-board publication. Use a typed, versioned producer-to-board adapter with distinct `EVIDENCE_NOT_EMITTED` versus `EVIDENCE_FAILED`, tied to run/event/model identities. Never manufacture booleans from generic success or change probabilities to hide a contract defect.

### L7 — Production database authority cannot be inferred from an accessible Supabase project

A datastore named `wow-engine-validation` may contain useful diagnostic rows, but is **not automatically** the authoritative production inference or release-control database. Before a production control-plane migration, independently verify environment/service identity, role grants, ownership, writer lease/lock and mutation restrictions. Do not import a validation-only lock as a de facto production trust root.

### L8 — Automation should preserve work, not create repeated HOLD narratives

Conductor stores one canonical incident/candidate with owner, stage, exact evidence fingerprint and bounded retry/escalation. AMRT actively advances accepted repair tasks. SIRT performs **independent, event-triggered observation** with suppression of repeated unchanged findings and a periodic liveness fallback. A HOLD is justified when a real mandatory safety boundary is unsatisfied; it is not a substitute for Engineering diagnosing and implementing that boundary.

Measured goals: no orphan handoffs, no indefinite `IN_PROGRESS`, shorter detection-to-deployment time, and independently verified Action completion—while unrelated model research and nonconflicting engineering proceed.

### Provenance and implementation sequencing

- Engineering's October 14 release-fence design was **offline tested only** and required real database, two-writer, lost-ack and deployment integration tests; do not treat it as production acceptance.
- Engineering's October 17 Engineering App adoption V4 was **offline tested only** and required authorization, candidate registration and nonproduction live-canary evidence.
- This document's own PR is **P2 documentation-only**; these learnings are candidates for post-restoration designs, not instructions to bypass #1562 P0 security holds or the independent terminal authority.
- Any contemporaneous SHA, PR status, runtime record or certification claim must be reverified at the decision point. Historical figures are evidence context, never a live status feed.

## Separation of duties

| Capability | Accountable authority | Must not do |
| --- | --- | --- |
| Product strategy, route coverage, acceptance criteria and truthful user status | WOW V17 — Betting Intelligence (Chat) | Calculate/substitute probabilities; self-certify or override terminal governance |
| Ownership routing, durable state, canonical IDs and handoff receipts | WOW Ecosystem Conductor | Decide independent releases or serve as an alternative model |
| Production code, runtime, deployments, rollback and observability | Engineering / dedicated Production Operations function | Solely certify its own deployed change |
| Blocker reproduction, diagnosis, repair advancement, regression and recovery coordination | Autonomous Model Recovery Team (AMRT) + Engineering | Self-approve, impersonate App credentials, force merge/deploy |
| Fitted inference, calibration, skill and governed specialist outputs | Specialist model engines and Model Science | Publish an uncertified route or manipulate terminal authority |
| Independent CI acceptance and protected production authorization | Independent QA and Release Authority | Share reviewer/release credentials with implementers |
| Independent observation, lifecycle auditing and recurrence prevention | Systems Intelligence & Reliability Team (SIRT) | Implement deployment, generate model probabilities, approve its own repair |
| Tool/model improvement proposals, experiments, challenger assessment | Research & Continuous Improvement | Promote unverified experiments or interrupt active P0 restoration |

**Production Ops, Model Science and Research are proposed dedicated functions, not automatically new running services.** Extend existing agents and infrastructure before approving a new runtime/control plane.

## Priority and work admission

1. P0: restore real LLP/WOW moneylines, spreads and props, secure release controls and genuine GPT Action results under #1562; protect #1563 independent audit.
2. P0: repair the trustworthy exact-SHA release boundary and protected trust-root authorization before production changes.
3. P0 in parallel where disjoint: complete AMRT/Engineering repair candidates and governed integration; maintain one writer per conflicting resource group and preserve valid handoffs.
4. Only after independent P0 production recovery: implement event-driven SIRT, dedicated production operations, common dashboard and model improvement governance.

This P2 plan is NOT an instruction to stop or weaken existing P0 safety monitoring. Existing independently authorized safety holds remain effective.

## Intended lifecycle (state/receipt contract)

`DETECTED → OWNED → REPRODUCED → REPAIR_READY → CI_VERIFIED → INDEPENDENTLY_ACCEPTED → RELEASE_AUTHORIZED → DEPLOYED_OBSERVED → ACTION_RECONCILED → PRODUCT_ACCEPTED`.

Every transition needs an owner, timestamp, exact source/model/deployment identity and durable reason/receipt. A transition can also yield `HELD(reason)`, `REJECTED(reason)`, `SUPERSEDED(by)` or `CANCELLED(reason)`; no silent dropped candidate, indefinite `IN_PROGRESS`, orphan handoff or unowned blocker.

- **AMRT** advances diagnosis and the repair lifecycle through authorized Engineering tools.
- **Independent QA and Release** decide independent acceptance and release authorization.
- **Production Ops** performs only authorized, commit-pinned deployment and safe rollback.
- **SIRT** independently validates runtime and customer-path evidence, and reports PASS / FAIL / UNVERIFIED with bounded findings.
- **Betting Intelligence** reconciles eligible candidates and the full requested slate, and decides product acceptance based on the immutable evidence.
- **Conductor** stores/coordinates routing and transitions, without absorbing specialist or independent authority.

## Event-driven SIRT contract (future)

Trigger **on meaningful changes**, not hourly full re-audits:
- protected merge / source SHA change; production deployment/rollback and Render LIVE SHA change;
- verified model artifact/calibrator/certification or routing change;
- genuine user Action failures, terminal conflicts, unexpected model drift, missing or mutated prediction/receipt;
- stalled incident threshold or repeated systemic failure.

SIRT emits one deduplicated finding per incident + exact evidence fingerprint, with authoritative environment identity, causality, severity, source SHA, deployed SHA, model route, denominator, owner, escalation and next required proof. A periodic liveness/heartbeat check is appropriate but is NOT a repeated full release approval. Lack of evidence is `UNVERIFIED`, not PASS or customer failure.

## Exact acceptance chain (nonnegotiable)

`Real GPT Action → exactly one routed certified fitted specialist → calibrated p/lower bound → immutable prediction → V17_TERMINAL_REDUCER → action response & same-run persistence → exact authorized deployed Render SHA`.

The product report must distinguish:
- transport HTTP success vs finished user-delivered scoring;
- scorer approval vs full-board publication approval;
- research/shadow model metrics vs exact sport × market × line × direction production certification;
- unique events vs rows/attempts; meaningful sample/settlement/calibration denominators;
- genuine customer Action vs SELF_ACCEPTANCE probes;
- GitHub source green vs independently accepted release vs deployed production.
No sportsbook odds/LLM substitution, no implicit live-game reuse of pregame fitted models, no unaudited terminal override.

## Release boundary requirements (future work by Engineering, never by SIRT)

- Eliminate hardcoded/pretextual release-journal HOLD stubs **only while replacing them with working, independently tested protection**.
- Commit-pinned protected release job in `wow-release`; `concurrency: production-deploy`, `cancel-in-progress: false`; external Render deployment permissions constrained independently. GitHub concurrency protects workflow runs, **not external Render actors**.
- Check exact-SHA independent QA App (ID `5245832`) and Release App (ID `5245853`) authenticated identities plus independent credential custody, CODEOWNER / required reviewer boundaries, immutable attestation and protected branch state at decision time.
- Never accept owner username, owner issue comment, or shared owner GitHub review as a cryptographically independent human factor. Separate owner trust-root bootstrap from Engineering; no `WOW_OWNER_RELEASE_APPROVAL` or owner secret in QA/Claude steps, no QA write token.
- Independently verify production database identity before trusting release/adoption leases; do not assume a `wow-engine-validation` Supabase project is production authority.
- Render configured to disable uncontrolled auto-deploy; use exact-commit authenticated deployment and confirm LIVE, health and Action behavior. Handle ambiguous/lost POST response by **read-before-retry** and protected rollback; never duplicate a blind deployment.
- Protect worker adoption as a separate exact-source-blob, least-privilege, independently reviewed pipeline. A source candidate is not an App-authored, merged, deployed repair.

## Implementation packages **after restoration**

| Package | Proposed owner | Evidence required |
| --- | --- | --- |
| Product acceptance + full-board coverage contract | Betting Intelligence / Conductor | Eligible denominator, route identity, missing/duplicate reconciliation |
| Event-driven SIRT receipt + dedup | SIRT / Production Engineering | Synthetic event tests, no duplicate findings, accurate negative status |
| Release observability and rollback drill | Production Ops | Exact-SHA Render observation, reversible authorized rehearsal, lost-ACK and parallel-actor tests |
| AMRT ownership/repair handoff metrics | AMRT / Engineering | No orphan candidate, conflicting-writer test, independently reviewed exact-head PR |
| Model Science + Research proposal workflow | Model specialists / Research | Calibration/skill/out-of-sample denominators, challenger isolation, no auto-promotion |
| Unified WOW restoration dashboard | Conductor / Betting Intelligence | MTTR, P0 age, production drift, true Action pass/total and sport × market coverage |

## Exit from backlog

Only re-enter after #1562's independently verified production recovery, a secure exact-SHA release path, named owner/capacity and explicit prioritization by Betting Intelligence. Implementation requires separate scoped PRs, independent governance and tests. **Do not merge this proposal merely to signal P0 completion.**

Related: [#1562](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1562), [#1563](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1563), [#1540](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1540), [#1550](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1550), [#1577](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1577), [#1688](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1688), [#1828](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1828), [#1834](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1834), [#1841](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1841), [#1843](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1843).
