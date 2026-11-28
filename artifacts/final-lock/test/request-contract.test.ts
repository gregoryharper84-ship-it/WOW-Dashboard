import assert from "node:assert/strict";
import { test } from "node:test";
import { canonicalRequest, receiptRecovery, publicationText } from "../src/lib/v17-request.ts";
import { API_ROOT } from "../src/lib/api.ts";

const row = { row_key: "r1", event_id: "provider:event1", event_start_time: "2026-11-07T20:00:00Z", player: "Example", sport: "NBA", stat_type: "POINTS", line: 20.5, direction: "MORE" as const };

test("canonical submission and recovery preserve exact identity without caller probabilities", () => {
  const request = canonicalRequest("req1", row);
  assert.deepEqual(request, { request_id: "req1", response_mode: "COMPACT", rows: [{ ...row, source_type: "NORMALIZED" }] });
  const recovery = receiptRecovery(request);
  assert.deepEqual(recovery, { request_id: "req1", rows: [{ row_key: row.row_key, event_id: row.event_id, player: row.player, sport: row.sport, stat_type: row.stat_type, line: row.line, direction: row.direction }] });
  assert.equal(API_ROOT, "/api");
});

test("missing event timezone, non-numeric lines and missing identity are rejected", () => {
  for (const change of [{ event_start_time: "2026-11-07T20:00:00" }, { line: Number.NaN }, { event_id: "" }]) {
    assert.throws(() => canonicalRequest("req1", { ...row, ...change }));
  }
});

test("publication status is never inferred from successful HTTP or model values", () => {
  assert.equal(publicationText({ calibrated_probability: 0.9, ok: true }), "Publication status not provided");
  assert.equal(publicationText({ probability_publishable: false, calibrated_probability: 0.9 }), "Publishable: false");
});
