import assert from "node:assert/strict";
import { test } from "node:test";
import express from "express";
import { createV17Proxy } from "../src/routes/v17-proxy.ts";
import { scoreCompletionUnknown } from "../../final-lock/src/lib/v17-request.ts";

const backendKey = "server-held-backend-secret";
const dashboardKey = "independent-dashboard-secret";
const config = { backendUrl: "https://wow-governed-probability-engine.onrender.com", backendKey, dashboardKey };

async function withProxy(
  options: Parameters<typeof createV17Proxy>[0],
  run: (base: string) => Promise<void>,
) {
  const app = express();
  app.use(express.json());
  app.use("/api/v17", createV17Proxy(options));
  const server = app.listen(0, "127.0.0.1");
  await new Promise<void>(resolve => server.on("listening", resolve));
  const address = server.address() as { port: number };
  try { await run(`http://127.0.0.1:${address.port}/api/v17`); }
  finally { await new Promise<void>(resolve => server.close(() => resolve())); }
}

test("missing dashboard auth or backend configuration fails closed before fetching", async () => {
  let calls = 0;
  const fetchImpl: typeof fetch = async () => { calls++; return Response.json({}); };
  await withProxy({ ...config, fetchImpl }, async base => {
    const response = await fetch(`${base}/capabilities`);
    assert.equal(response.status, 401);
    assert.equal((await response.json()).code, "DASHBOARD_AUTH_REQUIRED");
  });
  for (const missing of [{ dashboardKey: "" }, { backendKey: "" }]) {
    await withProxy({ ...config, ...missing, fetchImpl }, async base => {
      const response = await fetch(`${base}/capabilities`, { headers: { "X-Dashboard-Key": dashboardKey } });
      assert.equal(response.status, 503);
      assert.equal((await response.json()).can_execute, false);
    });
  }
  assert.equal(calls, 0);
});

test("canonical score uses server Bearer and preserves HTTP status, row identity and typed holds", async () => {
  const request = { request_id: "stable-request", rows: [{ row_key: "stable-row", event_id: "event-123" }] };
  const receipt = { ok: false, rows: [{ row_key: "stable-row", code: "MODEL_INPUTS_INSUFFICIENT", probability_publishable: false, rank_eligible: false, can_execute: false }], can_execute: false };
  let calls = 0;
  await withProxy({ ...config, fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url, `${config.backendUrl}/score-pick-request`);
    assert.equal(new Headers(options?.headers).get("Authorization"), `Bearer ${backendKey}`);
    assert.equal(new Headers(options?.headers).get("X-Dashboard-Key"), null);
    assert.equal(options?.redirect, "error");
    assert.deepEqual(JSON.parse(String(options?.body)), request);
    return Response.json(receipt, { status: 422 });
  } }, async base => {
    const response = await fetch(`${base}/score-pick-request`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify(request) });
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), receipt);
  });
  assert.equal(calls, 1);
});

test("dashboard and backend credentials must be distinct", async () => {
  let calls = 0;
  await withProxy({ ...config, backendKey: dashboardKey, fetchImpl: async () => { calls++; return Response.json({}); } }, async base => {
    const response = await fetch(`${base}/capabilities`, { headers: { "X-Dashboard-Key": dashboardKey } });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).code, "DASHBOARD_CREDENTIAL_SEPARATION_REQUIRED");
  });
  assert.equal(calls, 0);
});

test("receipt recovery uses its allowlisted read operation and never scores again", async () => {
  let calls = 0;
  await withProxy({ ...config, fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url, `${config.backendUrl}/v17/prediction-receipts/lookup`);
    assert.equal(options?.method, "POST");
    return Response.json({ request_id: "same-request", rows: [], can_execute: false });
  } }, async base => {
    const response = await fetch(`${base}/prediction-receipts/lookup`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify({ request_id: "same-request", rows: [{ prediction_id: "p1" }] }) });
    assert.equal(response.status, 200);
  });
  assert.equal(calls, 1);
});

test("native top-level HTTP error envelopes remain unchanged without invented row receipts", async () => {
  const native = { detail: { code: "MODEL_INPUTS_INSUFFICIENT", probability_publishable: false, can_execute: false } };
  await withProxy({ ...config, fetchImpl: async () => Response.json(native, { status: 409 }) }, async base => {
    const response = await fetch(`${base}/score-pick-request`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify({ request_id: "same-request", rows: [{}] }) });
    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), native);
  });
});

