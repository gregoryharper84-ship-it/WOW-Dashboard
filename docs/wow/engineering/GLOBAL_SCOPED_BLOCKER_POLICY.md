# Global scoped-blocker and work-continuation policy

Applies to WOW Engineering, repository agents, Ecosystem Conductor, Betting Intelligence, SIRT assurance, and independent verification handoffs. Preserve each role's authority and existing Class A/B/C gates.

## Mandatory decision rule

A blocker stops only actions that depend on it. A release hold is not a development hold. Before stopping, identify the exact prohibited operation, its dependency and evidence; continue all authorized work that does not depend on that operation.

Maintain development and release progress separately. Implementation-complete or tests-passed does not mean released or FIXED_AND_VERIFIED. Never clear a hold, self-approve, publish a held probability or bypass required independent review to keep moving.

## Required blocker record

Carry the existing incident/work ID and owner, blocked operation, affected lane/environment, dependency, authoritative evidence, unblock condition, next permitted action, next owner, and revisit trigger. Preserve existing leases, bounded WIP, single-writer ownership and retry budgets. Do not introduce a competing queue.

For each candidate next action, evaluate authorization, dependencies, isolation and ownership. Missing evidence is not permission. If no action can proceed, retain BLOCKED_WITH_EXACT_REASON and explain why every relevant available action is blocked. A repeated hold report alone is not progress.

## Continue within authority

Engineering continues permitted reproduction, diagnosis, branch implementation, CI repair, isolated tests, review preparation, rollback planning and evidence preparation. Conductor coordinates permitted work and handoff receipts; it never implements a self-issued release approval. SIRT continues read-only diagnosis and assurance. Independent Verification remains separate from Engineering.

Explicit baseline/branch dependencies remain binding: do not create a dependent restoration branch before its required verified baseline. Advance independent work instead. A confirmed security incident may prohibit credential use or affected writes; use safe read-only or isolated work only where authorized.

## Stall response and reporting

When a held release has actionable development, select and execute the next permitted action in the current working session. Deployed workers must apply the same decision within their existing cycle; policy text alone does not prove runtime enforcement. Repeated idle cycles with eligible work require a tracked stall finding and repair, not endless polling or unbounded retries.

Reports state blocked action, permitted action actually performed, evidence, remaining dependency and next action. Never claim workers started or background continuation without execution receipts.

## Acceptance scenarios

- Release authorization absent: dependent merge/deploy stays held; authorized investigation, isolated branch work and tests continue.
- Required verified baseline absent: dependent lane waits; independent permitted work advances.
- Unknown ownership/lease: affected writes wait; authorized read-only diagnosis continues.
- Security hold forbids affected credentials/writes: respect the prohibition; evaluate isolated work.
- All available work truly blocked: exact reasons and revisit trigger persist; no fabricated progress.
- Development complete but release held: retain separate states; never claim production closure.

This is an instruction/coordination policy. Runtime dispatcher wiring, behavioral regressions and live GPT editor installation require separate exact-revision evidence before claiming deployed enforcement.
