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

## 2026-10-28 close-and-drain correction

The staged migration now persists immutable `(incident_id,epoch,owner,group)`
closure records. The existing CLI accepts `--operation CLOSE_EPOCH --receipt
<canonical receipt>` only in its existing protected worker context. It closes
admission without writing GitHub or releasing the lease. `DRAINING` exits
nonzero and preserves pending-intent reconciliation; only exact `DRAINED`
permits proceeding to the separately authenticated legacy release. New intent
IDs are rejected after closure, including after renewal; pending/confirmed IDs
are readback-only. Legacy release/takeover requires closure, no pending writes,
and an increased epoch on the next HELD ownership. Lease deletion and closure
mutation are denied. Nullable SQL authorization uses explicit fail-closed tests.

Read-only inspection of `iczfhsmjrrafhvcpmqhr` confirmed the legacy table
columns and claim/release/renew function definitions. It also showed existing
`service_role` DELETE/TRUNCATE/TRIGGER/REFERENCES privileges on the lease
table. The candidate explicitly revokes those privileges on all protocol
tables and UPDATE on closure records, including inherited default grants.
**service_role still has trusted intent UPDATE authority**; this is not an
RPC-only security boundary. Keeping that credential out of providers and all
untrusted code is a mandatory cutover requirement. Database administrators
remain privileged; these triggers do not claim to constrain the table owner.

### Exact remaining activation work

| Existing route | Observed source path | Required cutover evidence |
|---|---|---|
| Owner-source adoption | `engineering_authorized_adoption.py::integrate` uses direct App push/PR creation | Same canonical lease/incident binding, gateway invocation, token available only to trusted writer |
| Provider launch/recovery | `wow-v17-engineering-provider-dispatcher.yml`, `engineering_remote_fallback.py` | Provider artifacts/read-only access; revoke direct repository mutation permission, prove stale run is denied |
| Cursor finalization | `engineering_remote_writer_release.py`, source #2198/App #2217 | Close canonical epoch, drain/reconcile, authenticate provider terminal evidence, then release and project ledger state |
| Protected release | Existing QA/Release/merge and Render workflows | Genuine distinct-actor review, fresh exact-head checks/approval, deployed web+worker SHA and negative production canary |

The current owner-source increment prepares the protocol and its test adapter;
none of these live routes is switched by this patch. Do not apply SQL alone:
legacy releases would now deliberately fail until their worker closes the
canonical epoch. Preserve the Cursor hold until the complete attested cutover.

### Verification boundaries

- Existing offline gateway/CLI regressions include exact close identity,
  malformed responses, no GitHub mutation during close, and denied closure.
- `test_v17_writer_epoch_postgres.py` executes the migration in the existing
  ephemeral local PostgreSQL proof job. It tests real two-connection ordering,
  pending recovery, expired ownership, epoch reuse, NULL inputs, role grants,
  deletion and closure immutability. Normal suites skip it without the explicit
  isolated DSN; a skipped test is never concurrency evidence.
- Local PostgreSQL WASM execution can validate SQL syntax and sequential
  behavior, but cannot certify native multi-connection races or live grants.
- Fresh assistant review is advisory code review, not signed Security,
  CODEOWNER, QA App or Release App approval. No existing approval transfers.
