# SIRT independent review — Trigger.dev DEV pilot PR #2507

Intake: issue #2516. Reviewer: Copilot coding agent, acting as non-author SIRT reviewer. This review is independent of the feature author and of the PR's own `SECURITY_REVIEW.md`. I re-read the source and re-ran the checks instead of relying on the engineering report.

This record grants **no** merge, deployment, scheduler, production, model-qualification or execution authority.
`can_execute=false`; `terminal_authority=V17_TERMINAL_REDUCER`.

## Decisions (bound to exact SHAs)

| SHA | Role | Decision |
|---|---|---|
| `09347d8988ffb08b9fe5ba0bff1f8f10d564ae15` | Intake head named in #2516 | **FAIL (STALE)** |
| `85c1f68611004f61f62694e53bc01ce8d03bda87` | Current PR #2507 head at review time (base `main` `db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c`) | **HOLD** |

Any new head on PR #2507 makes this review stale. The review must then be repeated.

### Why 09347d89 = FAIL

- The head has been superseded: it is an ancestor of `85c1f686`, with 23 first-parent commits in between.
- **Reproduced runtime defect at this SHA.** After `npm ci --ignore-scripts`, importing `node_modules/trigger.dev/dist/esm/build/externals.js` fails with `SyntaxError: The requested module 'minimatch' does not provide an export named 'makeRe'`. The `minimatch@11.0.1` security override removed `makeRe`, and Trigger.dev CLI 4.8.3 imports it. As a result, `trigger dev` cannot start at this SHA.
- Pilot CI run [#42433230633](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42433230633) passed with 18/18 tests and 0 audit findings. That pass was a false green for worker startup.

### Why 85c1f686 = HOLD (not FAIL)

At the code level, the environment, side-effect and no-authority boundaries **pass**. The HOLD is caused by the following open items. None of them is a hard-boundary violation:

1. Required check **`WOW Independent QA exact-head` = failure** on `85c1f686` (check run 128773590313). It has no linked workflow run, and the cause is unexplained.
2. Live DEV receipts exist only for child PR #2529 head `6b38aa6de0191a2eb5f93049c1a446750b9478d9`. They were not produced on the PR #2507 head itself (see "Live evidence" below).
3. Findings F1–F4 below need either remediation or an explicit risk acceptance. In particular, `SECURITY_REVIEW.md` contains two overstatements (F1 and F4).

PASS conditions: F1–F4 are resolved or explicitly accepted, the `WOW Independent QA exact-head` failure is explained or cleared, and a fresh SIRT readback is done on the resulting exact head.

## Reproduction (performed on Node 24.22.1 / npm in a clean worktree per SHA)

```bash
git worktree add /tmp/pilot <SHA>
cd /tmp/pilot/integrations/trigger-dev-pilot
npm ci --ignore-scripts --no-audit --no-fund
npm run patch:cli && npm run cli:smoke     # 85c1f686 only
npm test
npm run typecheck
npm audit
```

| Check | 09347d89 | 85c1f686 |
|---|---|---|
| `npm ci` (committed lock) | 304 packages | 304 packages |
| `npm test` | 18/18 pass | 28/28 pass |
| `npm run typecheck` | pass | pass |
| `npm audit` (all severities) | 0 vulnerabilities | 0 vulnerabilities |
| CLI `externals.js` import | **SyntaxError (makeRe)** | pass after guarded patch |
| `npm ls --all` | — | exit 0 |

Pilot CI for `85c1f686` is run [#42494049459](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42494049459) (Node 26), conclusion success. Note that the CI audit step uses `--audit-level=high`. Moderate or low advisories would therefore **not** fail CI, so the "0 vulnerabilities" claim depends on the logged output and on this local reproduction, not on the gate.

## Review areas

### 1. Environment boundary — PASS (with F1)

- Both task entrypoints pass Trigger.dev's `ctx.environment.type` into a strict equality check, `=== "DEVELOPMENT"`. This happens before any fetch (`src/trigger/read-only-health.ts`, `src/trigger/synthetic-retry.ts`, `src/lib/read-only-health.mjs:assertTriggerDevEnvironment`).
- The self-declared `WOW_*` flags are combined with the provider check using **AND**, not OR, so they cannot replace it. The provider check runs first.
- I bundled the real task files with the installed esbuild 0.25.0 and a stub SDK (`task = o => o`), then invoked `run` directly. Results:
  - `PRODUCTION`, `STAGING`, `PREVIEW`, `development` (lowercase), `""` and `undefined` all produce `AbortTaskRunError TRIGGER_ENVIRONMENT_NOT_DEVELOPMENT` with 0 fetch calls.
  - `PRODUCTION` with all WOW flags disabled also aborts with the environment code and 0 fetches.
  - `DEVELOPMENT` with `WOW_TRIGGER_PILOT_ENABLED=false` produces `PILOT_DISABLED` with 0 fetches.
- **F1 (Low):** if `ctx.environment` is missing, the health task throws a raw `TypeError` outside the guard. That error is **retryable**, giving up to 3 attempts. It never reaches fetch, so there is no side effect. The synthetic task, by contrast, aborts with `SYNTHETIC_RETRY_DEV_GUARD_FAILED`. `SECURITY_REVIEW.md` says that missing context "abort[s] without retry", which is accurate only for `type === undefined`. Remediation: use `ctx?.environment?.type` and make the documentation match.

### 2. Side-effect boundary — PASS

- The only network call is `fetch(HEALTH_URL, { method: "GET", headers: { accept: "application/json" }, redirect: "manual", signal: AbortSignal.timeout(8000) })`. `HEALTH_URL` is a module constant (`https://wow-governed-probability-engine.onrender.com/health/live`). No caller-supplied URL, no Authorization header and no body are sent.
- I tested a real Node fetch against a local 302 server. With `redirect: "manual"` it returned `status 302` and the target was hit exactly once, so the redirect was not followed. The code then classifies 3xx as permanent. An opaque status of 0 maps to `INVALID_HEALTH_RESPONSE`, which aborts.
- Source scan of `src/`, `bin/`, `scripts/` and `trigger.config.ts` found no Supabase/DB, POST/PUT/PATCH/DELETE, schedule/cron, scoring, settlement, order or publication path. `scripts/patch-trigger-cli.mjs` writes only the local `node_modules/trigger.dev/.../externals.js`, and only behind version and source-shape guards.
- The PR diff against base touches 16 files, all under `integrations/trigger-dev-pilot/**` or its dedicated verify workflow. No `artifacts/wow-engine` or governance code is touched.

### 3. Failure semantics — PASS (with F2)

| Condition | Observed classification |
|---|---|
| 429 / 5xx | `HEALTH_HTTP_<n>` retryable |
| timeout (`TimeoutError`) | `HEALTH_TIMEOUT` retryable |
| other transport throw | `HEALTH_TRANSPORT_UNAVAILABLE` retryable |
| 3xx / 4xx | permanent, aborted |
| status outside 100–599 / missing response | `INVALID_HEALTH_RESPONSE` aborted |
| missing fetch | `FETCH_UNAVAILABLE` aborted |
| invalid/null payload | `INVALID_PROBE_ID` aborted, 0 fetches |
| disabled/weakened WOW flags | aborted, 0 fetches |
| unexpected non-ProbeError | rethrown, so retryable (bounded by `maxAttempts: 3`, `maxDuration: 30`) |
| synthetic attempts 1, 2 / 3 / 4 | transient / success `attempts:3` / `UNEXPECTED_SYNTHETIC_ATTEMPT` abort |

- **F2 (Low):** the response body is cancelled *before* the status is classified. If `body.cancel()` throws after a 3xx or 4xx response, the result is `HEALTH_BODY_CANCEL_FAILED` (retryable). A permanent failure can therefore turn into up to 2 more identical GETs. This is bounded and read-only. Remediation: classify the status first, or treat cancel failure as non-retryable when the status is already permanent.

### 4. Idempotency — PASS for what is claimed; no exactly-once claim

- The key is `wow-v17-readonly-health:<probeId>`, where `probeId` must match `^[A-Za-z0-9._-]{1,80}$`. It is submitted with `idempotencyKeyTTL: "24h"` and queue `ttl: "10m"`.
- The SDK (`@trigger.dev/core` 4.8.3 `makeIdempotencyKey`) wraps raw strings with scope `run`. Outside a task context, `injectScope` adds nothing, so from `bin/trigger-once.mjs` the key is a SHA-256 of the raw key material. That makes it effectively global within the Trigger environment for this task, for 24 hours.
- Evidence shows **sequential** same-key dedupe only:
  - the earlier build: `run_06grh630j1kvlqlkn6jofb0fa2`, from a user-supplied observation;
  - child run #42555938361: two sequential `enqueueReadOnlyProbe` calls returned the same `run_06gs0mflinch6ee9nhqdbidaa2`.
- **Not proven:**
  - concurrent or racing submissions;
  - behavior at the 24-hour expiry boundary;
  - key reuse after a failed, cancelled or expired run (the queue TTL is 10 minutes);
  - exactly-once side effects.
- The unit test explicitly does not claim API deduplication. Any future writable task needs recipient-side idempotency.

### 5. Supply chain — PASS (with F3, F4)

- The action SHAs were verified against upstream tags: `actions/checkout` v5.0.0 = `08c6903cd8c0fde910a37f88322edcfb5dd907a8`, and `actions/setup-node` v5.0.0 = `a0853c24544627f65ddf259abe73b1d18a591444`. The workflow uses `contents: read`, `persist-credentials: false`, no secrets and `npm ci --ignore-scripts`.
- Each override has an advisory justification. I checked this against the GitHub Advisory DB and against `npm audit` with the override removed:

| Override | Declared by consumer | Out of range? | Advisory driving override |
|---|---|---|---|
| tar 7.5.22 | giget 1.2.5 `^6.2.1`; CLI `^7.5.13` | **yes (giget)** | multiple node-tar path-traversal/DoS (≤7.5.20) |
| esbuild 0.25.0 | CLI `^0.23.0` | **yes** | advisory affecting 0.17–0.28.0 is *withdrawn*; 0.25.0 is still inside that range |
| minimatch 11.0.1 | CLI `^10.2.3` | **yes (major)** | GHSA-g3hv-75wg-m249 (moderate, 7.0.0–11.0.0): `npm audit` reports it for 10.2.6 when override removed |
| zod 4.6.7 | CLI **exact `4.5.4`**; core/sdk `^4.0.0` | **yes (CLI exact pin)** | zod memory exhaustion < 4.6.6 |
| deepmerge-ts 8.0.2 | @prisma/config **exact `7.1.5`** | **yes (major)** | stack exhaustion < 8.0.0 |

- **F3 (Low, DEV-tooling compatibility):** reachability of the out-of-range packages on the `trigger dev` path:
  - `tar` is imported only by the CLI `deploy/*` modules and by `giget`. `giget` is reached through c12 only for remote `extends`, which this config does not use. Not on the dev path.
  - `deepmerge-ts` is reached only through `@trigger.dev/build` → `@prisma/config`, which is not used.
  - `esbuild` 0.25.0 *is* on the dev bundling path. It bundled both task files in this review, and the child live run started a worker with it, but its full CLI plugin pipeline compatibility is not proven.
  - `zod` 4.6.7 is in range for core and sdk but violates the CLI's exact pin.
  - The minimatch → picomatch `makeRe` substitution can differ in glob semantics, for example around dotfiles. It is version- and shape-guarded and covered by 6 tests.

  Keep the pilot DEV-only and do not run `trigger deploy` with this tree.
- **F4 (Doc accuracy):** `SECURITY_REVIEW.md` lists only tar and esbuild as out of range. It omits zod (exact pin) and deepmerge-ts (major). It also groups minimatch with the "high/critical" findings, although its advisory is moderate.
- The lockfile is complete. The workflow's 33 cross-platform optional entries are present, and `npm ls --all` exits 0.

### 6. Operational limits — PASS for DEV-only use

- **Credentials:** on Free/Hobby, the `tr_dev_` key has full access *within DEVELOPMENT*. The `tr_dev_` prefix check in `start-dev-preflight.mjs` and `bin/trigger-once.mjs` is self-declared. The real boundary is the provider-reported environment check in the tasks.
- **Output hygiene:** `bin/trigger-once.mjs` prints only `probe_id`, `run_id` and safe error codes. No secret is logged by pilot code.
- **Local-worker dependency:** the pilot works only while a `trigger dev` worker is running, either locally or on the opt-in GitHub-hosted runner from PR #2529.
- **No-deploy boundary:** there is no deploy script, and the verify workflow has no deploy step. Nothing technically prevents an operator holding a PAT from running `trigger deploy`. If that happens, both tasks still abort in non-DEVELOPMENT environments before any GET (defense in depth).
- **Rollback / disable path:**
  1. Set `WOW_TRIGGER_PILOT_ENABLED` to anything other than `true`. Tasks abort with no fetch.
  2. Stop the worker.
  3. Revoke the `tr_dev_` key and any CLI PAT.
  4. Close the PR. No production state, schedule or database is involved.

### 7. No model authority — PASS

The pilot imports nothing from `artifacts/wow-engine`, and it calls no scoring, calibration, qualification, publication, settlement or order route. Every task output hard-codes `production_verified:false`, `model_qualified:false` and `can_execute:false`. A `/health/live` HTTP 200 is reachability only. It cannot change fitted probabilities or terminal publication.

## Live evidence (read back independently, not taken from the report)

Child PR #2529 remote DEV workflow [#42555938361](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42555938361), attempt 3, job `129168755023`, at `6b38aa6d`, conclusion success at 2026-11-09T12:49:29Z:

- `TRIGGER_PAT_WHOAMI_STATUS 200`;
- health `run_06gs0mflinch6ee9nhqdbidaa2` COMPLETED, `http_status` 200, duplicate submission returned the same run ID;
- synthetic `run_06gs0mfqesl8utv7cusrv6ima2` COMPLETED with `attempts: 3`;
- recovery `run_06gs0mggkvt3579n29hnke6ua2` with checkpoint/resume proved.

`git merge-base --is-ancestor 85c1f686 6b38aa6d` is true. `git diff 85c1f686 6b38aa6d` touches none of these files, so this pilot's task code is identical in both heads:

- `src/lib/read-only-health.mjs`
- `src/lib/submit-readonly.mjs`
- `src/trigger/read-only-health.ts`
- `src/trigger/synthetic-retry.ts`
- `trigger.config.ts`
- `package.json`
- `package-lock.json`

Limits of this evidence:

- The receipt field `"environment": "DEVELOPMENT"` is **hard-coded** by the runner script (`run-remote-dev-acceptance.mjs`), not read from the provider.
- Provider-reported DEVELOPMENT is nonetheless proven *indirectly*. Both tasks completed successfully, and both run `assertTriggerDevEnvironment(ctx.environment.type)` before any work.
- Trigger.dev-side run records were not read by this reviewer.
- These receipts belong to the child head, not to `85c1f686` itself.

## Required remediation before PASS

1. Explain or clear the failing `WOW Independent QA exact-head` check on the PR #2507 head.
2. F1: use null-safe `ctx?.environment?.type` in the health task, or explicitly accept the risk. Correct the "missing context" statement in `SECURITY_REVIEW.md`.
3. F2: classify permanent status before treating a body-cancel failure as retryable, or explicitly accept the risk.
4. F3/F4: record zod (exact pin) and deepmerge-ts (major) as out-of-range overrides. Correct the minimatch severity. Keep `trigger deploy` out of scope.
5. Do not claim concurrency, expiry, cancellation or exactly-once idempotency.
6. Request a fresh SIRT readback bound to the resulting exact head.

PR #2507 must remain DRAFT. This review is not merge, deployment, scheduler, production or execution authorization.
