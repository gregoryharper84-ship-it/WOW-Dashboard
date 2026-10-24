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
   passing the exact approved incident for both P0 and P1. P0 requires a
   manifest domain lease; P1 is pinned to its exact issue under the existing
   GLOBAL single-writer lease. A generic P1 invocation is prohibited because
   it could reselect a pending-PR or retry-capped incident.
8. Before each cycle, writes a separate fail-closed resident-dispatcher heartbeat to
   the protected Supabase `wow_engineering_auditor_runtime` table under
   `WOW_ENGINEERING_RESIDENT_DISPATCHER` (not the auditor's row). If the
   PRE-DISPATCH heartbeat fails to persist, no workflow is dispatched.
   Each completion/typed hold updates status and outcome; missing or stale
   records never prove successful repair.
9. Logs a compact typed supervisor outcome, never a credential value.

## Protected exact-P1 dependency and safe rollout

Protected workflow modifications were separated into **bootstrap PR #1531**.
This PR #1525 deliberately restores the ChatGPT, Claude and provider
dispatcher workflow sources to canonical main. Their current protected-main
versions accept exact **P0 RAPID** targets only; they do **not** independently
support exact **P1 STANDARD** GLOBAL targets. The resident therefore refuses
to dispatch a P1 candidate by default, emitting
`P1_EXACT_WORKER_BOOTSTRAP_REQUIRED` and persisting a DEGRADED heartbeat.
It still attempts independently eligible P0 work rather than silently
reselecting the blocked P1 incident.

Only **after** trusted independent worker/bootstrap certification and
the protected merge of #1531 may Release explicitly enable
`WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED=1` on the Render worker.
This is a deployment-controlled authorization flag, not proof that the
bootstrap exists. Release/QA must verify exact deployed main workflow
revision, source integrity and two unattended P1 runs. Other values
(including an unset or `yes` flag) remain fail-closed. Do not enable the
flag based on this PR or untrusted claims.

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
- [ ] Verify trust-root bootstrap PR #1531 was separately and independently
      approved and protected-merged before enabling exact P1 dispatch.
- [ ] Configure WOW_ENGINEERING_GITHUB_TOKEN securely on the worker; scoped
      only to this repo (Actions read/write for workflow dispatch; Issues and
      Pull Requests read; metadata read). Never print or commit the token.
- [ ] Confirm REDIS_URL is connected and only one GLOBAL mutation owner is
      permitted by the protected work controller.
- [ ] Confirm SUPABASE_URL and one service-role credential exist only as
      worker secrets and the separate resident heartbeat can persist without
      modifying the WOW_ENGINEERING_AUDITOR row. A missing credential blocks
      supervisor activation.
- [ ] Merge only through protected review, then deploy exact approved SHA.
- [ ] Set WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED=1 through approved
      Render configuration **after** the readiness checks above.
- [ ] After exact approved worker deployment and resident flag enablement,
      set the independent GitHub Actions repository variable
      `WOW_SIRT_REQUIRE_ENGINEERING_RESIDENT_HEARTBEAT=1`. Independently
      verify SIRT fails on missing, stale (>15 min), DEGRADED/STOPPED or
      governance-invalid dispatcher records. Setting this variable while the
      dispatcher is not activated intentionally makes SIRT fail closed.
      The repository variable is not itself a production-deploy receipt.
- [ ] Verify two unattended cycles on real GitHub/Render/Supabase: initial
      dispatch and subsequent follow-up, non-duplicated issue/PR handoff,
      durable SIRT intake, worker heartbeat, and exact negative cases.
- [ ] Verify P0 domain target and P1 exact GLOBAL target on both OpenAI and
      Anthropic workers, including wrong-lease and unsupported-severity rejection.
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
