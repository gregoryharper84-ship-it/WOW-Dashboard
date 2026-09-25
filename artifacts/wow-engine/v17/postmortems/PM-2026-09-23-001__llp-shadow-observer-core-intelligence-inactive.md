# PM-2026-09-23-001 — LLP V17.1 shadow observer 404s because Core Intelligence production activation was never flipped

## Status

OPEN

## Summary

The scheduled `wow-v17-llp-shadow-observer` workflow (added 2026-09-21, cron
`17 2,8,14,20 * * *`, four runs/day) has failed on every scheduled invocation
since it went live. Each run calls `POST /internal/v17/llp/shadow/capture` on
the production origin and receives `HTTP 404 Not Found`. The route exists in
`main` and is unit/contract tested, but it is never mounted on the running
production app because the two environment-variable gates that control it
are not present anywhere in `render.yaml` and there is no other mechanism in
this repository that would set them on the live Render service.

## Impact

- User-visible impact: none. This is an internal research/evidence pipeline;
  no governed pick/probability/publication surface is affected.
- Model/probability impact: none. `LLP_V17_1_SHARPNESS_CHALLENGER` remains
  `SERVING_MODE=SHADOW_ONLY`; no production ranking/calibration/market-prior
  behavior is touched by this incident either way.
- Governance impact: none directly, but the intended append-only shadow
  evidence stream (used to eventually evaluate the challenger) has been
  silently empty in production since the automation was scheduled.
- Data/persistence impact: zero rows have been written by this pipeline in
  production. No data was lost or corrupted; nothing has ever been captured.

## Detection

- Detected at: 2026-09-23 (this run), via `gh run list --workflow=wow-v17-llp-shadow-observer.yml`.
- Detected by: nightly engineering autopilot / autonomous QA recovery run.
- First known bad run: `35733134146` (schedule, 2026-09-22T13:23:20Z) — the
  first scheduled run after the feature merged.
- Every subsequent scheduled run has failed identically:
  `35764693193` (2026-09-22T18:03:39Z), `35794888209` (2026-09-22T22:55:25Z),
  `35833206403` (2026-09-23T07:43:22Z).
- `push`/`pull_request` runs on the same workflow are green because that job
  only runs the local unit/contract suite (`verify` job); the failing job is
  `shadow-observer`, which only runs on `schedule`/`workflow_dispatch` and
  calls the live production origin.

## Evidence

- Run `35833206403`, step "Capture, grade, and score LLP shadow evidence":
  `WOW_ACTION_ORIGIN=https://wow-governed-probability-engine.onrender.com`;
  OIDC token exchange against `ACTIONS_ID_TOKEN_REQUEST_URL` succeeds; the
  first application call,
  `POST /internal/v17/llp/shadow/capture?max_predictions=1000`, raises
  `urllib.error.HTTPError: HTTP Error 404: Not Found`. Job exits 1.
- `artifacts/wow-engine/v17/core_intelligence_runtime.py:316-321` — the LLP
  shadow internal routes (including `/internal/v17/llp/shadow/capture`) are
  only installed when
  `os.getenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "0") == "1"`.
- `artifacts/wow-engine/api_ncaaf_acceptance.py:56` and `:169-175` — the
  entire Core Intelligence route group (which contains the LLP shadow
  installer) is only mounted when
  `os.getenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", "0") == "1"` AND
  `WOW_V17_ACTIVE=1`. `api_ncaaf_acceptance:app` is the exact production
  ASGI target (`render.yaml` `startCommand: uvicorn api_ncaaf_acceptance:app ...`).
- `render.yaml` (`wow-governed-probability-engine` service `envVars`, lines
  24-108 on `main` at `2750bc29`) declares neither
  `WOW_V17_CORE_INTELLIGENCE_ACTIVE` nor
  `WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE`. Every comparable feature flag in
  this file (`WOW_KALSHI_WEATHER_V2_ACTIVE`, `WOW_PROP_LIFECYCLE_AUTOPILOT_ENABLED`,
  etc.) is explicitly declared with a `value:`; there is no declared-but-
  dashboard-managed (`sync: false`) entry for either of these two keys, and no
  other file in the repository sets them.
- PR #613 ("V17: harden and activate Core Intelligence production routes",
  merged 2026-09-20 as `2493d258`) description: *"Production rollout remains
  migration-first: merge only after required checks pass, apply the
  access-hardening migration, verify privileges/triggers/RLS, then set
  `WOW_V17_CORE_INTELLIGENCE_ACTIVE=1` on the canonical Render service."* This
  is a deliberate, sequenced, operator-executed activation step — not a value
  the PR itself set in `render.yaml`.
- The live 404 on 2026-09-23 is direct evidence that, three days after PR
  #613 merged, that manual activation step has still not been completed (or
  was completed and later reverted) on the production service.

## Root Cause

