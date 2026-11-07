import assert from "node:assert/strict";
import { test } from "node:test";

test("V17 server boots without legacy DB and API parsing failures stay typed JSON", async () => {
  delete process.env.DATABASE_URL;
  delete process.env.WOW_V17_BACKEND_API_KEY;
  process.env.NODE_ENV = "production";
  process.env.LOG_LEVEL = "silent";
  process.env.WOW_DASHBOARD_API_KEY = "isolated-dashboard-key";
  const { default: app } = await import("../src/app.ts");
  const server = app.listen(0, "127.0.0.1");
  await new Promise<void>(resolve => server.on("listening", resolve));
  const { port } = server.address() as { port: number };
  const base = `http://127.0.0.1:${port}/api`;
  const headers = { "X-Dashboard-Key": "isolated-dashboard-key" };
  try {
    const health = await fetch(`${base}/healthz`);
    assert.equal(health.status, 200);
    const legacy = await fetch(`${base}/props/providers`, { headers });
    assert.equal(legacy.status, 503);
    assert.equal((await legacy.json()).code, "LEGACY_DATABASE_NOT_CONFIGURED");
    const v17 = await fetch(`${base}/v17/capabilities`, { headers });
    assert.equal(v17.status, 503);
    assert.equal((await v17.json()).code, "BACKEND_AUTH_NOT_CONFIGURED");
    const invalid = await fetch(`${base}/v17/score-pick-request`, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: "{invalid-json" });
    assert.equal(invalid.status, 400);
    assert.equal((await invalid.json()).code, "DASHBOARD_INPUT_INVALID");
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});
