---
name: triple-d
description: Root-cause repair and end-to-end engineering delivery for WOW V17 incidents, blockers, stuck PRs, broken workflows, and user-visible defects. Use when the user says Triple D, fix E2E, build and fix, continue until done, or reports a stalled engineering task.
---
# WOW Triple D — DEBUG → DISSOLVE → DEPLOY

## Input
User objective; project/repo; observed failures; prior PR/issue if provided. Do not treat a historical status as a current fact.

## Phase A — DEBUG
1. Read current branch/head, issue/PR status, writer leases, protected-check requirements and active owner.
2. Trace the request's real end-to-end path, using backend logs and production/CI receipts when accessible. Separate source, identity, hydration, model, calibration, reducer, persistence, Action, publication and GPT editor failures.
3. Reproduce the symptom. Produce a failing test or exact error/receipt and timestamp. Compare recent known-good/bad revisions to pinpoint the change.
4. Classify deterministic governance failures separately from transient transport/quota faults. Do not retry a fixed author/file-policy mismatch hoping it will pass.
5. Define scope and acceptance: which user-observed operation must succeed, not merely which test must turn green.

## Phase B — DISSOLVE
1. Choose smallest fix that resolves the root cause, not an exception or suppressed assertion.
2. Work in a focused branch/worktree. Preserve active leases, unrelated modifications and old successful cases.
3. Add a red-to-green regression test; include fail-closed, no-fabrication, and opposite/nearby-case tests as relevant.
4. Run focused checks and broader impacted suites. State environment: unit sandbox vs CI vs live production.
5. Diagnose each red check by exact log and head SHA. Fix source/setup; do not delete tests, grant broader auth, or bypass reviewer policy.
6. Provider/tool failure: use an already authorized secondary provider or alternate documented read/write path if available. Do not create new costs, invent receipts, or misclassify capacity as model absence.

## Phase C — DEPLOY AND VERIFY
1. Prepare PR with root-cause statement, minimal diff, regression evidence, exact-head SHA and acceptance criteria.
2. Obtain external independent QA/security/semantic acceptance and release authorization where required. Claude's own adversarial reviewer is useful but **not independent sign-off**.
3. Merge/deploy only through established authorized protected process. Never self-approve, force-push, or write direct to main.
4. Verify new deployed revision, actual request/response, associated persistence/receipt and downstream consumer. Verify two cycles where the incident's acceptance contract requires two.
5. Reconcile incident and queue once; classify `COMPLETE` only with receipts. When blocked by human authorization, give the exact requested action and continue a different eligible item.

## Anti-loop rule
If two consecutive attempts make no new evidence or state change, stop retrying and change the diagnostic approach, hand off the precise block, or move to another eligible task. Never produce hour-after-hour status-only updates as engineering progress.

## Required closeout
`ID | root cause | fix | branch/PR/SHA | failing→passing test | exact-head CI | independent acceptance | deployed SHA | live receipt | status | remaining owner action`.

## Exact CI failure classification
Before changing app code for a failed check, record its actual category: `APPLICATION_REGRESSION`, `CI_ENVIRONMENT_GAP`, `DEPENDENCY_VERSION_DRIFT`, `DATABASE_SCHEMA_BOOTSTRAP_GAP`, `MISSING_EXTERNAL_SERVICE_BY_DESIGN`, `PREEXISTING_TEST_FAILURE`, or `UNKNOWN`. Log affected SHA, test command and fixture. Resolve cause rather than weakening validation or retrying an immutable policy denial.
