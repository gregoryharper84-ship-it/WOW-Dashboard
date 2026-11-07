// Opt-in, zero-browser-credit authenticated HTTP smoke; no temporary tunnels.
// Run: node --test tests/llp_readback.test.js
// Set WOW_LLP_READBACK_INTEGRATION_ENABLED=true, WOW_LLP_READBACK_BASE_URL,
// WOW_LLP_READBACK_RUN_ID (with a saved LLP team event), and
// WOW_LLP_READBACK_BEARER. Without all four, the network test skips.
import assert from "node:assert/strict";
import test from "node:test";

const enabled = process.env.WOW_LLP_READBACK_INTEGRATION_ENABLED === "true";
const base = process.env.WOW_LLP_READBACK_BASE_URL;
const runId = process.env.WOW_LLP_READBACK_RUN_ID;
const bearer = process.env.WOW_LLP_READBACK_BEARER;
const configured = enabled && Boolean(base && runId && bearer);
const PAGE_LIMIT = 25;
const MAX_PAGES = 40; // Bounded to 1,000 real persisted source rows.

function makeReadbackURL(baseUrl, run, offset = 0) {
  const origin = new URL(baseUrl);
  const localhost = ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
  assert.ok(
    origin.protocol === "https:" || (origin.protocol === "http:" && localhost),
    "Remote LLP readback endpoints must use HTTPS",
  );
  assert.ok(!origin.username && !origin.password && !origin.search && !origin.hash,
    "Readback base URL cannot carry credentials or query parameters");
  assert.match(run, /^[a-zA-Z0-9_-]{1,100}$/, "Run ID must be a bounded token");
  // Preserve an existing gateway prefix like /functions/v1/wow-llp-action-gateway.
  // A root URL remains valid for directly hosted FastAPI.
  const prefix = origin.pathname.replace(/\/+$/, "");
  const endpoint = new URL(
    `${prefix}/v17/llp-team-intelligence/${encodeURIComponent(run)}/events`,
    origin.origin,
  );
  endpoint.searchParams.set("offset", String(offset));
  endpoint.searchParams.set("limit", String(PAGE_LIMIT));
  return endpoint;
}

test("readback URL preserves the owned gateway prefix", () => {
  assert.equal(
    makeReadbackURL("https://example.org/functions/v1/wow-llp-action-gateway", "run_1", 25).pathname,
    "/functions/v1/wow-llp-action-gateway/v17/llp-team-intelligence/run_1/events",
  );
  assert.equal(
    makeReadbackURL("https://example.org/", "run_1").pathname,
    "/v17/llp-team-intelligence/run_1/events",
  );
  assert.throws(() => makeReadbackURL("http://example.org", "run_1"), /HTTPS/);
  assert.throws(() => makeReadbackURL("https://example.org?secret=bad", "run_1"), /credentials/);
});

test("owned LLP endpoint enforces bearer and returns a saved event", {
  skip: !configured && "Opt-in owned integration backend, saved run and bearer are required",
}, async () => {
  const endpoint = makeReadbackURL(base, runId);
  const anonymous = await fetch(endpoint, { redirect: "manual", signal: AbortSignal.timeout(15000) });
  assert.ok([401, 403].includes(anonymous.status),
    "Unauthenticated readback must reject the call with HTTP 401 or 403");

  let offset = 0;
  let found = false;
  const visited = new Set();
  for (let page = 0; page < MAX_PAGES; page++) {
    assert.ok(!visited.has(offset), "Source pagination must advance");
    visited.add(offset);
    const authorized = await fetch(makeReadbackURL(base, runId, offset), {
      headers: { Authorization: `Bearer ${bearer}` },
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
    });
    assert.equal(authorized.status, 200, "Authenticated readback must succeed");
    const response = await authorized.json();
    assert.equal(response.can_execute, false);
    assert.ok(Array.isArray(response.events), "Response must include an events array");
    assert.ok(response.events.every(event => ["NFL", "NBA", "NHL"].includes(event.sport)),
      "Only supported LLP sports may be projected");
    if (response.events.length > 0) {
      found = true;
      break;
    }
    const next = response.source_next_offset;
    if (next === null || next === undefined) break;
    assert.ok(Number.isInteger(next) && next > offset,
      "Source pagination must return an increasing offset");
    offset = next;
  }
  assert.ok(found,
    "Configured saved test run must contain a supported LLP event in the bounded pages");
});
