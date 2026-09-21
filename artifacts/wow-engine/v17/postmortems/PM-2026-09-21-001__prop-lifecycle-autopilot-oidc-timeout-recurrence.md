# PM-2026-09-21-001 — Recurring `wow-v17-prop-lifecycle-autopilot` OIDC/timeout failure on the lifecycle-cycle step

## Status

OPEN

## Summary

The scheduled `wow-v17-prop-lifecycle-autopilot` workflow (cron `*/15 * * * *`)
repeatedly fails its "Run universal V17 prop lifecycle cycle" step. `curl`
against `POST /v17/prop-lifecycle-autopilot-run` on the production Render
service exhausts its retry budget with zero bytes received, and once a
response finally arrives it is `HTTP 401 {"detail":"Invalid credential."}`
because the single GitHub Actions OIDC token minted before the call has
expired by the time the server responds. This is a CI/observability-layer
failure of the production automation job, not a governed-model or
probability defect: `can_execute=false` is asserted before every request and
the job never reaches a state where execution could occur.

## Impact

- User-visible impact: none directly; this is a backend automation job, not
  a user-facing route.
- Model/probability impact: none. No probability, calibration, or terminal
  semantics are touched by this failure or by the diagnosis below.
- Governance impact: none. `can_execute=false` is asserted in the workflow
  before every request; no run in the evidence below reached a 2xx response
  with an execution-relevant field violated.
- Data/persistence impact: unknown whether the in-flight production request
  actually completes server-side after the client gives up (the client only
  reports what it observed, not server-side cycle completion). Worth
  confirming in a future run via `/v17/prop-lifecycle-health`.

## Detection

- Detected at: 2026-09-21 (this nightly/hourly engineering worker run),
  discovered via `gh run list --branch main` showing recent `failure`
  conclusions for `wow-v17-prop-lifecycle-autopilot` interleaved with
  occasional `success`.
- Detected by: `wow-v17-claude-engineering-worker` (this run).
- First known bad run in the inspected window: `35543182817`
  (2026-09-20T22:57:09Z).
- Last known bad run in the inspected window: `35590594479`
  (2026-09-21T10:47:14Z). No further runs of this workflow were observed in
  `gh run list` as of this inspection despite the 15-minute cron, which is
  itself worth re-checking in a future run.
- Last known good run: `35554307873` (2026-09-21T02:28:46Z) — the run
  immediately following the merge of PR #631 (see Evidence).

## Evidence

- Run `35590594479` (2026-09-21T10:47:14Z), step "Run universal V17 prop
  lifecycle cycle":
  ```
  curl: (28) Operation timed out after 240001 milliseconds with 0 bytes received
  curl: (28) Operation timed out after 240000 milliseconds with 0 bytes received
  Lifecycle endpoint returned HTTP 401.
  {"detail":"Invalid credential."}
  ```
- Run `35563004043` (2026-09-21T05:01:29Z), identical pattern:
  ```
  curl: (28) Operation timed out after 240001 milliseconds with 0 bytes received
  curl: (28) Operation timed out after 240000 milliseconds with 0 bytes received
  Lifecycle endpoint returned HTTP 401.
  {"detail":"Invalid credential."}
  ```
- Run `35550643651` (2026-09-21T01:21:40Z, before the PR #631 merge at
  2026-09-21T02:28:43Z) failed one step earlier, in "Register exact
  basketball research candidates after runtime deploy":
  ```
  curl: (28) Operation timed out after 30002 milliseconds with 0 bytes received
  curl: (28) Operation timed out after 30002 milliseconds with 0 bytes received
  NBA scalar candidate registration route did not become ready.
  {"detail":"Invalid credential."}
  ```
- PR #631 (`fix(v17): refresh lifecycle OIDC during deploy polling`, merged
  2026-09-21T02:28:43Z, commit range prior to `22d13a61`) fixed exactly this
  failure mode for the *registration* step by minting a fresh OIDC token for
  every attempt instead of reusing one token across a multi-minute poll loop.
  The very next scheduled run, `35554307873` (2026-09-21T02:28:46Z),
  succeeded.
- PR #528 (`V17: prevent lifecycle OIDC expiry on long production cycle`,
  **closed 2026-09-17T22:41:51Z without merging**) diagnosed the identical
  mechanism for the *lifecycle-cycle* step itself: "the universal lifecycle
  cycle exceeded the old 240s curl timeout; curl then retried with the same
  expired GitHub OIDC token and received HTTP 401." Its proposed remediation
  (`--max-time 720`, removing automatic curl retries) was never merged.
- Current file
  `.github/workflows/wow-v17-prop-lifecycle-autopilot.yml` (as of `main`
  `22d13a61`), step "Run universal V17 prop lifecycle cycle" (lines
  144-183), confirms the vulnerable pattern is still present: one
  `oidc_token` is minted, then used for one `curl --retry 3
  --retry-all-errors --retry-delay 5 --connect-timeout 30 --max-time 240`
  call against `/v17/prop-lifecycle-autopilot-run`. The job's own
  `timeout-minutes: 12` (720s) is wide enough for the request to eventually
  finish, but the single OIDC token minted before the call is short-lived
  and does not survive across the up-to-four attempts curl can make
  (`1 + 3 retries`, each up to `240s` plus `5s` delay).

## Root Cause

