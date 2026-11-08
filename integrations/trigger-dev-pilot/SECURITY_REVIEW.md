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
- `npm run dev` invokes it through `predev`; `npm ci --ignore-scripts` remains lifecycle-script-free.
- Added six compatibility tests for glob-to-RegExp behavior, exact patch behavior, idempotency, package-version rejection and source drift. A prior proposed `Minimatch.makeRe()` adaptation was disproved by a direct API test (22/23 tests); it was not accepted and was replaced with picomatch.
- Added a no-credential CI smoke import of the patched CLI module.

**Exact-head CI evidence:** commit `c26207f2d332bf59ecacab141cee8b9b1f9be5cc`, workflow run [#42493613765](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42493613765) completed successfully: 24/24 tests, picomatch-based patch applied, CLI module import smoke passed, TypeScript check passed, cross-platform lock entries present, and `npm audit` reported zero vulnerabilities.

**Additional dependency limitation:** `tar@7.5.22` is outside `giget@1.2.5`’s declared `^6.2.1` range; `esbuild@0.25.0` is outside Trigger.dev CLI 4.8.3’s declared `^0.23.0` range. The current CLI import smoke does not exercise giget archive extraction or the full esbuild build pipeline. These compatibility paths remain UNVERIFIED and are a reason to keep the pilot local and DEVELOPMENT-only.

**Limits:** this proves the patched CLI module imports in CI. It does not prove `trigger dev` starts a connected worker, provider-reported `DEVELOPMENT`, actual task registration, retries, remote idempotency, or checkpoint/resume. Those require the operator's local DEV project reference/key and real Trigger.dev receipts. The patch is a temporary local compatibility adaptation, not an upstream Trigger.dev fix.

**Disposition:** runtime compatibility is improved but the pilot remains `WORKING_NOT_COMPLETE`. No merge, deployment, production activation, scheduler change, writable task, or SIRT acceptance is claimed.
