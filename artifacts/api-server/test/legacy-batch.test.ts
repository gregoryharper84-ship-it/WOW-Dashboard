import assert from "node:assert/strict";
import { test } from "node:test";
import { scoreLegacyProp } from "../src/lib/legacy-batch.ts";

const prop = { player: "Example", sport: "NBA", prop_type: "points", line: 20.5, side: "MORE", source_status: "RETRIEVED", team_confidence: "HIGH" };

test("HTTP 200 Flask rejection is a failed legacy batch row with native blockers preserved", async () => {
  const row = await scoreLegacyProp(prop, { fetchImpl: async (_url, options) => {
    const sent = JSON.parse(String(options?.body));
    assert.equal(sent.status_confirmed, false);
    assert.equal(sent.line_verified, false);
    return Response.json({ ok: false, classification: "REJECT", blocker_code: "STATUS_NOT_CONFIRMED", blocker_detail: "Player status has not been confirmed" });
  } });
  assert.equal(row.ok, false);
  assert.equal(row.result.blocker_code, "STATUS_NOT_CONFIRMED");
  assert.equal(row.result.classification, "REJECT");
  assert.equal(row.result.can_execute, false);
});

test("non-2xx, malformed and transport failures cannot become successful scored rows", async () => {
  for (const fetchImpl of [
    async () => Response.json({ error: "upstream invalid" }, { status: 422 }),
    async () => new Response("<html>failure</html>"),
    async () => Response.json({ ok: true, can_execute: true, terminal_label: "FINAL_APPROVED" }),
    async () => { throw new Error("secret network detail"); },
  ]) {
    const row = await scoreLegacyProp(prop, { fetchImpl });
    assert.equal(row.ok, false);
    assert.equal(row.result.can_execute, false);
    assert.ok(!JSON.stringify(row).includes("secret network detail"));
  }
});

test("explicit confirmed inputs are preserved, source grade cannot fabricate confirmation", async () => {
  await scoreLegacyProp({ ...prop, status_confirmed: true, line_verified: true }, { fetchImpl: async (_url, options) => {
    const sent = JSON.parse(String(options?.body));
    assert.equal(sent.status_confirmed, true);
    assert.equal(sent.line_verified, true);
    return Response.json({ ok: true, classification: "WATCH", blocker_code: "DATA_QUALITY_HOLD" });
  } }).then(row => assert.equal(row.ok, true));
});