Confirmed (by direct analogy to the already-merged fix for the sibling step
in the same workflow, PR #631, and the matching unmerged diagnosis in PR
#528): the "Run universal V17 prop lifecycle cycle" step mints exactly one
GitHub Actions OIDC token before issuing a `curl` call that may retry up to
three additional times over several minutes. `POST
/v17/prop-lifecycle-autopilot-run` performs multi-stage, all-declared-route
work (`forward evidence -> exact settlement -> certification audit ->
production-registration audit -> calibrator-candidate audit -> health
persistence` for every route in `DECLARED_PROP_LANES`, since the request
body sends `"routes":[]`, which `_requested_tokens([])` in
`v17/prop_lifecycle_autopilot.py` resolves to *every* declared lane). When
that work is slow enough that the whole retry sequence exceeds the OIDC
token's lifetime, the token is stale by the time the server finally accepts
and validates the connection, producing `401 Invalid credential` — the exact
mechanism PR #631 already fixed one step earlier in the same file.

This has not been confirmed against a live production timing trace (this
worker's tool surface and network egress do not include access to
`wow-governed-probability-engine.onrender.com`), so the specific duration of
a full all-route lifecycle cycle is not independently measured here; the
evidence above is the matching failure signature plus the already-accepted
diagnosis and fix for the identical mechanism in the neighboring step.

## V17 Classification

- BACKEND_RUNTIME: CI/deployment automation failure (workflow-level), not a
  request-serving runtime defect.
- MODEL_CAPABILITY: not applicable — no model/scorer was invoked or
  evaluated by this failure.
- REPOSITORY_GOVERNANCE: not applicable — no governance gate was weakened;
  `can_execute=false` held in every observed run.
- LIVE_GPT_EDITOR_SYNC: not applicable.
- Terminal status: not applicable (no row was scored).
- `scoring_attempted`: not applicable (the request never completed from the
  client's perspective).

## Controlling Lane / Specialist

Not applicable. This is a `DEPLOYMENT_RUNTIME` / CI automation incident
under the `wow-nightly-engineering-autopilot` subsystem map, not a sporting
specialist lane.

## Failure Semantics

No typed model/scorer failure is involved. The workflow step itself fails
closed (`exit 1` on non-2xx) and does not fabricate a result.

## Remediation

- Engineering fix ID(s): none yet. The equivalent unmerged attempt is PR
  #528 (closed, not linked to a FIX record).
- Temporary mitigation: none applied by this record.
- Permanent fix (not implemented by this record — see Hard Boundary): apply
  the same fresh-token-per-attempt pattern PR #631 already uses for the
  registration step to the "Run universal V17 prop lifecycle cycle" step in
  `.github/workflows/wow-v17-prop-lifecycle-autopilot.yml` — e.g. mint a new
  OIDC token immediately before each `curl` attempt (removing curl's
  built-in `--retry` in favor of an explicit loop that re-mints the token,
  mirroring the registration step's `issue_oidc_token` helper), and/or
  revisit the unmerged PR #528 approach (`--max-time 720`, no blind curl
  retry) now that the job timeout is `12` minutes.

## Hard Boundary

`STOP_REASON`: the only currently known complete remediation requires
editing `.github/workflows/wow-v17-prop-lifecycle-autopilot.yml`.
`FIRST_BLOCKED_OPERATION`: this worker's operating instructions explicitly
prohibit modifying `.github/workflows/**` in this run.
`EXACT_ERROR_OR_STATUS`: not a test/code failure — a scope restriction.
`MISSING_CAPABILITY_OR_AUTHORITY`: permission/authority to edit repository
GitHub Actions workflow files from this worker invocation.
`WORK_COMPLETED_BEFORE_BLOCK`: root cause reproduced from CI evidence and
cross-referenced against the already-merged fix for the identical mechanism
in the sibling step (PR #631) and the matching unmerged diagnosis (PR #528);
this postmortem persists that evidence so it is not re-diagnosed from
scratch next run.
`SMALLEST_NEXT_ACTION`: a worker or operator authorized to edit
`.github/workflows/wow-v17-prop-lifecycle-autopilot.yml` should apply the
fresh-token-per-attempt pattern to the lifecycle-cycle step (see
Remediation) and confirm the next few scheduled runs succeed.

No backend-only (non-workflow) alternative was identified that is both (a)
guaranteed to resolve this without a workflow change and (b) free of R2/R3
risk: shortening the per-request workload would mean reducing the
already-declared-route scope of the calibrator-candidate/certification
audit stages inside a single governed lifecycle cycle, which touches
governed calibration-evidence generation and is out of this worker's
authority to change unilaterally.

## Verification

- Regression test(s): not applicable — no application code changed.
- Acceptance test(s): not applicable.
- Production verification: not applicable — no fix was deployed by this
  record.
- Verified commit/deploy: not applicable.

## Prevention / Follow-up

- Confirm whether `wow-v17-prop-lifecycle-autopilot` is still firing on its
  15-minute cron; no runs were observed after `35590594479`
  (2026-09-21T10:47:14Z) as of this inspection, which is a separate
  observation worth checking independently in the next run.
- Once the workflow-level fix lands, verify with `/v17/prop-lifecycle-health`
  that no lifecycle cycles were silently duplicated by the client-side
  retries while a prior cycle was still running server-side.

## Closure

- Closed at: (open)
- Closed by: (open)
- Final status: OPEN / BLOCKED_HARD_BOUNDARY for this worker
- Linked engineering fix(es): none yet

---

V17 safety invariant: `can_execute=false`. This record cannot authorize, route, modify, approve, or cancel a wager/order.
