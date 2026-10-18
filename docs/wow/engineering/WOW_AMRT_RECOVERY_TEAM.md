# WOW V17 — Autonomous Model Recovery Team (AMRT)

**State:** SOURCE CANDIDATE / NOT DEPLOYED
**Priority:** Model-first P0 production recovery
**Parent control plane:** WOW_ECOSYSTEM_CONDUCTOR
**Runtime:** V17_ACTIVE
**Global terminal:** V17_TERMINAL_REDUCER
**Execution:** can_execute=false; DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true

## Contract

AMRT is an Engineering & Closure execution team, **not** an alternate control plane,
new model authority, security bypass, or reporting-only organization. Every blocker
has one accountable Engineering owner until independent proof of production recovery
or a named exact external block. GitHub issue lifecycle remains the visible source
of incident truth; existing Conductor envelopes, Supabase engineering receipts,
GitHub workflow and Redis/DB writer leases remain authoritative. Never create a
second incident database. The production Supabase project must be verified before
using it as a production evidence source; wow-engine-validation is not automatically
production.

## Seven distinct responsibilities

| Role | Input | Output |
| --- | --- | --- |
| Blocker Sentinel | Model/Action failures, Datadog, Render, GitHub, persisted run gaps | Deduplicated incident, typed severity, evidence |
| Root Cause Investigator | Reproducible incident | Actual failing boundary and root cause |
| Model Recovery Engineer | Scorer, route, package, inference and published-stage integration failures | Class B repair candidate, unchanged fitted probabilities |
| Data & Integration Engineer | Feed, quota, event identity, hydration, durable row failures | Reconciled data-contract repair |
| Code & CI Engineer | Reviewed reproduction and implementation scope | Single-writer lease-held patch, focused and regression tests, fallback worker |
| Independent QA | Exact candidate SHA and negative cases | Independent pass/reject receipt; never implementation self-approval |
| Release & Verification | Protected approvals and authorized merge | Live SHA, Action readback, persistence reconciliation, independent final signoff |

## Lifecycle, no fake closure

1. **Detect** from existing sources. De-duplicate by incident plus stable failure
   fingerprint; do not create an issue if the defect already has one.
2. **Admit** under existing engineering_dispatch_manifest.json and the shared
   Conductor owner envelope. P0 uses RAPID and an exact domain lease_group.
   No new dispatcher or separate queue authority.
3. **Lease** using the existing atomic incident writer mechanism. A stale or
   missing lease is not authorization to self-acquire a writer lease. An expired
   lease routes to the Conductor for atomic reclaim under the existing protocol.
4. **Investigate** and record root cause, source proof and exact acceptance.
5. **Repair** through the existing authenticated Engineering App workflow. Cursor,
   Copilot or another approved provider may prepare code, but only one authorized
   writer may commit at a time; repeat attempts are bounded and failing providers
   must hand back explicit reasons.
6. **Test/review** on exact SHA. Class A may use existing authorized release
   conditions; Class B always gets independent relevant review; Class C requires
   model owner certification plus governed approval. No self-approval.
7. **Release/verify** only after authorized merge: compare exact integrated SHA
   with Render deployed SHA and live Action receipt SHA; confirm all acceptance
   scopes against the identified production DB and one persisted run identity.
   Validate scorer/board/terminal concordance; negative/held cases are not
   silently promoted.
8. **Close** only if the independent closure gate returns FIXED_AND_VERIFIED and
   the existing Release and QA authorities have supplied their own receipts.
   Otherwise maintain BLOCKED_WITH_EXACT_REASON, the accountable owner and
   explicit re-entry trigger.

## Current restoration acceptance cases — October 18, 2026

### P0 #1841 / candidate PR #1842
* Stage-proof contract from actual performing authority; missing dynamic
  calibration or final refresh is UNPROVEN, not a fabricated PASS.
* All eight disputed NFL scored rows and the 31-row full-board path must
  reconcile; preserve calibrated probability values, terminal reducer, and
  fail-closed ranks.
* Full/Compact Action parity and persisted row readback; negative cases for
  stale proof, package failure and guard conflicts.
* No production closure on PR existence or reported local 89-test run.

### P0 #823 and #1806 / integration PR #1835
* Canonical sport/event identities cannot suppress NFL/NHL with MLB raw-ID
  collisions; full-slate and official-publication rules must reconcile.
* A rejected review requires a changed exact-head candidate and independently
  reviewed re-test; no merging rejected state.
* Coordinate #1842's publication adapter but do not combine unrelated
  authorizations or overwrite active writer leases.

### P0 governed release path
* Existing authorized protected workflow produces integrated SHA, independent
  approvals and explicit deploy authorization.
* Render live SHA equals the authorized integrated SHA, rather than source PR
  head SHA when a merge commit was created.
* Live Action acceptance and persisted run/event/SHA evidence agree and
  independent QA signs closure.

## Integration checklist for the Engineering App

This draft intentionally adds a pure decision module and tests; it does not
edit protected workflows or activate a background daemon. The authenticated
Engineering App must:
- Adopt the additive gate into the existing v17 engineering worker, keeping
  credentials/lease decisions server-side.
- Admit #1841 as a P0 RAPID domain-scoped incident, preserving existing #823 /
  #1806 ownership and non-overlap with the #1835 source lease.
- Persist phase receipts to existing wow_engineering_attempt_receipts and
  existing Conductor ownership format; do not invent a new data authority.
- Ingest permitted GitHub, Datadog, Render and database signals with bounded
  polling/webhook retries and deduplication; use worker concurrency <= 2.
- Use the existing independent QA/Release workers to supply truthful receipts,
  not implementation-generated booleans.
- Verify source + CI SHA, authorized merge + Render SHA and same-run Action,
  model, persistence and reconciliation. Call the pure closure gate last.
- Submit through protected CODEOWNER/Security/QA/Release gates. No direct
  production deploy from this candidate branch.

## Operating scorecard

Report only verified values: open P0s, unverified repair candidates,
independently verified production recoveries, median detection-to-closure time,
stalled leases, and incident recurrence within 7/30 days. A diagnostic PASS,
draft PR, workflow dispatch or green CI alone must not count as recovered.

**Initial target:** closure of #1841 under the complete proof chain, without
misreporting source-only progress as production green.
