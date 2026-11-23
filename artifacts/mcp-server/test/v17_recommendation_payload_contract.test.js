import assert from "node:assert/strict";
import test from "node:test";

import {
  RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS,
  V17_TOOLS,
} from "../src/v17_contract.js";
import { V17GatewayError, invokeV17Operation } from "../src/v17_backend.js";

const byName = Object.fromEntries(V17_TOOLS.map((entry) => [entry.name, entry]));

for (const name of ["recordWowV17Recommendations", "settleWowV17Recommendations"]) {
  test(`${name} uses the canonical backend rows envelope`, async () => {
    const schema = byName[name].inputSchema;
    if (name === "recordWowV17Recommendations") {
      assert.deepEqual(schema.required, [...RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS, "rows"]);
    } else {
      assert.deepEqual(schema.required, ["rows"]);
    }
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

    const payload =
      name === "recordWowV17Recommendations"
        ? {
            research_run_id: "run-v17-mcp",
            host_identity: "WOW_CUSTOM_GPT",
            model_identity: "WOW_BETTING_ENGINE",
            source_type: "AUTONOMOUS_DISCOVERY",
            rows: [{ acceptance_row: "v17-mcp" }],
          }
        : { rows: [{ acceptance_row: "v17-mcp" }] };
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

test("recordWowV17Recommendations host schema rejects blank and whitespace-only identity", () => {
  const schema = byName.recordWowV17Recommendations.inputSchema;
  for (const field of RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS) {
    assert.ok(schema.required.includes(field), field);
    const rule = schema.properties[field];
    assert.equal(rule.type, "string", field);
    assert.equal(rule.minLength, 1, field);
    const pattern = new RegExp(rule.pattern);
    for (const blank of ["", " ", "   ", "\t\n", "\u00a0\u00a0"]) {
      const accepted = blank.length >= rule.minLength && pattern.test(blank);
      assert.equal(accepted, false, `${field} accepted ${JSON.stringify(blank)}`);
    }
    assert.equal(pattern.test("WOW_BETTING_ENGINE"), true, field);
  }
  assert.deepEqual(byName.settleWowV17Recommendations.inputSchema.required, ["rows"]);
});

test("whitespace-only recommendation identity fails closed at the MCP edge before transport", async () => {
  let fetched = false;
  const fetchImpl = async () => {
    fetched = true;
    throw new Error("malformed envelope must not reach the backend");
  };
  const payload = {
    research_run_id: "   ",
    host_identity: "\t\n",
    model_identity: " \u00a0 ",
    source_type: "",
    rows: [{ acceptance_row: "v17-mcp" }],
  };

  await assert.rejects(
    invokeV17Operation("recordWowV17Recommendations", payload, {
      fetchImpl,
      backendKey: "test-only",
      baseUrl: "https://backend.example",
    }),
    (error) => {
      assert.ok(error instanceof V17GatewayError);
      assert.equal(error.code, "MCP_REQUEST_ENVELOPE_INVALID");
      assert.deepEqual(error.details.invalid_fields, [...RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS]);
      assert.equal(error.details.can_execute, false);
      for (const field of RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS) {
        assert.ok(error.message.includes(field), field);
      }
      return true;
    },
  );
  assert.equal(fetched, false);

  await assert.rejects(
    invokeV17Operation(
      "recordWowV17Recommendations",
      { ...payload, research_run_id: "run-v17-mcp", host_identity: "WOW_CUSTOM_GPT" },
      { fetchImpl, backendKey: "test-only", baseUrl: "https://backend.example" },
    ),
    (error) => {
      assert.equal(error.code, "MCP_REQUEST_ENVELOPE_INVALID");
      assert.deepEqual(error.details.invalid_fields, ["model_identity", "source_type"]);
      return true;
    },
  );
  assert.equal(fetched, false);
});
