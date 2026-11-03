# WOW V17 restoration loop — V1

**Status:** source candidate, not activated. This document and its workflow add no
release authority. `can_execute=false` · `V17_TERMINAL_REDUCER`.

## One loop, one unit of work

One queue, one active repair, one verified release at a time. The loop extends the
existing control plane; it does not add a team, broker, orchestration vendor,
certification layer, cron database or second scheduler.

| Step | Implemented behavior |
| --- | --- |
| **Inspect** | Every 15 minutes the loop reads production health, the deployed Render commit, the active Engineering App repair, its exact-head checks and any merged-but-unproved release. Unfinished work is examined before new work. A broken observation is a typed HOLD, never inferred health. |
| **Select** | The existing manifest-backed queue is filtered by GitHub open state, then `select_dual_stream_work` picks the highest-priority restoration incident. Selection requires the incident's existing PR, branch and acceptance test; otherwise it holds. |
| **Repair** | The next action is routed to the existing `wow-v17-engineering-auto-repair` path with failure evidence. The loop never authors a competing branch or a second writer. |
| **Verify** | Required exact-head checks decide: failed → feedback to the same PR; pending → wait; passed → release admission. A skipped or missing check is not a pass. |
| **Release** | Passed checks plus acceptance hand the PR to the existing protected governance dispatcher. The loop never merges, approves, or deploys. |
| **Prove** | A release is verified only when the live Render commit equals the merged commit and a probe bound to that commit exercised the repaired user function. Only then is the issue closed. |

The planner is `artifacts/wow-engine/v17/restoration_loop.py`; the scheduler is
`.github/workflows/wow-v17-restoration-loop.yml`.

## Four rules

1. **One writer.** An active repair is resumable only on the exact head reserved
   by `wow-engineering[bot]`. `DISPATCH_RESERVED` means the dispatch outcome is
   unknown and is never resent. Foreign, duplicate, malformed or stale
   reservations hold. Stale ownership is recovered only through the existing
   reconciliation/release path.
2. **Bounded recovery.** A transient provider failure is retried once; after that
   only a pre-authorized alternative. Quota/billing failures are never retried
   against a paid provider. Deterministic failures are repaired, not retried.
3. **Keep useful work moving.** A release hold blocks that release only.
   Diagnosis, acceptance-test preparation and the next queue item continue; the
   loop still opens no second writer or release.
4. **Record evidence.** Each cycle updates one marker comment on the incident with
   commit, test result, deployment result, reason codes and next action. A
   repeated `AWAIT_*` cycle is recorded as `progress_made: false` and
   `heartbeat_only: true`; a heartbeat never counts as progress.

## Observation contract

The collector is read-only. It reads GitHub issues, open and merged
`wow-engineering[bot]` PRs, their reservation comments and exact-head check runs,
`/health/live`, and the live Render deploy commit when `WOW_RENDER_SERVICE_ID`
and `RENDER_API_KEY` are configured. Missing inputs become `collection_errors`
and the cycle holds.

An active repair is resumed only when all of the following are present:

- `Incident: <n>` in the PR body and an open span in the dispatch manifest;
- the reservation marker `<!-- WOW_RECOVERY_ATTEMPTS -->` authored by
  `wow-engineering[bot]` with `state: DISPATCH_ACCEPTED` on the live head;
- an `Acceptance-Test:` line in the PR body;
- required exact-head check runs.

A release is proved only when the issue is open, the PR is merged, the live
Render commit equals `merge_commit_sha`, and a probe record exists with:

```json
{"name": "...", "passed": true, "evidence_ref": "...", "verified_commit": "<40-hex>"}
```

The probe is produced by the existing acceptance/canary lane, not invented by the
loop.

## Metrics

Every cycle publishes:

- `verified_repairs` — distinct incidents with recorded verified proof;
- `last_verified_repair_at`;
- `seconds_since_last_verified_repair`, or unknown / `NEVER_VERIFIED`.

Set `vars.WOW_RESTORATION_LEDGER_ISSUE` to one issue to accumulate the verified
count across incidents. Without it the metric falls back to the
active/release/selected issue, which only measures that incident.

Zero or absent evidence is `NEVER_VERIFIED`, never 100% or "healthy". A green CI
run, merged PR or live process is not a verified repair.

## First job — reconcile the reservation, prove one cycle

The reservation's current state needs fresh verification. Run the read-only
collector, then read the active repair:

```bash
PYTHONPATH=artifacts/wow-engine python -m v17.restoration_loop collect \
  --repo gregoryharper84-ship-it/WOW-Dashboard \
  --manifest artifacts/wow-engine/v17/engineering_dispatch_manifest.json \
  --output /tmp/observation.json \
  --production-url https://wow-governed-probability-engine.onrender.com
jq '{active_repair, releases_in_flight, production, collection_errors}' /tmp/observation.json
```

Then classify the marker explicitly:

```bash
gh api --paginate "/repos/gregoryharper84-ship-it/WOW-Dashboard/issues/<PR>/comments?per_page=100" \
  > /tmp/comments.json
PYTHONPATH=artifacts/wow-engine python -m v17.restoration_loop reconcile \
  --comments /tmp/comments.json --head-sha <40-hex-head>
```

Expected dispositions:

| Status | Meaning | Allowed next operation |
| --- | --- | --- |
| `NO_RESERVATION` | No active writer marker | Existing dispatcher may reserve the exact head |
| `ACCEPTED_EXACT_HEAD` | One authenticated writer on the live head | Resume/verify that same repair; never re-dispatch |
| `RESERVED_UNCONFIRMED` | Dispatch POST outcome unknown | Read-only run lookup; reconcile, never resend |
| `ACCEPTED_STALE_HEAD` / `RESERVED_STALE_HEAD` | Marker bound to another head | Hold; recover through existing reconciliation |
| `FOREIGN_OWNERSHIP_PRESENT` / `AMBIGUOUS_MULTIPLE_RESERVATIONS` / `INVALID_RESERVATION` | Not a single authentic writer | Hold; manual reconciliation |

A reservation with no corresponding protected-main run is ambiguous and stays a
HOLD. There is no blind dispatch retry.

To prove one complete repair-to-production cycle, run the loop on an inert
incident with a real acceptance test and record, in order: the exact reserved
head; the exact-head required checks; the protected merge commit; the live Render
commit equal to that merge commit; the user-function probe bound to that commit;
and the closed issue with `verified_repairs` incremented. Until all six exist on
the same identity, the cycle is not verified.

## Activation and boundaries

- Merge through the normal protected PR path with the required checks. The
  workflow is inert until it exists on `main`.
- GitHub schedules can be delayed; the 15-minute cadence is the minimum, not a
  guarantee. Every run is self-contained, so a delayed run only widens the gap.
- Required optional configuration: `vars.WOW_RENDER_SERVICE_ID` and
  `secrets.RENDER_API_KEY` for deployed-commit proof. Without them the loop
  holds at `DEPLOYED_COMMIT_UNAVAILABLE` rather than guessing.
- Rollback: disable the workflow. The existing dispatcher, QA/Release and Render
  paths are unaffected.

## Out of scope for V1

No merge/deploy by the loop, no new queue, no second writer, no paid retry after
quota exhaustion, no probability or calibration change, no review approval, no
live execution. Adding any of those requires its own protected change.
