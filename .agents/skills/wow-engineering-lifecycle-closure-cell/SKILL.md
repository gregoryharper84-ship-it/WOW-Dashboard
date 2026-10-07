# WOW V17 Engineering Lifecycle Closure Cell

Status: `ACTIVE_ON_MERGE`
Identity: `WOW_ENGINEERING_LIFECYCLE_CLOSURE_CELL`
Parent: existing WOW V17 engineering control plane
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Mission

Keep one engineering incident moving from detection to a truthful terminal disposition. The cell is a coordination and reliability layer only. It does not own sporting probability rows, does not alter model math, and does not become a second engineering control plane.

## Roles

- `LIFECYCLE_CONTROLLER_AGENT`: owns the parent closure journey and prevents handoff loss.
- `QUEUE_STEWARD_AGENT`: enforces closure-first WIP, duplicate/stale-work cleanup, and highest-priority focus.
- `RELEASE_VERIFICATION_OWNER_AGENT`: owns exact-head governance follow-through, merge-to-deploy continuity, production acceptance, and terminal verification evidence.

These roles coordinate the existing Engineering, Independent Review, System Architect, QA, Release/Observability, Product Acceptance, and Reporter agents. They do not replace them.

## Closure sequence

`Detect -> Reproduce -> Classify -> Root Cause -> Minimal Fix -> Test -> Review -> Merge -> Exact-SHA Deploy -> Production Acceptance -> Terminal Disposition`

Work remains owned by the Lifecycle Closure Cell until it terminates as one of:

- `FIXED_AND_VERIFIED`
- `PR_CREATED`
- `EXPERIMENT_CREATED`
- `DUPLICATE`
- `NOT_REPRODUCIBLE`
- `BLOCKED_WITH_EXACT_REASON`
- `DEFERRED_WITH_JUSTIFICATION`

A merge is not terminal closure.

## Class A boundary

Lifecycle-control-plane work may remain Class A only when all protected scope flags are explicitly false.

Class B escalation is required for:
- model registration;
- routing or hydration;
- canonical reconciliation behavior;
- prediction/evidence persistence contracts.

Class C escalation is required for:
- sporting probability math or distributions;
- fitted artifacts or coefficients;
- calibration or calibrated lower bounds;
- qualification thresholds;
- failure-path weighting.

The deterministic classifier in `artifacts/wow-engine/v17/engineering_agent_team.py` is authoritative for this lifecycle boundary. An agent label cannot downgrade the required class.

## Operating rules

1. Closure-first: finish the highest-priority existing parent incident before opening ordinary new repair work.
2. One parent incident owns the implementation lease. Read-only specialists may work in parallel only on that same closure journey.
3. Trusted exact-head governance must be terminally approved before lifecycle-controlled autonomous merge.
4. Release verification must prove protected-main merge SHA -> deployed SHA -> production acceptance.
5. Preserve typed V17 failures. Never translate unrelated failures into `MODEL_UNAVAILABLE`.
6. Preserve exactly one fitted specialist owner per sporting probability row.
7. Never use sportsbook implied probability, narrative reasoning, or generic LLM output as a governed probability substitute.
8. Preserve `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` and `can_execute=false`.

## WIP

Use the existing deterministic WIP controller. The Lifecycle Closure Cell does not create a second queue. Truly superseded implementations should be closed, but base drift alone is never supersession and must not cause PR recreation.

## Base-drift and merge-candidate policy

1. Movement of `main` alone never closes, supersedes, or recreates an otherwise valid repair PR.
2. When `main` advances, preserve the PR number and head branch. Invalidate only the stale base/candidate certification and revalidate the same PR against current `main`.
3. A mergeable candidate uses `REVALIDATE_IN_PLACE`, including when protected lifecycle paths overlap; protected overlap requires the full relevant candidate gates, not a replacement PR.
4. Restack the same PR branch only when Git reports a real merge conflict or review proves semantic incompatibility with the new base.
5. Closing as `DUPLICATE/SUPERSEDED` is reserved for an implementation that has actually been replaced or made obsolete, never for a PR that is merely commits behind `main`.
6. Candidate evidence must bind the PR identity, PR head SHA, current base SHA, and prospective merge-candidate SHA. If the base moves again, discard only that candidate receipt and generate a new one.
7. Serialize overlapping lifecycle-control mutations at the merge/certification boundary; unrelated repository work may continue.

## Completion evidence

Reporter may publish completion only from durable exact-identity evidence. The lifecycle cell cannot approve its own implementation and cannot manufacture a `FIXED_AND_VERIFIED` state from prose.
