import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const base = "http://127.0.0.1:4184";

test("fixture-only prop normalization rejects unexpected input and never qualifies output", async t => {
  const fixtureServer = fileURLToPath(new URL("./fixtures/testdriver-props-mock.mjs", import.meta.url));
  const child = spawn(process.execPath, [fixtureServer], { stdio: "ignore" });
  t.after(() => { child.kill(); });

  let ready = false;
  for (let attempt = 0; attempt < 30; attempt++) {
    if (child.exitCode !== null) throw new Error("Fixture server exited before health ready");
    try {
      const response = await fetch(base + "/health", { signal: AbortSignal.timeout(1000) });
      const body = await response.json();
      if (response.status === 200 && body.fixture_only === true) {
        ready = true;
        break;
      }
    } catch { /* loopback-only readiness retry */ }
    await delay(100);
  }
  assert.equal(ready, true, "Local fixture health endpoint must become available");

  const valid = await fetch(base + "/api/props/normalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sport: "NBA",
      props: [{ player: "Test Fixture Player", prop_type: "Points", side: "MORE", line: 25.5 }],
    }),
  });
  assert.equal(valid.status, 200);
  const normalized = await valid.json();
  assert.equal(normalized.ok, true);
  assert.equal(normalized.props.length, 1);
  assert.equal(normalized.props[0].source_grade, "UNVERIFIED");
  assert.equal(normalized.props[0].source_status, "UNAVAILABLE");
  assert.equal(normalized.props[0].source, "TEST_FIXTURE_ONLY");
  assert.equal(normalized.props[0].player, "Test Fixture Player");

  const bad = await fetch(base + "/api/props/normalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sport: "NFL", props: [{ player: "Unknown" }] }),
  });
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).ok, false);

  const missing = await fetch(base + "/unexpected");
  assert.equal(missing.status, 404);
});
