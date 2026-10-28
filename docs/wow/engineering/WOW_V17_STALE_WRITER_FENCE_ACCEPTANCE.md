# #1739 / #2136 — End-to-end stale writer fencing (protected candidate)

**Status: source candidate only; NOT released or production verified.**

## Root cause and immutable safety invariants

GitHub Actions concurrency is only a scheduler. The #1138 issue-comment hold
ledger is telemetry, not a transactional lease. An external provider or manual
session with GitHub write credentials can bypass either. The V17 Supabase
validation database currently has an atomic per-incident lease, but not verified
fleet-wide write fencing; incident_id uniqueness is not lease_group exclusion.

Keep custom_gpt_identity=WOW_BETTING_ENGINE,
runtime_generation=V17_ACTIVE, terminal_authority=V17_TERMINAL_REDUCER,
can_execute=false, DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.
No betting orders, probability semantics, self-approval, or reduced protection.

## Minimal integration of existing control plane

1. **One authority:** review and apply the companion SQL to the designated
   production lease database only after credential/project identity attestation.
   Do not create a second queue. The existing lease epoch stays monotonic.
2. **One narrow writer:** the authenticated Engineering App gateway holds
   GitHub mutation permission; Cursor, Claude, OpenAI, Copilot, Gemini, Flint,
   manual sessions and Render workers receive read-only access or produce
   source artifacts for it. Remove other protected source-write credentials and
   inventory direct token/branch/PR routes. Credential restriction is a hard
   deployment prerequisite; calling a Python gate() preflight is NOT enough.
3. **Atomic reservation:** the gateway binds incident, group, owner, epoch,
   operation, request UUID, epoch-qualified branch, exact SHA and payload digest
   via wow_engineering_begin_writer_mutation. It must not send a GitHub POST
   until the result is STARTED. An existing PENDING means reconcile-only,
   never resend. CONFIRMED means readback-only.
4. **Do not release while in flight:** DB trigger prevents relinquishing or
   reassigning a lease with a PENDING external mutation. GitHub API and DB are
   not a distributed transaction; if GitHub returns an ambiguous timeout,
   preserve PENDING and recover by exact authoritative GitHub readback.
5. **Separate read probes:** terminal-run verification remains read-only and
   never takes the mutation lease. The #1138 ledger remains a projection of
   canonical state, not a second control authority.
6. **Bounded recovery:** unmatched pending intents need durable owned re-entry
   and authenticated negative proof plus explicit governed resolution; never
   automatically clear an unknown in-flight request to escape a blocker.

## Independent acceptance matrix

- Same incident: two simultaneous claims; a paused stale Cursor writer versus
  a new Claude epoch; late finalizer after renewed ownership.
- Same GLOBAL group: different incidents cannot write simultaneously;
  truly disjoint P0_* domains may write independently.
- A provider still carrying a raw GitHub token must be unable to mutate the
  protected branch/PR. Test platform permissions, not merely internal mocks.
- CREATE_BRANCH and CREATE_PR: immutable identity, head/body readback,
  one accepted effect; repeat same UUID is idempotent.
- GitHub success + lost ACK, delayed POST, branch-head drift, DB outage,
  cancelled runner and payload mismatch; no stale mutation or dropped incident.
- Existing legacy claim/renew/release RPCs must respect pending transaction
  fence, group conflicts and owner/epoch; no alternate RPC bypass.
- Genuine independent CODEOWNER/security + QA + Release exact-head approval,
  reviewed migration, protected merge, production endpoint and worker SHA,
  job/reconciliation receipts, SIRT negative production canary.

## Evidence and classification

engineering_fenced_writer.py is a token-isolated runner library, not a
provider-exposed proxy. Its unit tests are **offline**; they cannot prove
GitHub Apps permission restrictions, Supabase concurrency, or live outcome.
The SQL is deliberately REVIEW_ONLY and must not run against production
without independent review. Source PR is PR_CREATED, never FIXED_AND_VERIFIED
until deployment and adversarial acceptance are recorded.
