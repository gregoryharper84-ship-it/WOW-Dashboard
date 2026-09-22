import test from "node:test";
import assert from "node:assert/strict";

import { invokeV17Operation, V17GatewayError } from "../src/v17_backend.js";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("public backend health can be read without WOW_ACTION_API_KEY", async () => {
  let request;
  const result = await invokeV17Operation("getWowV17BackendHealth", {}, {
    backendKey: "",
    baseUrl: "https://example.invalid",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return jsonResponse({ status: "ok", can_execute: false });
    },
  });

  assert.equal(request.url, "https://example.invalid/health");
  assert.equal(request.options.headers.Authorization, undefined);
  assert.equal(result.status, "ok");
  assert.equal(result.mcp_gateway.can_execute, false);
});

test("public governance can be read without WOW_ACTION_API_KEY", async () => {
  let request;
  const result = await invokeV17Operation("getWowV17Governance", {}, {
    backendKey: "",
    baseUrl: "https://example.invalid",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return jsonResponse({
        runtime_generation: "V17_ACTIVE",
        terminal_authority: "V17_TERMINAL_REDUCER",
        can_execute: false,
      });
    },
  });

  assert.equal(request.url, "https://example.invalid/governance");
  assert.equal(request.options.headers.Authorization, undefined);
  assert.equal(result.terminal_authority, "V17_TERMINAL_REDUCER");
  assert.equal(result.mcp_gateway.can_execute, false);
});

test("protected backend operations still fail closed without WOW_ACTION_API_KEY", async () => {
  let fetchCalled = false;

  await assert.rejects(
    invokeV17Operation("getWowV17HostContract", {}, {
      backendKey: "",
      baseUrl: "https://example.invalid",
      fetchImpl: async () => {
        fetchCalled = true;
        return jsonResponse({});
      },
    }),
    (error) => {
      assert.ok(error instanceof V17GatewayError);
      assert.equal(error.code, "BACKEND_AUTH_NOT_CONFIGURED");
      return true;
    },
  );

  assert.equal(fetchCalled, false);
});
