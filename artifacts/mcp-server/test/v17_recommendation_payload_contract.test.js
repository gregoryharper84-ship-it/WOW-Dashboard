import assert from "node:assert/strict";
import test from "node:test";

import {
  RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS,
  V17_TOOLS,
} from "../src/v17_contract.js";
import { V17GatewayError, invokeV17Operation } from "../src/v17_backend.js";

const byName = Object.fromEntries(V17_TOOLS.map((entry) => [entry.name, entry]));

const RECORD_ENVELOPE = {
  research_run_id: "run-v17-mcp",
  host_identity: "WOW_CUSTOM_GPT",
  model_identity: "WOW_BETTING_ENGINE",
  source_type: "AUTONOMOUS_DISCOVERY",
};

const EXPECTED_REQUIRED = {
  recordWowV17Recommendations: [...RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS, "rows"],
  settleWowV17Recommendations: ["rows"],
};

const GATEWAY_OPTIONS = { backendKey: "test-only", baseUrl: "https://backend.example" };

for (const name of ["recordWowV17Recommendations", "settleWowV17Recommendations"]) {
  test(`${name} uses the canonical backend rows envelope`, async () => {
    const schema = byName[name].inputSchema;
    assert.deepEqual(schema.required, EXPECTED_REQUIRED[name]);
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

    const payload = {
      ...(name === "recordWowV17Recommendations" ? RECORD_ENVELOPE : {}),
      rows: [{ acceptance_row: "v17-mcp" }],
    };
    const result = await invokeV17Operation(name, payload, { ...GATEWAY_OPTIONS, fetchImpl });

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
    // minLength keeps the empty string invalid on hosts that ignore pattern;
    // pattern is what makes a whitespace-only value invalid everywhere.
    assert.equal(rule.minLength, 1, field);
    const pattern = new RegExp(rule.pattern);
    for (const blank of ["", " ", "   ", "\t\n", "\u00a0\u00a0"]) {
      assert.equal(pattern.test(blank), false, `${field} accepted ${JSON.stringify(blank)}`);
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

  async function assertEnvelopeRejected(candidate, invalidFields) {
    await assert.rejects(
      invokeV17Operation("recordWowV17Recommendations", candidate, { ...GATEWAY_OPTIONS, fetchImpl }),
      (error) => {
        assert.ok(error instanceof V17GatewayError);
        assert.equal(error.code, "MCP_REQUEST_ENVELOPE_INVALID");
        assert.deepEqual(error.details.invalid_fields, invalidFields);
        return true;
      },
    );
  }

  await assertEnvelopeRejected(payload, [...RECOMMENDATION_ENVELOPE_IDENTITY_FIELDS]);
  await assertEnvelopeRejected(
    { ...payload, research_run_id: "run-v17-mcp", host_identity: "WOW_CUSTOM_GPT" },
    ["model_identity", "source_type"],
  );
  assert.equal(fetched, false);
});
