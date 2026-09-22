import assert from "node:assert/strict";
import test from "node:test";

import {
  BACKEND_DEFAULT,
  CAN_EXECUTE,
  TERMINAL_AUTHORITY,
  V17_OPERATIONS,
  V17_TOOLS,
  V17_TOOL_NAMES,
  assertV17Contract,
} from "../src/v17_contract.js";
import {
  V17GatewayError,
  decorateGatewayResult,
  errorPayload,
  invokeV17Operation,
} from "../src/v17_backend.js";
import { createV17McpServer } from "../src/v17_server.js";

const EXPECTED_TOOLS = [
  "getWowV17BackendHealth",
  "getWowV17Governance",
  "getWowV17HostContract",
  "getWowV17DetailedEvidenceContract",
  "getWowV17Capabilities",
  "getWowV17RundownMarketHealth",
  "getWowV17OddsApiMarketHealth",
  "getWowV17CompactEspnDiscovery",
  "scoreWowPickRequest",
  "scoreWowProp",
  "runWowV17DailySnapshot",
  "readWowV17DailySnapshotRowDetail",
  "scoreWowV17TeamEventFromWowHost",
  "lookupWowV17PredictionReceipts",
  "recordWowV17Recommendations",
  "settleWowV17Recommendations",
];

test("V17 MCP exposes exactly the governed Action replacement surface", () => {
  assert.equal(assertV17Contract(), true);
  assert.deepEqual(V17_TOOL_NAMES, EXPECTED_TOOLS);
  assert.equal(V17_TOOLS.length, 16);
  assert.equal(Object.keys(V17_OPERATIONS).length, 16);
});

test("migration invariants preserve terminal authority and no execution", () => {
  assert.equal(CAN_EXECUTE, false);
  assert.equal(TERMINAL_AUTHORITY, "V17_TERMINAL_REDUCER");
  assert.equal(BACKEND_DEFAULT, "https://wow-governed-probability-engine.onrender.com");

  const forbidden = /(place|submit|execute|route|approve|cancel).*(bet|wager|order)|(?:bet|wager|order).*(place|submit|execute|route|approve|cancel)/i;
  assert.deepEqual(V17_TOOL_NAMES.filter((name) => forbidden.test(name)), []);
});

test("read/write annotations do not mislabel stateful scoring as read-only", () => {
  const byName = Object.fromEntries(V17_TOOLS.map((entry) => [entry.name, entry]));
  for (const name of [
    "scoreWowPickRequest",
    "scoreWowProp",
    "runWowV17DailySnapshot",
    "scoreWowV17TeamEventFromWowHost",
    "recordWowV17Recommendations",
    "settleWowV17Recommendations",
  ]) {
    assert.equal(byName[name].annotations.readOnlyHint, false, name);
    assert.equal(byName[name].annotations.destructiveHint, false, name);
  }
  for (const name of [
    "getWowV17BackendHealth",
    "getWowV17Governance",
    "getWowV17HostContract",
    "getWowV17DetailedEvidenceContract",
    "getWowV17Capabilities",
    "getWowV17RundownMarketHealth",
    "getWowV17OddsApiMarketHealth",
    "getWowV17CompactEspnDiscovery",
    "readWowV17DailySnapshotRowDetail",
    "lookupWowV17PredictionReceipts",
  ]) {
    assert.equal(byName[name].annotations.readOnlyHint, true, name);
  }
});

test("Action operation IDs map to the existing V17 backend routes", () => {
  assert.deepEqual(V17_OPERATIONS.scoreWowPickRequest, {
    method: "POST",
    path: "/score-pick-request",
    scope: "wow.predictions.score",
    timeoutMs: 330000,
  });
  assert.equal(V17_OPERATIONS.lookupWowV17PredictionReceipts.path, "/v17/prediction-receipts/lookup");
  assert.equal(V17_OPERATIONS.runWowV17DailySnapshot.path, "/v17/daily-snapshot-run");
  assert.equal(V17_OPERATIONS.scoreWowV17TeamEventFromWowHost.path, "/score-team-event");
  assert.equal(V17_OPERATIONS.recordWowV17Recommendations.path, "/record-recommendations");
  assert.equal(V17_OPERATIONS.settleWowV17Recommendations.path, "/settle-recommendations");
});

