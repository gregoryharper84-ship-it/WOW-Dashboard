# Durable writer epoch closure

Approved direction: complete one enforced Engineering App mutation boundary,
close admission before release, reconcile pending writes, restrict provider
credentials, and preserve independent exact-head release gates.

## Evidence and scope

Source #2188 currently fences pending mutations but has no irreversible epoch
closure. Production adoption still uses direct App writes. Supabase read-only
inspection on 2026-10-28 confirms legacy claim increments epoch; release and
renew update the same canonical row. No staged mutation migration is deployed.

This increment adds durable close-and-drain to the existing staged gateway and
real isolated PostgreSQL acceptance in the existing proof workflow. It does not
activate the migration, change production credentials, or claim universal
enforcement. Those require actual independent security/database review and
attested credential cutover. Existing #2215/#2217 approvals cannot transfer to
new heads. The Cursor reservation stays held.

## Design

Persist one immutable closure per incident/epoch, bound to owner/group. Close
and begin serialize on the same advisory lock and lease row. Closing stops new
mutation IDs immediately, including after renewal; already admitted PENDING
intents remain reconciliation-only. Return DRAINING until all pending writes
are resolved, then DRAINED. Never automatically clear an uncertain write.

Legacy release/takeover must reject an unclosed or pending old epoch. Preserve
epoch history by rejecting lease DELETE and incident-ID changes. Takeover must
increase the epoch, and a closed epoch can never be reopened. Fail closed on
nullable SQL inputs and nullable lease identity. Existing idempotent confirmed
or pending mutation recovery remains readback-only after closure.

Use the existing gateway/RPC adapters and existing PostgreSQL proof workflow.
An observation-only extra provider reread cannot establish write fencing;
another controller would create competing authority. Neither is used.

## Deployment boundary

Independent reviewer must validate real PostgreSQL races and role privileges,
inventory and revoke bypass credentials, bind source/incident/lease receipts,
wire existing App and finalizer paths, and independently verify deployed web
and worker SHAs plus stale-provider rejection. Until then this is PR_CREATED,
not FIXED_AND_VERIFIED. Keep can_execute=false and V17_TERMINAL_REDUCER.
