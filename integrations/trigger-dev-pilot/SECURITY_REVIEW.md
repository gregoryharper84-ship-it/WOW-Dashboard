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
| DEV flags are self-asserted and do not constitute provider-attested environment identity | **Residual medium deployment-boundary risk** | Not fully solved by code. The CLI refuses non-`tr_dev_` keys and this task has only a fixed GET sink, but a separately deployed task could be misconfigured. Keep this PR draft and do not deploy to Staging/Production. Any broader use requires a provider-verifiable environment guard and separate SIRT review. |
| npm audit found 9 transitive advisories (including high/critical findings in deepmerge-ts, esbuild, minimatch, tar and zod) in the Trigger.dev CLI tree | High dependency/supply-chain risk in developer tooling | Fixed with explicit compatible overrides to `deepmerge-ts@8.0.2`, `esbuild@0.25.0`, `minimatch@11.0.1`, `tar@7.5.22`, and `zod@4.6.7`; the regenerated lockfile contains these patched versions. A runner-side `npm audit --audit-level=high` completed successfully with the regenerated lockfile. The final committed-lock exact-head rerun is pending. |
| Sequential duplicate submissions returned one run ID and one execution | Accepted for tested case only | Does not prove simultaneous race behavior, 24-hour expiry-boundary behavior, cancellation semantics, or exactly-once side effects. Future writable operations require durable cross-provider leases and recipient-side idempotency. |
| Trigger Development API key was created with full access within the Development environment | Bounded credential scope | Keep it private, do not put it in GitHub or chat, and revoke it when no longer needed. It is not a Production key. |

## Failure behavior after hardening

- Disabled/misconfigured safety flags, invalid probe IDs, unavailable fetch implementation, invalid response shape, and non-2xx 4xx/3xx responses abort without retry.
- HTTP 429 and 5xx responses retry through Trigger.dev.
- Timeouts and transport errors retry through Trigger.dev, with a distinct timeout code.
- Exactly one fixed unauthenticated GET is issued per attempt; redirects are never followed and response bodies are cancelled.
- The synthetic task has no HTTP, database, scoring, settlement, or scheduling side effects.

## Preliminary engineering disposition

**Conditional technical readiness for the isolated, manually triggered Development pilot only, subject to final exact-head checks and formal non-author SIRT sign-off against this report and the live run receipts. This is not SIRT approval. Not approved for Production, Staging, scheduler replacement, writable workflows, model qualification, or automatic promotion.**