for (const status of [500, 502, 503, 504]) {
  test(`native scoring HTTP ${status} retains recovery identity and backend error without rescore`, async () => {
    for (const upstreamRequestId of [undefined, "untrusted-upstream-request"]) {
      const native = {
        code: "NATIVE_SCORING_FAILURE",
        detail: { code: "PICK_REQUEST_RESPONSE_INVALID", can_execute: false },
        blockers: ["NATIVE_RECEIPT_UNAVAILABLE"],
        ...(upstreamRequestId ? { request_id: upstreamRequestId } : {}),
      };
      let scoreCalls = 0;
      await withProxy({ ...config, fetchImpl: async () => {
        scoreCalls++;
        return Response.json(native, { status });
      } }, async base => {
        const response = await fetch(`${base}/score-pick-request`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify({ request_id: "trusted-submitted-request", rows: [{ row_key: "original-row" }] }) });
        const payload = await response.json();
        assert.equal(response.status, status);
        assert.deepEqual(payload, {
          ...native,
          request_id: "trusted-submitted-request",
          completion_unknown: true,
          recovery_operation: "prediction-receipts/lookup",
        });
        assert.equal(scoreCompletionUnknown(payload), true);
        assert.equal("rows" in payload, false);
      });
      assert.equal(scoreCalls, 1);
    }
  });
}

test("native scoring server errors retain supplied receipts while overriding unsafe recovery metadata", async () => {
  const native = {
    code: "NATIVE_PARTIAL_RESPONSE",
    request_id: "untrusted-upstream-request",
    completion_unknown: false,
    recovery_operation: "score-pick-request",
    detail: { code: "BACKEND_POST_PERSISTENCE_FAILURE", can_execute: false },
    rows: [{ row_key: "original-row", prediction_id: "immutable-prediction", blockers: ["NATIVE_HOLD"], can_execute: false }],
  };
  await withProxy({ ...config, fetchImpl: async () => Response.json(native, { status: 500 }) }, async base => {
    const response = await fetch(`${base}/score-pick-request`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify({ request_id: "trusted-submitted-request", rows: [{ row_key: "original-row" }] }) });
    assert.equal(response.status, 500);
    assert.deepEqual(await response.json(), {
      ...native,
      request_id: "trusted-submitted-request",
      completion_unknown: true,
      recovery_operation: "prediction-receipts/lookup",
    });
  });
});

test("local pre-dispatch score failures remain known and never claim upstream recovery", async () => {
  let calls = 0;
  const fetchImpl: typeof fetch = async () => { calls++; return Response.json({}); };
  const request = { request_id: "never-dispatched", rows: [{}] };
  for (const scenario of [
    { options: {}, authenticated: false, status: 401, code: "DASHBOARD_AUTH_REQUIRED" },
    { options: { dashboardKey: "" }, status: 503, code: "DASHBOARD_AUTH_NOT_CONFIGURED" },
    { options: { backendKey: "" }, status: 503, code: "BACKEND_AUTH_NOT_CONFIGURED" },
    { options: { backendUrl: "http://unsafe.invalid" }, status: 503, code: "BACKEND_URL_INVALID" },
    { options: { backendKey: dashboardKey }, status: 503, code: "DASHBOARD_CREDENTIAL_SEPARATION_REQUIRED" },
    { options: {}, query: "?target=unsupported", status: 400, code: "DASHBOARD_QUERY_UNSUPPORTED" },
    { options: {}, body: { request_id: "never-dispatched", rows: [] }, status: 400, code: "DASHBOARD_INPUT_INVALID" },
  ]) {
    await withProxy({ ...config, ...scenario.options, fetchImpl }, async base => {
      const response = await fetch(`${base}/score-pick-request${scenario.query ?? ""}`, {
        method: "POST",
        headers: { ...(scenario.authenticated === false ? {} : { "X-Dashboard-Key": dashboardKey }), "Content-Type": "application/json" },
        body: JSON.stringify(scenario.body ?? request),
      });
      const payload = await response.json();
      assert.equal(response.status, scenario.status);
      assert.equal(payload.code, scenario.code);
      assert.equal(scoreCompletionUnknown(payload), false);
      assert.equal("recovery_operation" in payload, false);
      assert.equal("completion_unknown" in payload, false);
    });
  }
  assert.equal(calls, 0);
});

test("unknown/internal routes cannot reach an authenticated upstream", async () => {
  let calls = 0;
  await withProxy({ ...config, fetchImpl: async () => { calls++; return Response.json({}); } }, async base => {
    for (const path of ["/internal/admin", "/score-pick-request/extra", "/capabilities?target=https://evil.invalid"]) {
      const response = await fetch(`${base}${path}`, { headers: { "X-Dashboard-Key": dashboardKey } });
      assert.ok([400, 404].includes(response.status));
    }
  });
  assert.equal(calls, 0);
});

