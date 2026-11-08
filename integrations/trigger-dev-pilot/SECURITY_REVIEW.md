# WOW V17 Trigger.dev pilot — preliminary independent security/readiness review

**Scope:** PR #2507, isolated package `integrations/trigger-dev-pilot`, and its dedicated CI workflow. This is a code-level readback independent of the feature implementation; it is **not** a formal SIRT sign-off, release approval, or authorization to deploy.

## Evidence reviewed

- PR #2507 head before hardening: `6708127ab8d4235035eed82022e05cf19c46385d`.
- Main branch readback: `7b09f7528189abff7a64839e586d7a71be12cb9f`; the PR branch now includes this exact main commit as its second parent. A fresh compare reports the PR branch 14 commits ahead and zero behind; the four intervening main commits touched unrelated source-review and post-deploy verification files.
- All 12 PR-added paths were inspected: the workflow, README, CLI, package manifests/lockfile, read-only probe, submit helper, two Trigger tasks, tests, Trigger config and tsconfig.
- Exact-head CI at the pre-hardening head reported 8/8 workflows successful, including the pilot-specific test/typecheck job.
- Live Trigger.dev Development evidence:
  - Synthetic retry run `run_06grh0b0dn1corv9gnc9b9r2a2`: platform attempts 1 and 2 failed, attempt 3 succeeded; output explicitly reported `production_verified:false`, `model_qualified:false`, and `can_execute:false`.
  - Two sequential invocations using probe ID `2026-11-07-idempotency-1` returned the same run ID `run_06grh630j1kvlqlkn6jofb0fa2`. The dashboard showed one logical run; after the local dev worker reconnected, its log recorded one successful task execution (455 ms).
- No production deploy, production API key, database credential, schedule, scoring operation, settlement, or publication action was used in these acceptance runs.

## Findings and disposition

| Finding | Severity | Disposition |
|---|---|---|
| CI used mutable `actions/checkout@v4` and `actions/setup-node@v4` tags | Low supply-chain integrity risk | Fixed by pinning verified release commits: checkout v5.0.0 (`08c6903cd8c0fde910a37f88322edcfb5dd907a8`), setup-node v5.0.0 (`a0853c24544627f65ddf259abe73b1d18a591444`); both release commits were verified as signed. |
| Probe did not cancel response bodies; redirect refusal could be classified as retryable transport failure | Low resource/failure-classification risk | Fixed: response bodies are cancelled; `redirect: "manual"` prevents following redirects and 3xx is permanent; timeout, transport, missing-fetch and invalid-response cases have distinct classifications. |
| CLI missing probe ID and Trigger API failures could emit unhandled stack traces | Low operator reliability risk | Fixed with usage validation, safe error codes and non-zero exit codes; raw SDK errors/secrets are not printed. |
| Regex/source-text checks could be mistaken for proof of no side effects | Test-evidence limitation | Documentation now explicitly labels these as smoke tripwires, not security proof. The task's current allowed import graph and fixed GET implementation were reviewed directly. |
| Task environment was previously guarded only by operator-set flags | Medium deployment-boundary risk | Fixed in the current code: both tasks check the provider-reported Trigger.dev `ctx.environment.type` and abort unless it is exactly `DEVELOPMENT`; the read-only health helper requires this value before making the fixed GET. Unit tests reject `PRODUCTION`, `STAGING`, `PREVIEW`, and missing environment types before network access. The CLI separately requires a `tr_dev_` key. |
| npm audit found 9 transitive advisories (including high/critical findings in deepmerge-ts, esbuild, minimatch, tar and zod) in the Trigger.dev CLI tree | High dependency/supply-chain risk in developer tooling | Security overrides were added for `deepmerge-ts@8.0.2`, `esbuild@0.25.0`, `minimatch@11.0.1`, `tar@7.5.22`, and `zod@4.6.7`, and the lockfile audit reports zero vulnerabilities. However, these overrides were not runtime-compatible by themselves: the exact-head local `npm run dev` later failed because Trigger.dev CLI 4.8.3 imports the removed top-level `minimatch.makeRe` export. This was a false-green gap in the earlier CI evidence; see the Nov 8 runtime re-challenge below. A version/source-guarded local CLI compatibility patch now addresses this import only for the pinned 4.8.3 package. It is not an upstream-supported release and does not establish full DEV worker startup. |
| Sequential duplicate submissions returned one run ID and one execution | Accepted for tested case only | Does not prove simultaneous race behavior, 24-hour expiry-boundary behavior, cancellation semantics, or exactly-once side effects. Future writable operations require durable cross-provider leases and recipient-side idempotency. |
| Trigger Development API key was created with full access within the Development environment | Bounded credential scope | Keep it private, do not put it in GitHub or chat, and revoke it when no longer needed. It is not a Production key. |

