# WOW V17 — CodeRabbit Senior Engineering Advisory

**Objective ID:** WOW-ENG-CODERABBIT-SENIOR-20261105  
**Status:** WORKING_NOT_COMPLETE until protected merge, actual current-head review, Engineering intake, and independent acceptance are observed.  
**Scope:** Advisory review of WOW-Dashboard pull requests. No new review authority, daemon, queue, database, payment, or execution pathway.  
**Accountable implementation:** Existing WOW Engineering / AMRT.  
**Independent challenge:** SIRT. **Release acceptance:** Existing independent QA, Release Authority, CODEOWNER and protected branch process.  
**Safety:** `can_execute=false`; V17_TERMINAL_REDUCER retains sole sporting terminal authority.

## Why this is an in-house engineering function

CodeRabbit is an external code-review service, not an in-house-hosted model or a new autonomous employee. WOW can incorporate its reviews into its existing engineering operating process with repository-owned configuration and attributable GitHub receipts. Do not describe a CodeRabbit comment, summary, or review as independent certification or proof of deployed behavior.

The repository root `.coderabbit.yaml` is the **single review configuration**. Use its `assertive` review profile, path-specific instructions and evidence-first tone to emulate a skeptical senior engineer's *review checklist*, not to claim human-equivalent judgment.

## Ownership and handoff

| Function | Accountability | Explicit boundary |
| --- | --- | --- |
| CodeRabbit — Senior Engineering Advisory | Review changed code for architectural/contract failures, security, concurrency/races, data/model correctness, regression gaps and evidence-quality issues | Advisory only; cannot approve PRs, set model probabilities, grant a PASS, deploy or authorize paid agent usage |
| AMRT Blocker Sentinel + Root Cause Investigator | Reproduce and classify actionable findings against exact PR/head and existing incident | No duplicate issue or uncontrolled retry |
| AMRT Code & CI / relevant Model or Data Engineer | Minimal defect repair, targeted negative and preserved-positive tests on leased branch | Exactly one writer; do not treat autofix as proven source correctness |
| SIRT | Independently challenge material security, model, integrity and reliability concerns | Never certify its own authored changes or deploy |
| QA / Release Authority / Independent Verification | Independent exact-head review, protected release, deployed SHA and production readback | No inferred receipt from CodeRabbit review or green source CI |

## Review contract for each actionable finding

Record in the **existing incident/PR** (do not create a second queue):

- Existing parent incident ID (or a new canonical incident only if truly novel), source PR and exact head SHA.
- Changed path and lines, verified reproduction or precisely labeled hypothesis, evidence and negative impact.
- Severity P0/P1/P2 with a written reason; distinguish immediate safety/data-loss/release bypass from ordinary maintainability.
- Concrete minimal remedy, failed-before/passed-after regression and nearest previously successful comparator.
- Assigned AMRT owner, writer lease reference if coding, reviewer/source receipt, and current terminal engineering state.
- Re-review after material change against the *new* head SHA. Stale review comments and CI results are not approval.

Preserve the native failure taxonomy (MODEL_INPUTS_INSUFFICIENT, MODEL_ROUTE_UNSUPPORTED, MODEL_UNAVAILABLE, MODEL_SCORER_FAILED, MODEL_OUTPUT_INVALID, Action/receipt faults, stale event, MARKET_DATA_UNOBTAINABLE). Do not rewrite a failed scorer as missing model capability or mask a real backend failure with an architectural narrative.

## Normal route — no new controller

1. Existing GitHub pull request becomes ready for review. CodeRabbit performs the configured advisory review and incremental updates.
2. The existing Engineering/AMRT workflow reads findings. Deduplicate against existing issues by defect fingerprint and exact code/PR identity. Record non-reproducible suggestions as advisory, not verified P0 bugs.
3. Diagnose the **first proven failing boundary**. Route data/integration, model, workflow/security and test findings to their existing Engineering owner.
4. Repair through the existing authorized single-writer branch and bounded provider failover. Manual CodeRabbit Fix comments / stacked PR may assist only where already enabled and free/approved; never automatically purchase agent minutes or launch paid runs.
5. Verify targeted failure and nearest successful regression; require independent SIRT and exact-head QA/Release evidence where applicable. Protected merge and separate production proof remain mandatory.
6. Reconcile the finding exactly once as fixed/independently verified, typed blocked, rejected as false positive with evidence, duplicate linked to canonical incident, or accepted noncritical debt. No silent disappearance.

## Risk-specific senior review expectations

- **Models and probabilities:** Check look-ahead leakage, immutable pregame snapshot, event/line/side identity, scorer failure taxonomy, calibrated lower bound, held/not-publishable states and cross-sport regressions. Do not suggest a model coefficient or probability change without governed chronological replay, holdout and independent model acceptance.
- **Web/API and data:** Check authentication/authorization, limits/timeouts, idempotency, exactly-once ledger reconciliation, JSON/persistence round-trip and end-user Action/readback contract.
- **Workflows and release:** Check untrusted content and secrets, source/merged/deployed SHA drift, genuine independent reviewer provenance, race/retry storms, infinite workflow triggers and fail-closed release gates.
- **Testing:** Require actual failures, negative cases, boundary conditions and previously correct paths. A passing test suite or generated summary is not independent verification.

## Acceptance evidence

This source change alone is not an active senior-review program.

1. The new configuration is merged through the protected process without changing required checks or allowing CodeRabbit approvals.
2. A ready-for-review nontrivial PR receives a real CodeRabbit review attached to its current exact head, with the intended path instruction exercised.
3. At least one specific finding (or a documented no-finding control) is read and reconciled by existing AMRT/Engineering against an issue/PR and accountable owner; tests and review outcome are recorded without invented receipts.
4. SIRT/Independent Verification checks that no new merge/release authority, credential access, paid provider, or betting/model-production behavior was added.
5. Measure eligible PR coverage, actionable confirmed findings, true-positive/false-positive ratio, time to repaired-and-independently-accepted change, recurring failures and actual spend. Missing observations are UNKNOWN, not zero or COMPLETE.

**Rollback:** revert this narrow advisory configuration if assertive reviews overwhelm actionable triage, materially increase paid usage, introduce irrelevant findings, or disrupt normal PR processing. Keep existing protected checks and independent review unchanged.
