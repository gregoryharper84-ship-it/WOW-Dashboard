# WOW V17 Trigger.dev isolated read-only pilot

**Status: proposed, not deployed.** This experiment adds NO new production scheduler, scoring workers, settlement/publishing calls, model certification or database writes. It does not modify the existing Render/Supabase/GitHub Action chain.

## Purpose

Prove bounded durable retries, stable trigger idempotency options, and read-only observability *in Trigger.dev DEVELOPMENT* before proposing any production adoption. The only permitted request is `GET https://wow-governed-probability-engine.onrender.com/health/live`, with no redirects or Authorization header. A 200 response means HTTP reachability **only**, not model health, publishability or production verification.

## Architecture and boundaries

- Existing authority: GitHub Actions OWNS existing scheduled data collection, grading, and prop lifecycle; Render/Supabase remain canonical.
- Pilot: a manual-only Trigger task, `wow-v17-readonly-health-pilot`; no `schedules.task`, cron, trigger webhook, or GitHub Action dispatcher.
- Fixed external GET only, no user-controlled hostname or route, no secrets sent to Render.
- `concurrency.total = 1`, `maxAttempts = 3`, 1–5s bounded backoff, 30s max duration, 8s request timeout. `retries.enabledInDev = true` explicitly enables retry testing in development (Trigger.dev defaults to retries disabled there). HTTP 429/5xx and transport failures retry; HTTP 4xx (other than 429) and disabled safety guards abort without retry.
- Invocation uses 24h idempotency key and 10m enqueue TTL; local tests verify stable options but **only an actual Trigger.dev run can prove remote deduplication**.
- Fail-closed `WOW_TRIGGER_PILOT_ENABLED=true`, `WOW_TRIGGER_PILOT_ENV=dev`, `WOW_CAN_EXECUTE=false`, `WOW_DRY_RUN_ONLY=true`; caller refuses non-dev Trigger secret keys.
- Free tier: zero schedules; manual probes only. Monitor usage; do not authorize plan upgrades.

## Local tests (no credentials; no HTTP)

Run `node --test` from this directory. The tests use stubbed fetch calls and do not require installing Trigger.dev SDK. For full SDK type-check/CLI integration install this package's dependencies with `npm ci` in this isolated directory, not at workspace root.

## DEV-only deployment procedure (operator action)

1. In existing **WOW Enterprise / WOW Model** Trigger.dev DEVELOPMENT environment, open project settings to obtain its actual `proj_...` project reference (the URL slug is not this reference). Configure `TRIGGER_PROJECT_REF` locally. Never paste secret keys into chat or code.
2. In the isolated directory run `npm ci --ignore-scripts`; then `npm run typecheck` and `npm test`. Confirm SDK v4 compatibility and lint/review the security constraints. The committed `package-lock.json` pins the tested dependency graph. Keep auto-deploy / GitHub integration disabled during trial. This package intentionally has no `deploy` npm script.
3. In your own environment set `WOW_TRIGGER_PILOT_ENABLED=true`, `WOW_TRIGGER_PILOT_ENV=dev`, `WOW_CAN_EXECUTE=false`, `WOW_DRY_RUN_ONLY=true`. Run `npm run dev` with your own *development* Trigger secret key; this registers the manually triggered task in DEV without deploying a production worker.
4. For an explicit one-off trial set `WOW_TRIGGER_PILOT_ENV=dev` and execute `node bin/trigger-once.mjs 2026-11-07-manual-1`. Repeat the identical command to verify Trigger.dev returns the same run ID and no second execution within 24h; then use a new probe ID for a second run.
5. In Trigger.dev development Runs, capture: task ID, environment, both invocation run IDs, attempt count, status, request HTTP code, duration, compute usage. For actual retry behavior, run the built-in **synthetic retry task** described below; local tests already inject a 503→503→200 sequence. Do **not** manipulate the production health endpoint to induce faults.
6. Production acceptance requires independent SIRT comparison with GitHub Actions and Render readbacks; no automatic promotion on successful health GET.

## Deterministic DEV retry proof (no HTTP calls)

The **separate** manually triggered `wow-v17-synthetic-retry-pilot` task performs no network or database operations. It deliberately fails on Trigger.dev platform attempts 1 and 2 and succeeds on attempt 3, using the SDK's `ctx.attempt.number`. After starting `trigger dev`, open this task in the existing DEV dashboard, select **Test**, and supply `{"probeId":"2026-11-07-synthetic-1"}`. Verify a single Trigger.dev run has exactly 3 attempts and a terminal success. The task is not scheduled and cannot touch a sport/model workflow.

Keep `WOW_TRIGGER_PILOT_ENABLED=true`, `WOW_TRIGGER_PILOT_ENV=dev`, `WOW_CAN_EXECUTE=false`, and `WOW_DRY_RUN_ONLY=true` set in the **Trigger.dev DEV task environment**, not merely the invocation shell. Local CLI tasks inherit that shell; any future remotely hosted DEV worker must receive those variables separately. Never copy these flags into a production deployment. Do not add project secrets or enable production.

## Security review notes and limits

- The CI workflow pins third-party actions to immutable commit SHAs, installs the committed lockfile with lifecycle scripts disabled, and audits the dependency tree for high/critical advisories.
- The current CLI dependency tree uses explicit patched overrides for `deepmerge-ts@8.0.2`, `esbuild@0.25.0`, `minimatch@11.0.1`, `tar@7.5.22`, and `zod@4.6.7`. These are recorded in the committed lockfile; do not replace them with `npm audit fix --force` or silently downgrade Trigger.dev.
- The fixed health response body is cancelled after headers; redirects are surfaced (never followed) and treated as permanent non-2xx failures. Missing fetch support is a non-retryable configuration failure; timeout and transport errors remain retryable.
- The source-text checks in `pilot.test.mjs` are smoke-test tripwires only. They are not proof that the whole package is side-effect-free; the reviewed task import graph and the fixed GET implementation establish the current bounded behavior.
- Both tasks require Trigger.dev's provider-reported `ctx.environment.type === "DEVELOPMENT"` at runtime. The read-only health helper independently requires that provider-supplied value before issuing any request; the synthetic task checks it before retry logic. Operator-set `WOW_*` flags remain additional controls, not environment proof. The CLI also requires a `tr_dev_` key. Do not deploy this pilot to Staging or Production; no deploy script, schedule, GitHub deployment integration, or production credential is part of this pilot. Any broader use still requires separate approval.
- The two same-key submissions returned one run ID and one dashboard run was observed to execute once. This proves the tested sequential duplicate case only; it does not prove concurrent race handling, expiry-boundary behavior, or exactly-once side effects. Never rely on Trigger.dev idempotency alone for future writable work.

## Acceptance criteria

1. `node --test` succeeds on the exact PR head; Trigger.dev DEV retries are explicitly enabled.
2. Exactly one GET is observed per probe attempt; forbidden mutation API paths absent.
3. Duplicate DEV invocations with the same key return the same run ID with one execution during the dedupe window (must be verified live, not inferred from tests).
4. 429/5xx fail transiently; 4xx permanent failures and missing governance flags abort without retry.
5. No new Trigger schedules or overlapping production workers; no changes to existing Actions, Render, Supabase, grading, scoring, or publication.
6. Real DEV run receipt plus independent validation before widening scope.

## Required before production integration

Verify durable cross-provider leases and recipient-side idempotency in the existing V17 API before ever moving any non-GET work. Trigger.dev idempotency alone cannot make a non-idempotent remote POST safe when a request times out after side effects. Maintain independent exact-head release and SIRT acceptance.