Confirmed: two nested environment-variable gates
(`WOW_V17_CORE_INTELLIGENCE_ACTIVE`, then `WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE`)
control whether `/internal/v17/llp/shadow/*` is mounted on the production
FastAPI app. Neither is declared in `render.yaml`, and neither has evidently
been set out-of-band on the live Render service, so both default to `"0"`
and the routes 404. The `wow-v17-llp-shadow-observer` workflow was scheduled
on 2026-09-21 assuming Core Intelligence was already (or would imminently be)
active in production; it was not.

This is a deployment/runtime topology gap (declared code capability vs.
actual live service configuration), not a code defect in the route, the
challenger math, or the workflow itself.

## V17 Classification

- BACKEND_RUNTIME: route correctly implemented and tested; not reachable in
  the deployed process because of a missing production activation flag.
- MODEL_CAPABILITY: not applicable — no scoring/probability path is touched.
- REPOSITORY_GOVERNANCE: `render.yaml` (declared manifest) does not match the
  intended/actual production topology described by PR #613.
- LIVE_GPT_EDITOR_SYNC: not applicable.
- Terminal status: not applicable (no row is scored).
- `scoring_attempted`: not applicable.

## Controlling Lane / Specialist

Not applicable. This incident is entirely in the
`WOW_HOST_ORCHESTRATION` / `DEPLOYMENT_RUNTIME` engineering subsystem
(production route mounting), not a sporting specialist.

## Failure Semantics

The workflow correctly fails closed and reports the true HTTP status (404)
rather than masking it. No typed model/scorer failure is involved.

## Remediation

- Engineering fix ID(s): none. See "Why not fixed in this run" below.
- Temporary mitigation: none applied. The workflow's continued scheduled
  failures are accurate signal, not noise, and were left as-is rather than
  silenced (this worker is also prohibited from editing
  `.github/workflows/**`).
- Permanent fix (proposed, NOT applied): declare both flags in
  `render.yaml` under the `wow-governed-probability-engine` service, e.g.

  ```yaml
        - key: WOW_V17_CORE_INTELLIGENCE_ACTIVE
          value: "1"
        - key: WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE
          value: "1"
  ```

  This is deliberately NOT being applied in this run.

### Why not fixed in this run

PR #613 explicitly requires, before this flag is set in production: "apply
the access-hardening migration, verify privileges/triggers/RLS." This
worker's execution environment has no Supabase/database access and no
network egress to the production Render origin (confirmed: a direct
diagnostic `curl`/network call in this session requires interactive
approval that is unavailable in an unattended run — the same limitation
independently documented in `FIX-2026-09-14-001`'s Production Verification
section). There is no way from this sandbox to confirm that
`migrations/20260920_v17_core_intelligence_access_hardening.sql` (RLS,
service-role SELECT/INSERT-only, `anon`/`authenticated` revocation, TRUNCATE
guards on the `wow_intelligence_*` tables) is actually applied to the live
`wow-engine-validation` Supabase project. Setting the activation flag without
that confirmation would flip a previously-inactive production write surface
based on an unverified security precondition, which is outside this worker's
authority (`R2-repair-policy`/`R3`-adjacent: production security/grant state
change without independent verification). Per the governing skill, this is
preserved as evidence and not autonomously implemented.

## Verification

- Regression test(s): none changed — no code was modified.
- Acceptance test(s): n/a.
- Production verification: n/a — this record documents an unresolved gap.
- Verified commit/deploy: root-caused against `main` @ `2750bc2912234475c4ee712cd10994775e09f6c3`.

## Prevention / Follow-up

Owner action required (cannot be completed autonomously from this
environment):

1. Confirm `migrations/20260920_v17_core_intelligence_access_hardening.sql`
   is applied to the canonical Supabase project and independently verify RLS,
   service-role-only grants, `anon`/`authenticated` revocation, and the
   `BEFORE TRUNCATE` guard on every `wow_intelligence_*` table.
2. If and only if verified, set `WOW_V17_CORE_INTELLIGENCE_ACTIVE=1` and
   `WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE=1` on the live
   `wow-governed-probability-engine` Render service (directly, or by merging
   the `render.yaml` addition proposed above once the precondition is
   confirmed).
3. After activation, re-run `wow-v17-llp-shadow-observer` via
   `workflow_dispatch` once and confirm `capture.status == "PASS"`,
   `grade.status == "PASS"`, and `can_execute=false` /
   `automatic_promotion_allowed=false` /
   `production_mutation_allowed=false` on every response, matching the
   workflow's own assertions.
4. Until then, the four daily scheduled failures on this workflow are
   expected and should not be treated as a new regression by future nightly
   runs — they are this same root cause recurring.

## Closure

- Closed at: (open)
- Closed by: (open)
- Final status: `BLOCKED_HARD_BOUNDARY` — safe repair identified, requires
  owner-verified production database state before an environment flag can be
  set.
- Linked engineering fix(es): none.

---

V17 safety invariant: `can_execute=false`. This record cannot authorize, route, modify, approve, or cancel a wager/order.
