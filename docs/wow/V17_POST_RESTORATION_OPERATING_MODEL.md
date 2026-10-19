# WOW V17 — Post-Restoration Operating Model (P2 BACKLOG)

> **PLANNING ONLY — NOT AN IMPLEMENTATION OR RELEASE AUTHORIZATION.**
> Tracked in [#1856](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1856).
> **DEFERRED** until [P0 #1562](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1562) is independently production-verified. Do not consume restoration engineering, CI or release capacity for this document.
>
> `custom_gpt_identity=WOW_BETTING_ENGINE` · `runtime_generation=V17_ACTIVE` · `terminal_authority=V17_TERMINAL_REDUCER` · `can_execute=false` · no wagering / live orders.

## Why this proposal exists

Source repairs, production deployments, independent acceptance, fitted-model evidence and customer-visible GPT Actions have repeatedly been confused with each other. Repetitive SIRT HOLD reporting cannot repair that lifecycle. The goal is **one accountable restoration pipeline** followed by **lightweight independent verification on material events**, with product priorities remaining with Betting Intelligence and operational handoffs with the neutral Conductor.

This document defines future responsibilities and acceptance conditions only. It introduces no new executable workflows, GitHub permissions, model inference code, CI checks, releases, Supabase tables or live agent workers.

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
