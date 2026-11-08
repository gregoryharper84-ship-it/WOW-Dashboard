import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { HEALTH_URL, ProbeError, observeWowHealth } from "../src/lib/read-only-health.mjs";
import { TASK_ID, idempotencyKeyFor, enqueueReadOnlyProbe } from "../src/lib/submit-readonly.mjs";

const safe = Object.freeze({ WOW_TRIGGER_PILOT_ENABLED: "true", WOW_TRIGGER_PILOT_ENV: "dev", WOW_CAN_EXECUTE: "false", WOW_DRY_RUN_ONLY: "true" });
const okResponse = { status: 200 };

test("fails closed when disabled: no network call", async () => {
  let calls = 0;
  await assert.rejects(observeWowHealth({ env: {}, fetchImpl: async () => { calls++; return okResponse; } }),
    (err) => err instanceof ProbeError && err.code === "PILOT_DISABLED" && !err.retryable);
  assert.equal(calls, 0);
});

test("refuses non-development environments before any request", async () => {
  let calls = 0;
  await assert.rejects(observeWowHealth({ env: {...safe, WOW_TRIGGER_PILOT_ENV: "prod"}, fetchImpl: async () => { calls++; return okResponse; } }),
    err => err.code === "NOT_DEV_PILOT" && !err.retryable);
  assert.equal(calls, 0);
});

test("requires permanent no-execution governance", async () => {
  for (const env of [ { ...safe, WOW_CAN_EXECUTE: "true" }, { ...safe, WOW_DRY_RUN_ONLY: "false" } ]) {
    let calls = 0;
    await assert.rejects(observeWowHealth({ env, fetchImpl: async () => { calls++; return okResponse; } }),
      (err) => err.code === "EXECUTION_GUARD_NOT_PROVEN" && err.retryable === false);
    assert.equal(calls, 0);
  }
});

test("fixed read-only GET with no token or redirection", async () => {
  const called = [];
  const result = await observeWowHealth({
    env: safe,
    now: () => "2026-11-07T13:00:00.000Z",
    fetchImpl: async (url, options) => {
      called.push([url, options]);
      return { ...okResponse, body: { cancel: async () => { called.push(["body-cancelled"]); } } };
    },
  });
  assert.equal(called.length, 2); // one request plus the explicit body-cancel observation
  assert.equal(called[0][0], HEALTH_URL);
  assert.equal(called[0][1].method, "GET");
  assert.equal(called[0][1].redirect, "manual");
  assert.deepEqual(called[0][1].headers, { accept: "application/json" });
  assert.deepEqual(called[1], ["body-cancelled"]);
  assert.equal(result.production_verified, false);
  assert.equal(result.model_qualified, false);
  assert.equal(result.can_execute, false);
  assert.equal(result.observed_at, "2026-11-07T13:00:00.000Z");
});

test("transient network / rate limit / server errors are eligible for retry", async () => {
  for (const scenario of [async () => { throw Error("timeout"); }, async () => ({status:429}), async () => ({status:503})]) {
    await assert.rejects(observeWowHealth({ env:safe, fetchImpl:scenario }),
      err => err instanceof ProbeError && err.retryable === true);
  }
});

test("permanent HTTP 4xx is never marked retryable", async () => {
  for (const code of [400,401,403,404]) {
    await assert.rejects(observeWowHealth({ env:safe, fetchImpl:async()=>({status:code}) }),
      err => err instanceof ProbeError && err.code === `HEALTH_HTTP_${code}` && !err.retryable);
  }
});

test("recovery simulation: exactly one GET per attempt, 2 retries then success", async () => {
  let n=0; let outcome;
  const mock = async () => ({status: ++n <= 2 ? 503 : 200});
  for (let attempt=1;attempt<=3;attempt++) {
    try {outcome=await observeWowHealth({env:safe,fetchImpl:mock});break;}
    catch(e){assert.equal(e.retryable,true);}
  }
  assert.equal(n,3); assert.equal(outcome.http_status,200);
});

test("stable idempotency input and duplicate submission arguments", async () => {
  const invocations=[];
  const fake = async (...args) => {invocations.push(args);return {id:"run_same"};};
  const a=await enqueueReadOnlyProbe("2026-11-07-manual-1",fake);
  const b=await enqueueReadOnlyProbe("2026-11-07-manual-1",fake);
  assert.equal(a.id,b.id);
  assert.equal(invocations.length,2); // API deduplication is NOT claimed by this fake.
  assert.deepEqual(invocations[0], invocations[1]);
  assert.equal(invocations[0][0],TASK_ID);
  assert.equal(invocations[0][2].idempotencyKey,idempotencyKeyFor("2026-11-07-manual-1"));
  assert.equal(invocations[0][2].idempotencyKeyTTL,"24h");
  assert.equal(invocations[0][2].ttl,"10m");
});

