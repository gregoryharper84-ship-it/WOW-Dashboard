import assert from "node:assert/strict";
import test from "node:test";
import { probeWowRuntime, WOW_BACKEND } from "../src/trigger/wow-v17-recovery-probe.ts";

const HEALTH = { status: "ok" };
const READY = { status: "ok", database: "ok", queue: "ok", worker_registry: "ok", can_execute: false };
const now = () => new Date("2026-11-01T05:00:00.000Z");

function responses(live = HEALTH, ready = READY, status = 200) {
  const calls = [];
  const fetchImpl = async (url, opts) => {
    calls.push({ url, opts });
    if (url === `${WOW_BACKEND}/health`) return { status: 200, ok: true, json: async () => live };
    if (url === `${WOW_BACKEND}/health/ready`) return { status, ok: status === 200, json: async () => ready };
    throw new Error("unexpected endpoint");
  };
  return { calls, fetchImpl };
}

test("ready infra is NOT proof of model/Action capability", async () => {
  const { calls, fetchImpl } = responses();
  const r = await probeWowRuntime({ fetchImpl, now });
  assert.equal(r.status, "WORKING_NOT_COMPLETE");
  assert.equal(r.code, "BACKEND_READY_MODEL_UNVERIFIED");
  assert.equal(r.model_capability, "UNVERIFIED_NOT_PROBED");
  assert.equal(r.action_receipt, "UNVERIFIED_NOT_PROBED");
  assert.equal(r.can_execute, false);
  assert.equal(r.durable_incident_written, false);
  assert.equal(r.dispatch_attempted, false);
  assert.equal(r.incident_key, null);
  assert.equal(r.observed_at, "2026-11-01T05:00:00.000Z");
  assert.deepEqual(calls.map(x => x.url), [`${WOW_BACKEND}/health`, `${WOW_BACKEND}/health/ready`]);
  assert.ok(calls.every(c => c.opts.method === "GET" && c.opts.redirect === "error"));
});

test("typed 503 readiness is a backend blocker, not MODEL_UNAVAILABLE", async () => {
  const r = await probeWowRuntime({ ...responses(HEALTH, { detail: { status: "not_ready", database: "ok", queue: "unreachable", worker_registry: "ok", can_execute: false } }, 503), now });
  assert.equal(r.status, "BLOCKED");
  assert.equal(r.code, "BACKEND_NOT_READY");
  assert.equal(r.readiness.queue, "unreachable");
  assert.equal(r.incident_key, "WOW_V17:BACKEND_RUNTIME:BACKEND_NOT_READY:QUEUE");
});

test("liveness transport failure does not attempt second probe", async () => {
  const calls = [];
  const r = await probeWowRuntime({ fetchImpl: async (url) => { calls.push(url); throw new Error("secret=never-log"); }, now });
  assert.equal(r.code, "BACKEND_LIVENESS_TRANSPORT_FAILURE");
  assert.equal(r.probes.length, 1);
  assert.equal(calls.length, 1);
  assert.ok(!JSON.stringify(r).includes("secret="));
});

test("malformed readiness response fails closed", async () => {
  const r = await probeWowRuntime({ fetchImpl: responses(HEALTH, { status: "ok", database: "ok" }).fetchImpl, now });
  assert.equal(r.code, "BACKEND_READINESS_CONTRACT_INVALID");
});

test("claimed execution authority fails closed", async () => {
  const r = await probeWowRuntime({ fetchImpl: responses(HEALTH, { ...READY, can_execute: true }).fetchImpl, now });
  assert.equal(r.code, "BACKEND_READINESS_CONTRACT_INVALID");
  assert.equal(r.can_execute, false);
});

test("non-503 HTTP failure retains its transport/status classification", async () => {
  const r = await probeWowRuntime({ fetchImpl: responses(HEALTH, { detail: "unexpected" }, 502).fetchImpl, now });
  assert.equal(r.code, "BACKEND_READINESS_HTTP_502");
});

test("invalid JSON does not masquerade as model outage", async () => {
  let n = 0;
  const r = await probeWowRuntime({ fetchImpl: async () => {
    n += 1;
    return n === 1 ? { status: 200, json: async () => HEALTH } : { status: 200, json: async () => { throw new SyntaxError("bad"); } };
  }, now });
  assert.equal(r.code, "BACKEND_READINESS_INVALID_JSON");
});


test("body interruption after HTTP headers is a transport failure, not invalid JSON", async () => {
  let calls = 0;
  const r = await probeWowRuntime({ fetchImpl: async () => {
    calls += 1;
    if (calls === 1) return { status: 200, json: async () => HEALTH };
    // The HTTP headers have arrived, but the body stream aborts.
    return { status: 200, json: async () => {
      throw new DOMException("response body aborted", "AbortError");
    } };
  }, now });
  assert.equal(calls, 2);
  assert.equal(r.code, "BACKEND_READINESS_TRANSPORT_FAILURE");
  assert.equal(r.status, "BLOCKED");
  assert.equal(r.probes[1].http_status, 200);
  assert.equal(r.can_execute, false);
});

test("liveness body interruption does not continue to readiness", async () => {
  let calls = 0;
  const r = await probeWowRuntime({ fetchImpl: async () => {
    calls += 1;
    return { status: 200, json: async () => {
      throw new DOMException("body aborted", "AbortError");
    } };
  }, now });
  assert.equal(calls, 1);
  assert.equal(r.code, "BACKEND_LIVENESS_TRANSPORT_FAILURE");
  assert.equal(r.probes.length, 1);
});
