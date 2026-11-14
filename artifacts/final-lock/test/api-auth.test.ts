import assert from "node:assert/strict";
import { test } from "node:test";
import { apiFetch, setDashboardKey } from "../src/lib/api.ts";

test("dashboard access key is attached only to the same-origin API namespace", async () => {
  const realFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (_input, options) => {
    calls++;
    assert.equal(new Headers(options?.headers).get("X-Dashboard-Key"), "dashboard-only");
    assert.equal(options?.redirect, "error");
    return Response.json({ ok: false });
  };
  try {
    setDashboardKey("dashboard-only");
    await apiFetch("/api/v17/capabilities");
    for (const target of ["https://outside.invalid/api/capabilities", "/other", "//outside.invalid/api/test"]) {
      assert.throws(() => apiFetch(target));
    }
    assert.equal(calls, 1);
  } finally { globalThis.fetch = realFetch; setDashboardKey(""); }
});