test("unsafe IDs are rejected before any API calls", async () => {
  let calls=0;
  for (const bad of ["", "with space", "../../x", "/etc/passwd", "x".repeat(81), undefined]) {
    await assert.rejects(enqueueReadOnlyProbe(bad, async()=>{calls++;}), TypeError);
  }
  assert.equal(calls,0);
});

test("task source tripwire: no obvious schedules or direct network calls; this is not proof of isolation", () => {
  const source=readFileSync(new URL("../src/trigger/read-only-health.ts",import.meta.url),"utf8");
  assert.match(source,/concurrency:\s*\{\s*total:\s*1\s*\}/);
  assert.match(source,/maxAttempts:\s*3/);
  const imports = [...source.matchAll(/from ["']([^"']+)["']/g)].map(match => match[1]).sort();
  assert.deepEqual(imports, ["../lib/read-only-health.mjs", "../lib/submit-readonly.mjs", "@trigger.dev/sdk"].sort());
  assert.doesNotMatch(source,/schedules\.task|cron\s*\(|\bfetch\s*\(/i);
});

test("actual Trigger DEV config enables retries and stays isolated", () => {
  const config = readFileSync(new URL("../trigger.config.ts", import.meta.url), "utf8");
  assert.match(config, /retries:\s*\{\s*enabledInDev:\s*true\s*\}/);
  assert.match(config, /dirs:\s*\["\.\/src\/trigger"\]/);
  assert.doesNotMatch(config, /schedules\.task|cron\s*\(|deploy\s*\(/i);
});

test("synthetic retry task is DEV-only and has no external side effects", () => {
  const source = readFileSync(new URL("../src/trigger/synthetic-retry.ts", import.meta.url), "utf8");
  assert.match(source, /assertPilotSafety\(\)/);
  assert.match(source, /idempotencyKeyFor\(payload\?\.probeId\)/);
  assert.match(source, /ctx\.attempt\.number/);
  assert.match(source, /attempt === 1 \|\| attempt === 2/);
  assert.match(source, /attempt !== 3/);
  assert.match(source, /maxAttempts:\s*3/);
  assert.match(source, /concurrency:\s*\{\s*total:\s*1\s*\}/);
  assert.doesNotMatch(source, /fetch\s*\(|https?:\/\/|POST|supabase|cron\s*\(|schedules\.task/i);
});


test("redirect responses are never followed and are permanent failures", async () => {
  let requestOptions;
  let cancelled = 0;
  await assert.rejects(
    observeWowHealth({
      env: safe,
      fetchImpl: async (_url, options) => {
        requestOptions = options;
        return { status: 302, body: { cancel: async () => { cancelled++; } } };
      },
    }),
    err => err instanceof ProbeError && err.code === "HEALTH_HTTP_302" && err.retryable === false,
  );
  assert.equal(requestOptions.redirect, "manual");
  assert.equal(cancelled, 1);
});

test("missing fetch implementation is a non-retryable configuration error", async () => {
  await assert.rejects(
    observeWowHealth({ env: safe, fetchImpl: null }),
    err => err instanceof ProbeError && err.code === "FETCH_UNAVAILABLE" && err.retryable === false,
  );
});

test("invalid response status is rejected and body cancellation failure is retryable", async () => {
  await assert.rejects(
    observeWowHealth({ env: safe, fetchImpl: async () => ({ status: 700 }) }),
    err => err instanceof ProbeError && err.code === "INVALID_HEALTH_RESPONSE" && err.retryable === false,
  );
  await assert.rejects(
    observeWowHealth({ env: safe, fetchImpl: async () => ({ status: 200, body: { cancel: async () => { throw Error("cancel failed"); } } }) }),
    err => err instanceof ProbeError && err.code === "HEALTH_BODY_CANCEL_FAILED" && err.retryable === true,
  );
});

test("timeout is classified distinctly from other transport errors", async () => {
  await assert.rejects(
    observeWowHealth({ env:safe, fetchImpl: async () => { const e = new Error("timed out"); e.name = "TimeoutError"; throw e; } }),
    e => e instanceof ProbeError && e.code === "HEALTH_TIMEOUT" && e.retryable === true,
  );
});

test("task validates probe ID separately from remote health errors", () => {
  const source = readFileSync(new URL("../src/trigger/read-only-health.ts", import.meta.url), "utf8");
  assert.match(source, /idempotencyKeyFor\(payload\?\.probeId\);[\s\S]*?throw new AbortTaskRunError\("INVALID_PROBE_ID"\)/);
  assert.doesNotMatch(source, /error instanceof TypeError/);
});
