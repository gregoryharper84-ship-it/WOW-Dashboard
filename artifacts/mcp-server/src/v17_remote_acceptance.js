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

async function runAcceptance() {
  const remoteUrl = new URL(requireEnv("WOW_MCP_REMOTE_URL"));
  const token = requireEnv("WOW_MCP_AUTH_TOKEN");

  const client = new Client(
    { name: "wow-v17-remote-acceptance", version: "1.0.0" },
    { capabilities: {} },
  );
  const transport = new StreamableHTTPClientTransport(remoteUrl, {
    authProvider: { token: async () => token },
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
    assert(health.mcp_gateway?.can_execute === false, "REMOTE_HEALTH_EXECUTION_AUTHORITY_VIOLATION", health);
    assert(
      health.mcp_gateway?.terminal_authority === "V17_TERMINAL_REDUCER",
      "REMOTE_HEALTH_TERMINAL_AUTHORITY_MISMATCH",
      health,
    );

    const governanceResult = await client.callTool({ name: "getWowV17Governance", arguments: {} });
    const governance = structured(governanceResult);
    assert(governanceResult?.isError !== true, "REMOTE_GOVERNANCE_TOOL_FAILED", governance);
    assert(governance.can_execute === false, "REMOTE_GOVERNANCE_EXECUTION_AUTHORITY_VIOLATION", governance);
    assert(governance.mcp_gateway?.can_execute === false, "REMOTE_GATEWAY_EXECUTION_AUTHORITY_VIOLATION", governance);

    const protectedResult = await client.callTool({ name: "getWowV17HostContract", arguments: {} });
    const protectedPayload = structured(protectedResult);
    assert(protectedResult?.isError === true, "REMOTE_PROTECTED_TOOL_DID_NOT_FAIL_CLOSED", protectedPayload);
    assert(
      protectedPayload.gateway_status === "BACKEND_AUTH_NOT_CONFIGURED",
      "REMOTE_PROTECTED_TOOL_WRONG_BLOCKER",
      protectedPayload,
    );
    assert(protectedPayload.can_execute === false, "REMOTE_PROTECTED_TOOL_EXECUTION_AUTHORITY_VIOLATION", protectedPayload);

    return {
      ok: true,
      acceptance: "REMOTE_MCP_PUBLIC_READS_VERIFIED",
      tool_count: names.length,
      exact_tool_discovery: true,
      backend_health: health.status,
      governance_can_execute: governance.can_execute,
      terminal_authority: health.mcp_gateway.terminal_authority,
      protected_operation_blocker: protectedPayload.gateway_status,
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
