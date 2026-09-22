import test from "node:test";
import assert from "node:assert/strict";

import { invokeV17Operation, V17GatewayError } from "../src/v17_backend.js";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("acceptance credential mode sends dedicated marker and bearer", async () => {
  let request;
  const result = await invokeV17Operation("getWowV17HostContract", {}, {
    backendCredentialMode: "acceptance",
    backendKey: "acceptance-test-key",
    baseUrl: "https://example.invalid",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return jsonResponse({ status: "ACTIVE", can_execute: false });
    },
  });

  assert.equal(request.url, "https://example.invalid/v17/host-contract");
  assert.equal(request.options.headers.Authorization, "Bearer acceptance-test-key");
  assert.equal(request.options.headers["X-WOW-MCP-Acceptance"], "V17_MCP_ACCEPTANCE_ONLY");
  assert.equal(result.can_execute, false);
  assert.equal(result.mcp_gateway.can_execute, false);
});

test("normal action credential mode never sends acceptance marker", async () => {
  let request;
  await invokeV17Operation("getWowV17HostContract", {}, {
    backendCredentialMode: "action",
    backendKey: "action-test-key",
    baseUrl: "https://example.invalid",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return jsonResponse({ status: "ACTIVE", can_execute: false });
    },
  });

  assert.equal(request.options.headers.Authorization, "Bearer action-test-key");
  assert.equal(request.options.headers["X-WOW-MCP-Acceptance"], undefined);
});

test("invalid backend credential mode fails before upstream fetch", async () => {
  let fetchCalled = false;
  await assert.rejects(
    invokeV17Operation("getWowV17HostContract", {}, {
      backendCredentialMode: "unexpected",
      backendKey: "never-used",
      baseUrl: "https://example.invalid",
      fetchImpl: async () => {
        fetchCalled = true;
        return jsonResponse({});
      },
    }),
    (error) => {
      assert.ok(error instanceof V17GatewayError);
      assert.equal(error.code, "BACKEND_AUTH_MODE_INVALID");
      return true;
    },
  );
  assert.equal(fetchCalled, false);
});
