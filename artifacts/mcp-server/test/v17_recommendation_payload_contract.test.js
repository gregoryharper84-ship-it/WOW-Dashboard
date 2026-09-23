import assert from "node:assert/strict";
import test from "node:test";

import { V17_TOOLS } from "../src/v17_contract.js";
import { invokeV17Operation } from "../src/v17_backend.js";

const byName = Object.fromEntries(V17_TOOLS.map((entry) => [entry.name, entry]));

for (const name of ["recordWowV17Recommendations", "settleWowV17Recommendations"]) {
  test(`${name} uses the canonical backend rows envelope`, async () => {
    const schema = byName[name].inputSchema;
    assert.deepEqual(schema.required, ["rows"]);
    assert.ok(schema.properties.rows);
    assert.equal(Object.prototype.hasOwnProperty.call(schema.properties, "records"), false);

    let observedBody;
    const fetchImpl = async (_url, request) => {
      observedBody = JSON.parse(request.body);
      return new Response(JSON.stringify({ ok: true, can_execute: false }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };

    const payload = { rows: [{ acceptance_row: "v17-mcp" }] };
    const result = await invokeV17Operation(name, payload, {
      fetchImpl,
      backendKey: "test-only",
      baseUrl: "https://backend.example",
    });

    assert.deepEqual(observedBody, payload);
    assert.equal(result.can_execute, false);
    assert.equal(result.mcp_gateway.can_execute, false);
  });
}
