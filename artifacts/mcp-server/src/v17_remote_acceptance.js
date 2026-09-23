import http from "node:http";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { V17_TOOL_NAMES } from "./v17_contract.js";

function requireEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name}_REQUIRED`);
  return value;
}

function structured(result) {
  return result?.structuredContent || {};
}

function assert(condition, code, details = {}) {
  if (!condition) {
    const error = new Error(code);
    error.details = details;
    throw error;
  }
}

function assertNonExecuting(payload, code) {
  assert(payload?.can_execute !== true, code, payload);
  assert(payload?.mcp_gateway?.can_execute === false, `${code}_GATEWAY`, payload);
  assert(
    payload?.mcp_gateway?.terminal_authority === "V17_TERMINAL_REDUCER",
    `${code}_TERMINAL_AUTHORITY`,
    payload,
  );
}

function gatewayBlocker(result) {
  const payload = structured(result);
  return result?.isError === true ? payload?.gateway_status : null;
}

async function callProtected(client, name, args = {}) {
  const result = await client.callTool({ name, arguments: args });
  const payload = structured(result);
  assertNonExecuting(payload, `REMOTE_${name}_EXECUTION_AUTHORITY_VIOLATION`);
  const blocker = gatewayBlocker(result);
  assert(
    blocker !== "BACKEND_AUTH_FAILED" && blocker !== "BACKEND_AUTH_MODE_INVALID",
    `REMOTE_${name}_BACKEND_AUTH_FAILED`,
    payload,
  );
  return { result, payload, blocker };
}

async function runProtectedParity(client) {
  const acceptanceId = `mcp-acceptance-${Date.now()}`;
  const futureStart = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();

  const host = await callProtected(client, "getWowV17HostContract");
  assert(host.result?.isError !== true, "REMOTE_HOST_CONTRACT_FAILED", host.payload);

  const scoreArgs = {
    request_id: acceptanceId,
    response_mode: "COMPACT",
    rows: [
      {
        row_key: "acceptance-row-1",
        event_id: `MCP-ACCEPTANCE:${acceptanceId}`,
        event_start_time: futureStart,
        sport: "MCP_ACCEPTANCE",
        player: "MCP Acceptance Fixture",
        stat_type: "STRIKEOUTS",
        line: 1.5,
        direction: "MORE",
        source_type: "NORMALIZED",
        platform: "MCP_ACCEPTANCE_TEST_ONLY",
      },
    ],
  };
  const score = await callProtected(client, "scoreWowPickRequest", scoreArgs);
  assert(
    score.result?.isError !== true,
    "REMOTE_PROTECTED_SCORE_TRANSPORT_FAILED",
    score.payload,
  );
  assert(
    score.payload?.request_id === acceptanceId || score.payload?.durable_run_manifest?.request_id === acceptanceId,
    "REMOTE_PROTECTED_SCORE_REQUEST_ID_MISMATCH",
    score.payload,
  );

  const receipt = await callProtected(client, "lookupWowV17PredictionReceipts", {
    request_id: acceptanceId,
    rows: [
      {
        row_key: "acceptance-row-1",
        event_id: `MCP-ACCEPTANCE:${acceptanceId}`,
        sport: "MCP_ACCEPTANCE",
        player: "MCP Acceptance Fixture",
        stat_type: "STRIKEOUTS",
        line: 1.5,
        direction: "MORE",
      },
    ],
  });
  assert(receipt.result?.isError !== true, "REMOTE_PROTECTED_RECEIPT_LOOKUP_FAILED", receipt.payload);

  const record = await callProtected(client, "recordWowV17Recommendations", {
    research_run_id: acceptanceId,
    request_id: acceptanceId,
    host_identity: "WOW_CUSTOM_GPT",
    model_identity: "WOW_BETTING_ENGINE",
    source_type: "MCP_ACCEPTANCE_TEST_ONLY",
    rows: [
      {
        row_key: "acceptance-recommendation-1",
        sport: "MCP_ACCEPTANCE",
        league: "MCP_ACCEPTANCE",
        event_id: `MCP-ACCEPTANCE:${acceptanceId}`,
        event_start_time: futureStart,
        participant: "MCP Acceptance Fixture",
        opponent: "MCP Acceptance Control",
        selection: "Acceptance transport fixture only",
        terminal_label: "MODEL_QUALIFIED_HOLD",
        probability_publishable: false,
        blockers: ["MCP_ACCEPTANCE_TEST_ONLY"],
        display_payload: { acceptance_only: true, can_execute: false },
      },
    ],
  });
  assert(record.result?.isError !== true, "REMOTE_PROTECTED_RECORD_FAILED", record.payload);
  assert(record.payload?.code === "RECOMMENDATION_LEDGER_WRITE_PASS", "REMOTE_PROTECTED_RECORD_CODE_MISMATCH", record.payload);
  assert(record.payload?.rows_in === 1 && record.payload?.rows_persisted === 1, "REMOTE_PROTECTED_RECORD_RECONCILIATION_FAILED", record.payload);
  const recommendationId = record.payload?.recommendation_record_ids?.[0];
  assert(typeof recommendationId === "string" && recommendationId.length > 0, "REMOTE_PROTECTED_RECORD_ID_MISSING", record.payload);

  const settledAt = new Date().toISOString();
  const settlement = await callProtected(client, "settleWowV17Recommendations", {
    rows: [
      {
        recommendation_record_id: recommendationId,
        settled_at: settledAt,
        settled_result: "VOID",
        official_result: "MCP acceptance transport fixture only; no sporting result asserted",
        settlement_source: "MCP_ACCEPTANCE_TEST_ONLY",
        position_reference: `MCP-ACCEPTANCE:${acceptanceId}`,
        position_structure: "SINGLE",
        underlying_market_count: 1,
      },
    ],
  });
  assert(settlement.result?.isError !== true, "REMOTE_PROTECTED_SETTLEMENT_FAILED", settlement.payload);
  assert(settlement.payload?.code === "RECOMMENDATION_SETTLEMENT_WRITE_PASS", "REMOTE_PROTECTED_SETTLEMENT_CODE_MISMATCH", settlement.payload);
  assert(settlement.payload?.reconciliation_pass === true, "REMOTE_PROTECTED_SETTLEMENT_RECONCILIATION_FAILED", settlement.payload);

  return {
    protected_host_contract: true,
    protected_score_route: true,
    protected_receipt_route: true,
    protected_record_route: true,
    protected_settlement_route: true,
    disposable_recommendation_id: recommendationId,
  };
}

async function runAcceptance() {
  const remoteUrl = new URL(requireEnv("WOW_MCP_REMOTE_URL"));
  const token = requireEnv("WOW_MCP_AUTH_TOKEN");

  const client = new Client(
    { name: "wow-v17-remote-acceptance", version: "1.0.0" },
    { capabilities: {} },
  );
  const transport = new StreamableHTTPClientTransport(remoteUrl, {
    requestInit: {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    },
  });

  try {
    await client.connect(transport);

    const listed = await client.listTools();
    const names = listed.tools.map((tool) => tool.name);
    assert(
      JSON.stringify(names) === JSON.stringify(V17_TOOL_NAMES),
      "REMOTE_TOOL_DISCOVERY_MISMATCH",
      { expected: V17_TOOL_NAMES, actual: names },
    );

    const healthResult = await client.callTool({ name: "getWowV17BackendHealth", arguments: {} });
    const health = structured(healthResult);
    assert(healthResult?.isError !== true, "REMOTE_HEALTH_TOOL_FAILED", health);
    assert(health.status === "ok", "REMOTE_BACKEND_HEALTH_NOT_OK", health);
    assertNonExecuting(health, "REMOTE_HEALTH_EXECUTION_AUTHORITY_VIOLATION");

    const governanceResult = await client.callTool({ name: "getWowV17Governance", arguments: {} });
    const governance = structured(governanceResult);
    assert(governanceResult?.isError !== true, "REMOTE_GOVERNANCE_TOOL_FAILED", governance);
    assert(governance.can_execute === false, "REMOTE_GOVERNANCE_EXECUTION_AUTHORITY_VIOLATION", governance);
    assertNonExecuting(governance, "REMOTE_GOVERNANCE_GATEWAY_VIOLATION");

    const protectedResult = await client.callTool({ name: "getWowV17HostContract", arguments: {} });
    const protectedPayload = structured(protectedResult);
    const protectedBlocker = gatewayBlocker(protectedResult);

    if (protectedBlocker === "BACKEND_AUTH_NOT_CONFIGURED") {
      assertNonExecuting(protectedPayload, "REMOTE_PROTECTED_TOOL_EXECUTION_AUTHORITY_VIOLATION");
      return {
        ok: true,
        acceptance: "REMOTE_MCP_PUBLIC_READS_VERIFIED_FAIL_CLOSED",
        tool_count: names.length,
        exact_tool_discovery: true,
        backend_health: health.status,
        governance_can_execute: governance.can_execute,
        terminal_authority: health.mcp_gateway.terminal_authority,
        protected_operation_blocker: protectedBlocker,
        protected_parity_verified: false,
        can_execute: false,
      };
    }

    assert(protectedResult?.isError !== true, "REMOTE_PROTECTED_HOST_CONTRACT_FAILED", protectedPayload);
    assertNonExecuting(protectedPayload, "REMOTE_PROTECTED_HOST_CONTRACT_EXECUTION_VIOLATION");
    const parity = await runProtectedParity(client);

    return {
      ok: true,
      acceptance: "REMOTE_MCP_PROTECTED_PARITY_VERIFIED",
      tool_count: names.length,
      exact_tool_discovery: true,
      backend_health: health.status,
      governance_can_execute: governance.can_execute,
      terminal_authority: health.mcp_gateway.terminal_authority,
      protected_parity_verified: true,
      ...parity,
      can_execute: false,
    };
  } finally {
    try { await client.close(); } catch {}
  }
}

let summary;
try {
  summary = await runAcceptance();
  console.log(`WOW_V17_REMOTE_ACCEPTANCE ${JSON.stringify(summary)}`);
} catch (error) {
  summary = {
    ok: false,
    acceptance: "REMOTE_MCP_ACCEPTANCE_FAILED",
    code: error?.message || String(error),
    details: error?.details || {},
    can_execute: false,
  };
  console.error(`WOW_V17_REMOTE_ACCEPTANCE ${JSON.stringify(summary)}`);
  process.exitCode = 1;
}

if (process.env.PORT && process.env.WOW_MCP_ACCEPTANCE_KEEPALIVE === "1") {
  const port = Number(process.env.PORT);
  http.createServer((req, res) => {
    if (req.url !== "/healthz") {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, can_execute: false }));
      return;
    }
    res.writeHead(summary.ok ? 200 : 503, { "Content-Type": "application/json" });
    res.end(JSON.stringify(summary));
  }).listen(port, "0.0.0.0", () => {
    console.log(`wow-v17-remote-acceptance listening on :${port}; can_execute=false`);
  });
}
