# WOW V17 — Resident Engineering Dispatch and SIRT Intake

Status: **Class B candidate on PR; NOT active production autonomy**.
Parent: #1021. Release/SIRT trust dependencies include #1247 and the separate
SIRT sentinel work #1513/#1514. This addition does not close those incidents.

## Existing system reused

- The resident Render Celery worker hosts the engineering auditor.
- Protected-main GitHub Actions ChatGPT/Claude workers own Lead -> Triage ->
  Engineering -> sandbox regression -> independent Review -> Architect -> QA ->
  PR creation and provider failover.
- The registered engineering dispatch manifest, plus **current GitHub issue
  open/closed state**, determines eligible engineering incidents. P0 wins.
- Existing protected merge/deploy/release verification is not replaced.

## New resident supervisor

In the resident worker process, the supervisor is a daemon launched only when
WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED=1 and governance passes. Every five
minutes it:

1. Acquires a Redis SET-NX TTL lock shared by Render replicas. If Redis cannot
   establish the lease, no dispatch occurs.
2. Holds during the 15-minute post-dispatch cooldown.
3. Enumerates queued/in-progress/waiting/requested GitHub Actions runs. A
   partial inventory fails closed, and a live engineering worker is never
   duplicated.
4. Selects the first still-open approved incident from the dispatch manifest.
   It does not invent or prioritize a new task from an LLM summary.
5. Holds rather than duplicating work if a matching open engineering PR already
   exists. That PR must go through review, fix/retry and SIRT/IV handoff.
6. Stops after three resident dispatches of the same still-open incident in
   a 24-hour window, surfacing a typed triage-needed hold instead of looping.
7. Dispatches the **existing** protected-main provider-dispatcher workflow,
   passing exact P0 incident/domain lease when required.
8. Logs a compact typed supervisor outcome, never a credential value.

The current GitHub Actions schedule and workflow-run continuations remain
present as secondary triggers. GitHub writer concurrency and existing worker
lease gates still apply. Redis is an admission-control lease, not review
authority, merge permission or a durable ticket execution receipt.

## SIRT handoff

On completion of a trusted protected-main engineering worker workflow,
the independent SIRT-intake workflow correlates the worker run identity to its
exact newly created PR branch. If a PR exists with pre-PR engineering PASS
metadata and an exact head SHA, it opens a deduplicated independent SIRT
intake issue and comments on the PR. The issue requires SIRT independent
assessment followed by **separate** Independent Verification and Release proof.

PR-body metadata is an **intake signal only**, not trusted approval evidence.
It is never used to set a passing merge/release status. Exact-head CI, trusted
review receipts and production acceptance remain mandatory under independent
protected controls. If the source worker created no PR or its proof is missing,
no SIRT PASS is invented. SIRT findings must route back to the same Engineering
incident and PR for bounded repair rather than opening duplicate implementations.

## Activation checklist (external approved promotion)

- [ ] Review exact PR head; required CI and adjacent lifecycle tests green.
- [ ] SIRT checks worker/lease race, reviewer independence, non-execution.
- [ ] Independent Verification confirms current ruleset/check requirements.
- [ ] Configure WOW_ENGINEERING_GITHUB_TOKEN securely on the worker; scoped
      only to this repo (Actions read/write for workflow dispatch; Issues and
      Pull Requests read; metadata read). Never print or commit the token.
- [ ] Confirm REDIS_URL is connected and only one GLOBAL mutation owner is
      permitted by the protected work controller.
- [ ] Merge only through protected review, then deploy exact approved SHA.
- [ ] Set WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED=1 through approved
      Render configuration **after** the readiness checks above.
- [ ] Verify two unattended cycles on real GitHub/Render/Supabase: initial
      dispatch and subsequent follow-up, non-duplicated issue/PR handoff,
      durable SIRT intake, worker heartbeat, and exact negative cases.
- [ ] Verify failure/timeout/restart, queue empty, duplicate PR, missing token,
      missing Redis, and in-flight workflow all fail closed.
- [ ] Preserve explicit independent production acceptance; not even
      two green supervisor cycles constitutes FIXED_AND_VERIFIED.

## Important remaining limitations

This supervisor is deliberately **not** the general-purpose agent writer.
The current worker supports bounded fix/test/PR creation. A separate
evidence-driven existing-PR remediation path is still required for automated
CI-review failure repairs and pending SIRT change requests. Until that path
has trusted receipts and tests, an existing PR makes the supervisor HOLD;
it will not manufacture another PR. CI/SIRT repair loop completion and
production activation remain independently verifiable gates.

V17_TERMINAL_REDUCER remains the sole sporting terminal authority.
can_execute=false. No wagering, market orders, fitted-model or calibration
changes, branch-protection bypass, or self-approval.