test("backend key stays server-side and bearer auth is injected by the gateway", async () => {
  let observedUrl;
  let observedRequest;
  const fetchImpl = async (url, request) => {
    observedUrl = url;
    observedRequest = request;
    return new Response(JSON.stringify({ ok: true, can_execute: false }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  const result = await invokeV17Operation("getWowV17HostContract", {}, {
    fetchImpl,
    backendKey: "server-secret",
    baseUrl: "https://backend.example",
  });

  assert.equal(observedUrl, "https://backend.example/v17/host-contract");
  assert.equal(observedRequest.headers.Authorization, "Bearer server-secret");
  assert.equal(JSON.stringify(result).includes("server-secret"), false);
  assert.equal(result.mcp_gateway.can_execute, false);
});

test("Daily row detail substitutes run_id and forwards only bounded query fields", async () => {
  let observedUrl;
  const fetchImpl = async (url) => {
    observedUrl = url;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  await invokeV17Operation(
    "readWowV17DailySnapshotRowDetail",
    { run_id: "run/abc", offset: 5, limit: 10, ignored: "do-not-forward" },
    { fetchImpl, backendKey: "secret", baseUrl: "https://backend.example" },
  );
  assert.equal(observedUrl, "https://backend.example/v17/daily-snapshot-run/run%2Fabc/rows?offset=5&limit=10");
});

test("upstream typed failures are preserved and never rewritten to MODEL_UNAVAILABLE", async () => {
  const fetchImpl = async () => new Response(
    JSON.stringify({ status: "MODEL_SCORER_FAILED", blocker: "SCORER_EXCEPTION" }),
    { status: 422 },
  );

  await assert.rejects(
    invokeV17Operation(
      "scoreWowPickRequest",
      { request_id: "req-1", rows: [{ row_key: "row-1" }] },
      { fetchImpl, backendKey: "secret", baseUrl: "https://backend.example" },
    ),
    (error) => {
      assert.ok(error instanceof V17GatewayError);
      assert.equal(error.code, "UPSTREAM_TYPED_FAILURE");
      assert.equal(error.details.upstream_payload.status, "MODEL_SCORER_FAILED");
      assert.equal(JSON.stringify(error.details).includes("MODEL_UNAVAILABLE"), false);
      return true;
    },
  );
});

test("transport ambiguity tells Pick Request callers to recover receipts before retry", async () => {
  const fetchImpl = async () => {
    const error = new Error("socket disconnected");
    error.name = "TypeError";
    throw error;
  };
  await assert.rejects(
    invokeV17Operation(
      "scoreWowPickRequest",
      { request_id: "req-2", rows: [] },
      { fetchImpl, backendKey: "secret", baseUrl: "https://backend.example" },
    ),
    (error) => {
      assert.equal(error.code, "ACTION_TRANSPORT_FAILURE");
      assert.equal(error.details.recovery_operation_id, "lookupWowV17PredictionReceipts");
      return true;
    },
  );
});

test("missing backend credential fails closed", async () => {
  await assert.rejects(
    invokeV17Operation("getWowV17Capabilities", {}, { backendKey: "", fetchImpl: async () => { throw new Error("should not run"); } }),
    (error) => error instanceof V17GatewayError && error.code === "BACKEND_AUTH_NOT_CONFIGURED",
  );
});

test("gateway decoration preserves backend payload and adds only safety metadata", () => {
  const out = decorateGatewayResult("getWowV17BackendHealth", { ok: true, runtime_generation: "V17_ACTIVE" });
  assert.equal(out.ok, true);
  assert.equal(out.runtime_generation, "V17_ACTIVE");
  assert.equal(out.mcp_gateway.terminal_authority, "V17_TERMINAL_REDUCER");
  assert.equal(out.mcp_gateway.can_execute, false);
});

test("error payload remains typed and non-executable", () => {
  const out = errorPayload(new V17GatewayError("ACTION_TRANSPORT_FAILURE", "network failed"), "scoreWowPickRequest");
  assert.equal(out.gateway_status, "ACTION_TRANSPORT_FAILURE");
  assert.equal(out.mcp_gateway.can_execute, false);
});

test("MCP server factory loads using the installed protocol SDK", () => {
  const server = createV17McpServer({ backend: { backendKey: "test-only" } });
  assert.ok(server);
});
