# WOW restoration observation — V1

Status: source candidate; not activated or production verified.

## One controller

`wow-engineering-24x7.yml` on protected main is the existing controller.
This change adds a **manual, read-only observer**, not another scheduled router.
It never dispatches a worker, reconciles/releases a reservation, approves or
merges a PR, deploys, closes an issue, or publishes model probabilities.
`can_execute=false`; `V17_TERMINAL_REDUCER` remains authoritative.

The earlier candidate scheduled a second router every 15 minutes. Its generic
repair dispatch did not bind the chosen incident, its reconciliation action
had no executor, and its collector had no authenticated production probe.
Those incomplete mutation paths have been removed rather than enabled.

## Inspection contract

The planner reports inspect/select/repair/verify/release/prove decisions from
snapshots. Action names describe proposed next work; they are not receipts that
an action ran. The collector reads the manifest-backed issue queue, Engineering
App PRs, reservation comments, active branch-rule requirements, exact-head check
runs, production liveness, and optional Render deployment identity.

Required check observations bind the required context, publisher App when
specified, and exact head. Missing contexts remain pending; missing protection
inventory remains unknown. Skipped checks are not passed checks. This diagnostic
does not replace protected governance or the existing delivery reducer.

Reservation classification is read-only. `DISPATCH_RESERVED` remains an unknown
outcome, not permission to resend. The remote writer ledger and finalizer remain
separate authoritative mechanisms. The observer cannot clear reservation
6553867635 or prove its remote worker has stopped.

Merged repairs are tracked independently for still-open governed incidents.
An unrelated newer PR cannot hide an unproved release. A user-function probe
must bind to the exact live commit. **The collector has no authenticated probe
adapter yet**, so deployed repairs hold at `USER_FUNCTION_PROBE_MISSING`.
There is no automatic issue close.

## Evidence and metrics

Each manual run publishes its observation and decision as workflow artifacts
and a job summary. It writes no issue comments or ledger entries. Existing
ledger comments are diagnostic inputs only; comments from other users are
ignored. They cannot authorize a release or prove a user-function probe.

Metrics count distinct recorded incident IDs and preserve those IDs across
ordinary cycles. Replaying the same proof cannot increment the count or reset
the last-repair timestamp. No metric means a production cycle has been proven
by this observer. Repeated unchanged decisions can be classified as heartbeats
when the caller supplies `last_cycle`; automatic cross-run progress persistence
is not implemented here.

## Manual inspection after protected merge

Run `wow-v17-restoration-loop` on main. It checks out the immutable workflow
source SHA with a read-only token. Optional `WOW_RENDER_SERVICE_ID` and
`RENDER_API_KEY` supply deployment identity; the key is read from the environment.
Missing proof configuration does not stop repair inspection. GitHub read errors
remain typed holds. The workflow has no cron and makes no provider calls.

For local diagnostic use, the CLI provides `collect`, `plan`, and `reconcile`.
A `CLOSE_VERIFIED_ISSUE` planner output is still diagnostic and is never executed
by this workflow. Do not feed untrusted snapshots to another mutation executor.

## Remaining production work

Use the existing 24x7 controller and its protected cutover requirements. Before
claiming autonomy, authenticate exact remote-writer release, prove available
coding capacity, complete required review/CI, and verify two real production
repair cycles. Connect a trusted user-function probe before adding completion
execution. This candidate alone does not complete any of those conditions.

## Repair plan and validation

1. Merge current main into the existing feature branch without force-push.
2. Reproduce missing checks, wrong publisher/head, forged metrics, duplicate
   counting, hidden releases, and duplicate-controller behavior in tests.
3. Fix the evidence boundaries and remove incomplete automatic mutation paths.
4. Run the focused tests, adjacent queue/controller/delivery tests, syntax and
   diff checks; then submit through the existing protected PR path.

The branch remains a review candidate until independent acceptance. No new
scheduler, provider, production secret, or governance exception is required.
