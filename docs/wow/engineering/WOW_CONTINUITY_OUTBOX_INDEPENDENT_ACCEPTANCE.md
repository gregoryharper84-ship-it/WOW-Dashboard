# WOW V17 — Continuous Dispatch Outbox: Independent Acceptance Contract

**STATUS: REVIEW-ONLY / NO PRODUCTION MIGRATION OR ACTIVATION**

Candidate files: \`docs/wow/engineering/proposals/WOW_CONTINUITY_OUTBOX_REVIEW_ONLY.sql\`,
\`v17/engineering_continuity_outbox*.py\`, \`v17/engineering_continuity_reconcile.py\`.
Production on October 15 did **not** contain the proposed continuity outbox table or RPCs.
The separate writer-lease schema/functions already existed. Do not reapply those migrations.
Only a trusted Engineering App principal may dispatch or write. No developer agent
is the independent owner, QA App, Release App, or the governed terminal reducer.

## Phase A — artifact and database provenance (stop on failure)

1. Pin the exact reviewed commit. Identify the canonical Supabase project and
   approved migration owner from the protected runtime config. No guessed DB,
   schema, owner, or credential. Reject a service-role secret supplied by PR
   text, issue comments, user messages or untrusted model output.
2. DBA compares review-only SQL to **live** schema and versioned migrations.
   Generate a canonical migration under the approved DB change process; this
   file is intentionally not an executable production migration.
3. In a disposable PostgreSQL database with the same security-role and RLS
   configuration, apply the migration **once**, then attempt replay. Assert one
   table, five RPCs (reserve, abort, reclaim, arm, mark), one partial unique
   active-incident index, and no public/anon/authenticated EXECUTE or table
   mutation. A fresh service-role identity is required. Audit all data changes.
4. Verify claim/check/renew/release RPC signatures against the installed
   writer lease implementation. The EXISTING lease RPCs are SECURITY INVOKER.
   NEW outbox RPCs must instead be SECURITY DEFINER owned by a separately
   DB-admin-certified restricted principal, with fixed search_path and
   exclusively allowlisted EXECUTE grants. No default PUBLIC execute and
   no direct service_role DML grants for the outbox table.
5. As negative tests verify direct INSERT/UPDATE/DELETE are denied for anon,
   authenticated and service_role, while independently authenticated trusted
   calls to reserve/abort/reclaim/arm/mark have the intended fenced behavior.
   Never put broad service-role credentials in AI worker prompts, Custom GPT
   Actions, pull requests or logs. Production restricted identity remains a gate.
## Phase B — two-session serializable behavior

The live lease table has one row per incident with a `lease_group` column
but no unique active-group constraint. The staged outbox derives that group only
from its row-locked lease record and adds a partial active-group unique index.
This stops duplicate **dispatch intents**, but does not by itself authorize
or serialize other GitHub code writers that bypass the outbox. All writer
entrypoints need independent group-wide admission before production.

Use **two independent database transactions and two independent trusted worker
identities**, not an in-memory fake or sequential API requests. Report exact
transaction/result IDs and successful/failing assertions:

| Case | Required observed behavior |
|---|---|
| Two concurrent reserve calls, same incident | Exactly one RESERVED active intent; loser IN_FLIGHT |
| Two different incidents, same certified lease_group | Exactly one active outbox intent; loser IN_FLIGHT, with both group values DB-derived |
| Different incidents, disjoint certified lease_group | Independent active outbox intents allowed |
| Lease takeover racing reserve/arm/abort/reclaim/mark | Lease row FOR UPDATE serializes takeover with outbox mutation; stale epoch may never alter intent |
| Two concurrent arm calls, same UUID | Exactly one true; second false; no duplicate provider POST |
| Pre-arm stale main/PR | Exact active-owner/epoch abort succeeds only while state=RESERVED; history remains |
| Abort vs arm racing | Exactly one wins the row; abort never changes ARMED into PREARM_ABORTED |
| Crash before arm then lease expiry/takeover | New valid epoch > old may reclaim still-RESERVED UUID; no provider POST; new UUID may reserve |
| Reclaim with old epoch, wrong UUID, wrong incident | false; immutable row remains |
| Reclaim vs delayed arm | Row lock/CAS serializes; cannot authorize both arm and reclaim |
| Attempt after arm / 204 / transport timeout | Outbox remains held; never automatically reclaim, reset or POST again |
| Lease takeover after arm | No new active intent until independently certified terminal transition |
| Crash between POST and database mark | No retry from uncertainty; preserve existing ARMED row |
| anon / authenticated direct RPC & table write | Denied; no direct outbox table writes |
| service_role direct outbox table UPDATE or INSERT | Denied; only vetted SECURITY DEFINER RPCs perform transitions |
| function owner / search_path / EXECUTE permission drift | Fail closed; no unreviewed role or PUBLIC access |
| Duplicate migration / corrupt SQL | Reject or cleanly no-op by reviewed idempotency contract; no duplicate functions or grants |

**Disqualifiers:** test-only DB, skipped race assertions, unit mocks called real DB
acceptance, a 204 used as correlated GitHub run proof, a missing artifact treated
as no-dispatch proof, a plain GitHub comment treated as author identity, expired
lease treated as sufficient by itself, or a role mismatch masked by retries.

## Phase C — authenticated end-to-end dispatch evidence

1. Engineering App session independently obtains the per-incident DB writer
   lease (under the approved restricted runtime identity) and only then calls
   the outbox. The worker uses the *recorded immutable* dispatch UUID, exact
   protected-main SHA, and optional exact PR head. A proposal is never its own
   authorization.
2. The protected workflow accepts intent identifiers only from an authenticated
   dispatcher. It writes an immutable same-run artifact containing incident,
   UUID, main SHA, action, and exact PR identity, read through independently
   authenticated GitHub API, never from an issue/PR comment.
3. The reconciler sees real workflow run ID, allowed workflow path, main branch,
   exact SHA, completed state, and matching artifact. **Even all of those mean
   only a candidate for further independent closure; not automatic retry.**
4. A separate DBA-reviewed CAS terminal/history transition, not in this PR,
   must decide whether the prior run truly completed with no unresolved
   mutation. Do not "prove" an absence of dispatch from a missing run or artifact.
5. Exercise two unattended cycles against an isolated test incident. Verify a
   restart between reservation and arm, a restart immediately after arm, a
   dropped POST ACK, an actual duplicate-dispatch rejection, and one legitimate
   later repair after independent terminal closure.

## Phase D — trust-root and production admission

- \`#1739\` was missing from the protected \`engineering_dispatch_manifest.json\`.
  Independent Engineering App must propose exact routing admission, or certify
  a protected #1688 parent route retaining original incident identity.
- Owner-authored \`#1743\` cannot pass \`QA_TRUST_ROOT_OWNER_AUTHORED\`. Resolve
  its workflow/CODEOWNERS conflicts on current main in a **new Engineering
  App-authored** PR. No self-approval or protection weakening.
- Existing Engineering App PR \`#1750\` has a known ambiguous Render POST
  duplicate-deployment risk; do not approve or merge its current revision.
- For protected files: owner CODEOWNER exact-head approval followed by
  independently source-pinned QA App and Release App checks on the same head.
  A changed SHA invalidates the earlier review.
- Merge with the approved protected release path only; deploy exact current
  protected SHA to BOTH backend and background worker; verify Render readback
  and true production Action journey. Merely passing CI or a Render health probe
  cannot establish full continuous engineering acceptance.

## Release disposition

Before all four phases pass: \`HOLD_NOT_CERTIFIED\`. Never activate the
resident dispatch flag, execute a blind redeploy, or use operator ownership as
a substitute for independent authorization. Keep \`can_execute=false\` and
\`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true\`.