## Failure behavior after hardening

- Provider-reported environment types other than `DEVELOPMENT` (including missing context), disabled/misconfigured safety flags, invalid probe IDs, unavailable fetch implementation, invalid response shape, and non-2xx 4xx/3xx responses abort without retry.
- HTTP 429 and 5xx responses retry through Trigger.dev.
- Timeouts and transport errors retry through Trigger.dev, with a distinct timeout code.
- Exactly one fixed unauthenticated GET is issued per attempt; redirects are never followed and response bodies are cancelled.
- The synthetic task has no HTTP, database, scoring, settlement, or scheduling side effects.

## Preliminary engineering disposition

**Conditional technical readiness for the isolated, manually triggered Development pilot only, subject to final exact-head checks and formal non-author SIRT sign-off against this report and the live run receipts. This is not SIRT approval. Not approved for Production, Staging, scheduler replacement, writable workflows, model qualification, or automatic promotion.**



## 2026-11-08 exact-head runtime re-challenge

**Observed failure on parent PR #2507 head `9ca92f8f7e215e4eecea975117e46841183b297f`:** after a clean `npm ci --ignore-scripts`, `npm run dev` failed before the Trigger.dev worker started:

```text
SyntaxError: The requested module 'minimatch' does not provide an export named 'makeRe'
Node.js v26.12.0
```

The lockfile and installed dependency tree showed `trigger.dev@4.8.3` with the security override `minimatch@11.0.1`. The published CLI source imports `makeRe` and calls it once in `dist/esm/build/externals.js`; minimatch 11.0.1 removes `makeRe()` entirely, including the class instance method. The zero-vulnerability npm audit did not establish runtime compatibility.

**Bounded remediation on this PR branch:**
- Added `scripts/patch-trigger-cli.mjs`, restricted to CLI 4.8.3, minimatch 11.0.1, picomatch 2.3.2 and exactly one expected import/call pair.
- The script rewrites only the local installed CLI import to `picomatch` and the call to `picomatch.makeRe(externalName)`.
- It is idempotent and fails closed on an unexpected package version or source shape.
- `npm run dev` invokes `start-dev-preflight.mjs` through `predev`; it refuses an invalid/missing `TRIGGER_PROJECT_REF`, a non-`tr_dev_` key, or any missing/weakened pilot flag before patching or starting the CLI. CI uses the separate `patch:cli` script and never needs credentials. `npm ci --ignore-scripts` remains lifecycle-script-free.
- Added six compatibility tests for glob-to-RegExp behavior, exact patch behavior, idempotency, package-version rejection and source drift. A prior proposed `Minimatch.makeRe()` adaptation was disproved by a direct API test (22/23 tests); it was not accepted and was replaced with picomatch.
- Added a no-credential CI smoke import of the patched CLI module.