test("unsafe URL, execution authority, non-JSON and credential echo return typed failures", async () => {
  for (const backendUrl of ["http://example.com", "https://user:pass@example.com", "https://example.com/internal", "https://example.com/?x=y"]) {
    await withProxy({ ...config, backendUrl }, async base => {
      const response = await fetch(`${base}/capabilities`, { headers: { "X-Dashboard-Key": dashboardKey } });
      assert.equal(response.status, 503);
      assert.equal((await response.json()).code, "BACKEND_URL_INVALID");
    });
  }
  for (const [payload, expected] of [
    [{ rows: [{ can_execute: true }] }, "UPSTREAM_GOVERNANCE_VIOLATION"],
    [{ debug: backendKey }, "UPSTREAM_CREDENTIAL_DISCLOSURE_BLOCKED"],
  ] as const) {
    await withProxy({ ...config, fetchImpl: async () => Response.json(payload) }, async base => {
      const response = await fetch(`${base}/capabilities`, { headers: { "X-Dashboard-Key": dashboardKey } });
      assert.equal(response.status, 502);
      const text = await response.text();
      assert.equal(JSON.parse(text).code, expected);
      assert.ok(!text.includes(backendKey));
    });
  }
  await withProxy({ ...config, fetchImpl: async () => new Response("<html>private upstream page</html>") }, async base => {
    const response = await fetch(`${base}/capabilities`, { headers: { "X-Dashboard-Key": dashboardKey } });
    assert.equal((await response.json()).code, "UPSTREAM_RESPONSE_INVALID");
  });
});

test("timeout is an ambiguous completion with recovery guidance, without retry", async () => {
  let calls = 0;
  await withProxy({ ...config, fetchImpl: async () => { calls++; throw new DOMException("private detail", "TimeoutError"); } }, async base => {
    const response = await fetch(`${base}/score-pick-request`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify({ request_id: "stable-request", rows: [{}] }) });
    const payload = await response.json();
    assert.equal(response.status, 504);
    assert.equal(payload.code, "ACTION_TRANSPORT_TIMEOUT");
    assert.equal(payload.recovery_operation, "prediction-receipts/lookup");
    assert.equal(payload.request_id, "stable-request");
    assert.equal(payload.can_execute, false);
    assert.ok(!JSON.stringify(payload).includes("private detail"));
  });
  assert.equal(calls, 1);
});

test("JSON escapes cannot disclose backend/dashboard credentials in strings or property names", async () => {
  const escape = (secret: string) => [...secret].map(char => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`).join("");
  for (const raw of [
    `{"nested":[{"debug":"prefix-${escape(backendKey)}-suffix"}]}`,
    `{"${escape(dashboardKey)}":true}`,
  ]) {
    assert.ok(!raw.includes(backendKey) && !raw.includes(dashboardKey));
    await withProxy({ ...config, fetchImpl: async () => new Response(raw) }, async base => {
      const response = await fetch(`${base}/capabilities`, { headers: { "X-Dashboard-Key": dashboardKey } });
      const output = await response.text();
      assert.equal(response.status, 502);
      assert.equal(JSON.parse(output).code, "UPSTREAM_CREDENTIAL_DISCLOSURE_BLOCKED");
      assert.ok(!output.includes(backendKey) && !output.includes(dashboardKey));
    });
  }
});

test("unusable responses after score dispatch retain ambiguous completion and receipt-only recovery", async () => {
  for (const responseFactory of [
    () => new Response("<html>not a receipt</html>"),
    () => Response.json([]),
    () => Response.json({ rows: [null] }),
    () => Response.json({ rows: [{ matches: [null] }] }),
    () => Response.json({}),
    () => Response.json({ debug: backendKey }),
    () => Response.json({ rows: [{ can_execute: true }] }),
  ]) {
    let calls = 0;
    await withProxy({ ...config, fetchImpl: async () => { calls++; return responseFactory(); } }, async base => {
      const response = await fetch(`${base}/score-pick-request`, { method: "POST", headers: { "X-Dashboard-Key": dashboardKey, "Content-Type": "application/json" }, body: JSON.stringify({ request_id: "same-request", rows: [{}] }) });
      const payload = await response.json();
      assert.equal(response.status, 502);
      assert.equal(payload.completion_unknown, true);
      assert.equal(payload.request_id, "same-request");
      assert.equal(payload.recovery_operation, "prediction-receipts/lookup");
      assert.equal(scoreCompletionUnknown(payload), true);
    });
    assert.equal(calls, 1);
  }
  assert.equal(scoreCompletionUnknown({ code: "DASHBOARD_INPUT_INVALID", can_execute: false }), false);
});
