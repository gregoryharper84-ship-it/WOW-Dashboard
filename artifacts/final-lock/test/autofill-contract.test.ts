import assert from "node:assert/strict";
import { test } from "node:test";
import { legacyAutofillUrl } from "../src/lib/legacy-autofill.ts";

test("legacy L10 autofill uses required GET parameter names and preserves side/line", () => {
  const url = new URL(legacyAutofillUrl({ player: "Example Player", sport: "NBA", prop: "Points", line: "20.5", side: "Under" }), "http://dashboard.local");
  assert.equal(url.pathname, "/api/wow/l10/v2");
  assert.deepEqual(Object.fromEntries(url.searchParams), { player: "Example Player", sport: "NBA", prop: "Points", line: "20.5", direction: "LESS" });
});

test("legacy autofill cannot silently omit sport or exact line", () => {
  assert.throws(() => legacyAutofillUrl({ player: "Example", sport: "", prop: "Points", line: "20.5", side: "Over" }));
  assert.throws(() => legacyAutofillUrl({ player: "Example", sport: "NBA", prop: "Points", line: "", side: "Over" }));
});