**Exact-head CI evidence:** commit `427e6616dda805676d6b77feeec0ee5d90099d5d`, workflow run [#42493964300](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42493964300) completed successfully on Node 26 (matching the operator local CLI runtime): 28/28 tests, picomatch-based patch applied, CLI module import smoke passed, TypeScript check passed, all 33 cross-platform lock entries present, and `npm audit` reported zero vulnerabilities.

**Additional dependency limitation:** `tar@7.5.22` is outside `giget@1.2.5`’s declared `^6.2.1` range; `esbuild@0.25.0` is outside Trigger.dev CLI 4.8.3’s declared `^0.23.0` range. The current CLI import smoke does not exercise giget archive extraction or the full esbuild build pipeline. These compatibility paths remain UNVERIFIED and are a reason to keep the pilot local and DEVELOPMENT-only.

**Limits:** this proves the patched CLI module imports in CI. It does not prove `trigger dev` starts a connected worker, provider-reported `DEVELOPMENT`, actual task registration, retries, remote idempotency, or checkpoint/resume. Those require the operator's local DEV project reference/key and real Trigger.dev receipts. The patch is a temporary local compatibility adaptation, not an upstream Trigger.dev fix.

**Disposition:** runtime compatibility is improved but the pilot remains `WORKING_NOT_COMPLETE`. No merge, deployment, production activation, scheduler change, writable task, or SIRT acceptance is claimed.


## 2026-11-08 GitHub-hosted DEVELOPMENT runner design — pending live secret configuration

The operator requested a no-local-terminal path. Trigger.dev's native GitHub integration deploys PRs into Preview branches; the published active Preview branch limits are Free 0, Hobby 5, Pro 20. The user's earlier dashboard observation was that Preview deployment is restricted on the current plan, and the standing cost directive is no additional spend. Therefore this pilot does **not** use native Preview deployment and does not request a plan upgrade.

A separate opt-in workflow, `.github/workflows/wow-trigger-remote-dev-acceptance.yml`, starts a bounded `trigger dev` worker inside a GitHub-hosted runner on one named Trigger.dev DEVELOPMENT branch. It is restricted to same-repository PR #2529, requires repository opt-in variable `WOW_TRIGGER_REMOTE_DEV_ENABLED=true`, has `contents: read`, no schedule, no GitHub write credential, and a 12-minute timeout. It re-runs tests/typecheck, applies the exact-version/source-guarded CLI patch, triggers only the existing health, synthetic retry and engineering-intake tasks, polls their run receipts, and terminates the worker. It does not deploy to Trigger Preview, Staging or Production, and it does not dispatch a coding agent.

**Credential trade-off:** Trigger.dev documents that Free/Hobby environments expose only the `No restrictions` API-key preset. A Development key stored in the GitHub Actions environment is therefore full access to the Trigger.dev DEVELOPMENT environment, even though the task code itself is read-only/synthetic. The new workflow requires a *new, short-lived Development-only key* in the GitHub environment secret `wow-trigger-dev / TRIGGER_SECRET_KEY`, never the previously used key and never a Production/Staging key. Set a short expiration, enable GitHub environment required-reviewer protection if available, and revoke the key after acceptance. The project reference is a non-secret Actions variable. The workflow is deliberately opt-in and does not run until the user configures the secret and variable.

**Current disposition:** code and workflow design only. No GitHub environment secret/variable is configured or read by SIRT; no cloud worker has been started; no provider receipt exists for this workflow. The opt-in job must remain skipped until the operator explicitly configures the dedicated Development key and project reference. A successful source CI run is not a connected-worker receipt. Formal non-author SIRT review remains outstanding.


### Exact-head no-credential CI readback for remote runner — Nov 8, 2026

- Current exact head: `26752693593706c170975160307bb2fe00190766`.
- Pilot verification [#42523475007](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42523475007): SUCCESS; 48/48 tests, TypeScript, patched CLI import smoke, 33 cross-platform lock entries and npm audit 0 vulnerabilities.
- Remote DEV workflow [#42523475026](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42523475026): SKIPPED by its explicit opt-in guard. The job did not start, no Trigger key was consumed, and no cloud task receipt was produced.
- The tests validate receipt-shape assertions and workflow safety conditions only. They do not validate a connected GitHub-hosted worker, provider-reported DEVELOPMENT, actual health/retry/checkpoint runs, or secret configuration.
- **Disposition remains WORKING_NOT_COMPLETE / UNVERIFIED for live DEV acceptance.** The operator must configure the repository opt-in variable, actual project reference variable, and a new short-lived Development-only key in the GitHub environment before the workflow can run. Formal non-author SIRT review remains pending.


### Live runner authentication finding — Nov 8, 2026

The first GitHub-hosted worker attempt reached the guarded job with the configured project reference and masked Development API key. The CLI process exited before worker registration and reported: `Authentication required in CI environment. Please set the TRIGGER_ACCESS_TOKEN environment variable with a Personal Access Token.` This is the Trigger.dev CLI's explicit CI authentication contract, not a project-ref or API-key failure.

Source inspection of Trigger.dev CLI v4.8.3 `packages/cli-v3/src/commands/login.ts` confirms that CI requires `TRIGGER_ACCESS_TOKEN` and validates it as a `tr_pat_...` Personal Access Token (or internal organization token). A Development environment API key (`tr_dev_sk_...`) is not accepted for CLI login. The CLI error is distinct from the SDK's `TRIGGER_SECRET_KEY` used by the task-triggering code.

The workflow now preflights `TRIGGER_ACCESS_TOKEN` before starting a worker and expects it as a separate GitHub Actions environment secret. This is a user-level PAT and has a broader potential blast radius than a single Development API key. Do not add it until the GitHub environment has a required reviewer / prevent-self-review protection and a branch deployment restriction. Use a new short-lived PAT, run once, then revoke it. No PAT was present in the first attempt; no task run was triggered and no task receipt exists.

**Current state:** Root cause CONFIRMED; remediation code/docs added; live worker acceptance remains BLOCKED on protected, short-lived PAT provisioning. Production/Staging access remains prohibited.

### Latest exact-head state and remaining user-only action — Nov 8, 2026

Exact child head: `4adac19878272f76fda20c9b4c03e849e285fc16`. No-credential pilot verification [#42525441419](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42525441419) completed SUCCESS: 51/51 tests, TypeScript passed, patched CLI import smoke passed, 33 cross-platform lock entries present, and npm audit reported 0 vulnerabilities.

The remote acceptance workflow [#42525441271](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42525441271) reached its explicit CLI credential preflight and failed closed with `TRIGGER_CLI_PERSONAL_ACCESS_TOKEN_REQUIRED`. The environment secret `TRIGGER_SECRET_KEY` is present (masked in logs); `TRIGGER_ACCESS_TOKEN` was empty. The worker did not start and no task run was created. This is expected until the additional CLI PAT is configured.

**Environment approval is not independently verified.** The workflow job did not expose a reviewer-approval receipt in the available readback, and the GitHub integration here cannot inspect environment protection settings. Treat required-reviewer and branch-restriction settings as UNVERIFIED. Before adding the user-level PAT, configure a required reviewer for `wow-trigger-dev` and restrict deployments to the fixed recovery branch. Use a new short-lived PAT, execute the bounded run once, then revoke it. Never paste it into chat.

Current disposition: implementation source validated; live task acceptance BLOCKED on protected PAT provisioning. PR #2529 remains DRAFT / UNMERGED. No Production/Staging, model scoring, coding dispatch or betting authority has been used.


### Credential-readiness guard — Nov 8, 2026

The remote job now requires repository variable `WOW_TRIGGER_CLI_AUTH_READY=true` in addition to the existing remote-worker opt-in. Set this only after the `wow-trigger-dev` environment has a required reviewer / branch restriction and both separate secrets are installed. This prevents automatic PR synchronization from repeatedly starting a runner while the CLI PAT is absent. The variable is an operator acknowledgement, not proof of environment protection; actual run receipts remain the evidence.
