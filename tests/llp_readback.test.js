// Optional zero-credit HTTP integration smoke. Run with:
// node --test tests/llp_readback.test.js
// Supply WOW_LLP_READBACK_INTEGRATION_ENABLED=true, WOW_LLP_READBACK_BASE_URL,
// WOW_LLP_READBACK_RUN_ID (a known persisted test run), and WOW_LLP_READBACK_BEARER.
// Without those inputs this test SKIPS; it never provisions a metered browser.
import assert from "node:assert/strict";
import test from "node:test";

const enabled = process.env.WOW_LLP_READBACK_INTEGRATION_ENABLED === "true";
const base = process.env.WOW_LLP_READBACK_BASE_URL;
const runId = process.env.WOW_LLP_READBACK_RUN_ID;
const bearer = process.env.WOW_LLP_READBACK_BEARER;
const configured = enabled && Boolean(base && runId && bearer);

test("owned LLP endpoint rejects unauthenticated calls and reads saved events", {
  skip: !configured && "Opt-in owned integration endpoint, test run and bearer are required",
}, async () => {
  const origin = new URL(base);
  const localhost = ["localhost", "127.0.0.1", "::1"].includes(origin.hostname);
  assert.ok(origin.protocol === "https:" || (origin.protocol === "http:" && localhost),
    "Remote LLP readback endpoints must use HTTPS");
  assert.ok(!origin.username && !origin.password && !origin.search && !origin.hash,
    "The readback origin cannot carry inline credentials or query parameters");
  assert.match(runId, /^[a-zA-Z0-9_-]{1,100}$/, "Run ID must be a bounded token");

  const endpoint = new URL(
    `/v17/llp-team-intelligence/${encodeURIComponent(runId)}/events`, origin
  );
  const anonymous = await fetch(endpoint, { redirect: "manual" });
  assert.ok([401, 403].includes(anonymous.status),
    "Unauthenticated readback must fail closed with HTTP 401 or 403");

  const authorized = await fetch(endpoint, {
    headers: { Authorization: `Bearer ${bearer}` },
    redirect: "manual",
  });
  assert.equal(authorized.status, 200, "Authenticated readback must succeed");
  const response = await authorized.json();
  assert.equal(response.can_execute, false);
  assert.ok(Array.isArray(response.events) && response.events.length > 0,
    "Known persisted test run must return saved events (not an empty mock)");
  assert.ok(response.events.every(event => ["NFL", "NBA", "NHL"].includes(event.sport)),
    "Only the supported LLP sport families are allowed");
});
